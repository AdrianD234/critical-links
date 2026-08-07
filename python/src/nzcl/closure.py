"""What a closure actually removes, stated explicitly.

V1 had one closure rule and no name for it. Selecting a road removed every
graph link derived from the same AMDS source feature, which on a long rural
highway means removing seventeen kilometres to model the closure of five. The
reported Tokoroa case (docs/audits/detour-v2/reported-tokoroa-case) is that
failure end to end: a correct number attached to a question nobody asked.

So V2 makes the scope a first-class parameter with three named values, and the
default is the one the interface's own language implies.

    segment         Both directed arcs of the exact post-noding graph link.
                    "This precise segment is unavailable." THE DEFAULT.

    direction       One directed traversal only. A contraflow, a lane closure
                    that leaves the other direction running.

    source_feature  Every graph child of one AMDS source feature. V1's
                    behaviour, kept because it is occasionally what someone
                    wants, demoted to Advanced because it usually is not.

`source_feature` is NOT "the physical road" and must never be described as one.
An AMDS source feature is a data-maintenance unit. It may be a few metres or
seventeen kilometres, it may stop where a road-controlling authority's
responsibility stops rather than where the road does, and the split into graph
children happens at inferred junctions. Calling it the road invites the reader
to picture something the data does not describe.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Literal, Sequence

from . import db
from .routing import Profile

Scope = Literal["segment", "direction", "source_feature"]
Direction = Literal["forward", "reverse"]

#: The V2 default. Stated once, here.
DEFAULT_SCOPE: Scope = "segment"

_MODE_COLUMN = {"car": "mode_vehicle", "heavy": "mode_vehicle_heavy",
                "emergency": "mode_emergency"}


@dataclass
class Closure:
    """A resolved closure: exactly what will be removed, and how it is shaped."""

    snapshot_id: str
    selected_link_id: int
    selected_amds_id: str
    closure_group_id: str
    scope: Scope
    #: Only meaningful for scope='direction'.
    direction: Direction | None
    vehicle_profile: Profile

    removed_link_ids: list[int]
    removed_arc_ids: list[int]
    removed_amds_ids: list[str]

    selected_segment_length_m: float
    total_closure_length_m: float

    #: Nodes where the closure meets links that stay open. These are the points
    #: a replacement path has to reach, and the reason a closure of one link is
    #: a different object from a closure of seventeen.
    boundary_nodes: list[int]
    #: All nodes touched by the closure, boundary or interior.
    closure_nodes: list[int]

    #: simple_chain | cycle | branching | disjoint | single_segment
    shape: str
    shape_detail: str

    fingerprint: str

    #: Present only when the scope removes more than the selected segment.
    warning: dict | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def removed_link_count(self) -> int:
        return len(self.removed_link_ids)

    @property
    def excess_length_m(self) -> float:
        return self.total_closure_length_m - self.selected_segment_length_m


def _link_row(snapshot_id: str, link_id: int) -> dict:
    row = db.query_one(
        "SELECT link_id, amds_id, closure_group_id, source_node, target_node, "
        "       length_m, forward_allowed, reverse_allowed, road_name, rca_name "
        "  FROM links WHERE snapshot_id=%s AND link_id=%s",
        (snapshot_id, link_id),
    )
    if row is None:
        raise KeyError(f"unknown link {link_id} in snapshot {snapshot_id}")
    return row


def resolve(snapshot_id: str, link_id: int, *, scope: Scope = DEFAULT_SCOPE,
            direction: Direction | None = None,
            profile: Profile = "car") -> Closure:
    """Work out exactly what `scope` removes, and describe it.

    Nothing here is approximate and nothing depends on row order: link ids and
    arc ids are sorted before they are reported or hashed, so the same request
    produces byte-identical output on any database that holds the same rows.
    """
    link = _link_row(snapshot_id, link_id)

    if scope == "source_feature":
        rows = db.query(
            "SELECT link_id, amds_id, length_m, source_node, target_node "
            "  FROM links WHERE snapshot_id=%s AND closure_group_id=%s "
            " ORDER BY link_id",
            (snapshot_id, link["closure_group_id"]),
        )
    else:
        rows = [link]

    removed_link_ids = sorted(int(r["link_id"]) for r in rows)

    if scope == "direction":
        if direction not in ("forward", "reverse"):
            raise ValueError(
                "scope='direction' requires direction='forward' or 'reverse'")
        arc_rows = db.query(
            "SELECT arc_id FROM arcs WHERE snapshot_id=%s AND link_id=%s "
            "  AND direction=%s ORDER BY arc_id",
            (snapshot_id, link_id, direction),
        )
    else:
        arc_rows = db.query(
            "SELECT arc_id FROM arcs WHERE snapshot_id=%s "
            "  AND link_id = ANY(%s) ORDER BY arc_id",
            (snapshot_id, removed_link_ids),
        )
    removed_arc_ids = sorted(int(r["arc_id"]) for r in arc_rows)

    removed_amds_ids = [
        r["amds_id"] for r in db.query(
            "SELECT amds_id FROM links WHERE snapshot_id=%s AND link_id = ANY(%s) "
            " ORDER BY link_id", (snapshot_id, removed_link_ids))
    ]

    total_length = float(sum(float(r["length_m"]) for r in rows))
    shape, shape_detail, closure_nodes, boundary = _describe(
        snapshot_id, rows, removed_link_ids, profile)

    warning = None
    if scope == "source_feature" and len(removed_link_ids) > 1:
        warning = {
            "code": "SOURCE_FEATURE_SCOPE_EXCEEDS_SELECTION",
            "severity": "warning",
            "headline": (
                f"This closes {total_length / 1000:.2f} km across "
                f"{len(removed_link_ids)} graph segments derived from one AMDS "
                f"source record."
            ),
            "detail": (
                f"You selected a {float(link['length_m']) / 1000:.2f} km segment. "
                f"This scope removes every graph segment split from AMDS source "
                f"feature {link['closure_group_id']}, which is "
                f"{total_length / 1000:.2f} km in total - "
                f"{(total_length - float(link['length_m'])) / 1000:.2f} km more "
                f"than the segment you selected. An AMDS source feature is a "
                f"data-maintenance unit, not a physical road: it may end where "
                f"an authority's responsibility ends rather than where the road "
                f"does. Results below describe the larger closure."
            ),
            "selectedSegmentLengthM": round(float(link["length_m"]), 1),
            "totalClosureLengthM": round(total_length, 1),
            "removedLinkCount": len(removed_link_ids),
        }

    return Closure(
        snapshot_id=snapshot_id,
        selected_link_id=link_id,
        selected_amds_id=link["amds_id"],
        closure_group_id=link["closure_group_id"],
        scope=scope,
        direction=direction if scope == "direction" else None,
        vehicle_profile=profile,
        removed_link_ids=removed_link_ids,
        removed_arc_ids=removed_arc_ids,
        removed_amds_ids=removed_amds_ids,
        selected_segment_length_m=float(link["length_m"]),
        total_closure_length_m=total_length,
        boundary_nodes=boundary,
        closure_nodes=closure_nodes,
        shape=shape,
        shape_detail=shape_detail,
        fingerprint=fingerprint(snapshot_id, scope, direction, profile,
                                removed_arc_ids),
        warning=warning,
    )


def _describe(snapshot_id: str, rows: Sequence[dict], removed_link_ids: list[int],
              profile: Profile) -> tuple[str, str, list[int], list[int]]:
    """Shape of the closure subgraph, and where it meets the open network.

    Shape is reported because it changes what the closure means. A simple chain
    is a stretch of one road. A branching closure is a junction being removed,
    which is a materially larger claim about the network and should not be
    presented in the same sentence.
    """
    deg: dict[int, int] = {}
    for r in rows:
        for n in (int(r["source_node"]), int(r["target_node"])):
            deg[n] = deg.get(n, 0) + 1
    nodes = sorted(deg)

    # Connectivity of the closure subgraph itself.
    adj: dict[int, list[int]] = {n: [] for n in nodes}
    for r in rows:
        s, t = int(r["source_node"]), int(r["target_node"])
        adj[s].append(t)
        adj[t].append(s)
    seen = {nodes[0]} if nodes else set()
    stack = list(seen)
    while stack:
        u = stack.pop()
        for w in adj[u]:
            if w not in seen:
                seen.add(w)
                stack.append(w)
    connected = len(seen) == len(nodes)

    ends = [n for n in nodes if deg[n] == 1]
    if len(rows) == 1:
        shape, detail = "single_segment", "one graph link, two endpoints"
    elif not connected:
        shape, detail = ("disjoint",
                         "the closure is not a single connected stretch of road")
    elif any(d > 2 for d in deg.values()):
        shape, detail = ("branching",
                         "the closure includes a junction: at least one node "
                         "has three or more closed links on it")
    elif not ends:
        shape, detail = "cycle", "the closure forms a closed loop"
    elif len(ends) == 2:
        shape, detail = ("simple_chain",
                         "an unbranched stretch of road between two endpoints")
    else:
        shape, detail = "disjoint", "the closure has an unexpected degree profile"

    # Boundary nodes: closure nodes that still carry an open link.
    mode = _MODE_COLUMN[profile]
    boundary_rows = db.query(
        f"""
        SELECT DISTINCT n.node_id
          FROM unnest(%s::bigint[]) AS n(node_id)
          JOIN links l
            ON l.snapshot_id = %s
           AND (l.source_node = n.node_id OR l.target_node = n.node_id)
           AND l.{mode}
           AND NOT (l.link_id = ANY(%s))
         ORDER BY n.node_id
        """,
        (nodes or [-1], snapshot_id, removed_link_ids),
    )
    boundary = [int(r["node_id"]) for r in boundary_rows]
    return shape, detail, nodes, boundary


def fingerprint(snapshot_id: str, scope: str, direction: str | None,
                profile: str, removed_arc_ids: Sequence[int]) -> str:
    """A deterministic identity for one closure computation.

    Keyed on the removed ARCS rather than on the link the user clicked: two
    requests that remove the same arcs under the same profile are the same
    computation however they were addressed, and should share a cache entry.
    The snapshot is included because an arc id means nothing without it.
    """
    payload = "|".join((
        "closure-impact-v2",
        snapshot_id,
        scope,
        direction or "-",
        profile,
        ",".join(str(int(a)) for a in sorted(removed_arc_ids)),
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def as_dict(c: Closure) -> dict:
    """API shape. Every field the brief requires a closure to carry."""
    return {
        "selectedLinkId": c.selected_link_id,
        "selectedAmdsId": c.selected_amds_id,
        "closureGroupId": c.closure_group_id,
        "scope": c.scope,
        "direction": c.direction,
        "removedLinkIds": c.removed_link_ids,
        "removedArcIds": c.removed_arc_ids,
        "removedAmdsIds": c.removed_amds_ids,
        "removedLinkCount": c.removed_link_count,
        "removedArcCount": len(c.removed_arc_ids),
        "selectedSegmentLengthM": round(c.selected_segment_length_m, 1),
        "totalClosureLengthM": round(c.total_closure_length_m, 1),
        "excessLengthM": round(c.excess_length_m, 1),
        "boundaryNodes": c.boundary_nodes,
        "closureNodes": c.closure_nodes,
        "shape": c.shape,
        "shapeDetail": c.shape_detail,
        "fingerprint": c.fingerprint,
        "warning": c.warning,
    }
