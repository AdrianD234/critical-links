"""Boundary ports: where a closure meets the network that is still open.

WHY THIS EXISTS
---------------
V1 and PR 1 both measure a detour between the closed segment's OWN two
endpoints. That is the defect, and the V1 semantics audit measured how large it
is: two-way LOCAL roads return DISCONNECTED 44.16% of the time, two-way state
highways 44.26%, and 96.6% of state-highway "cut off" results name a link that
is not an undirected bridge.

The tempting reading is that one-way carriageways are the problem, because
their DISCONNECTED rate is higher still (87.90%). That reading is wrong and the
audit says so: one-way geometry AGGRAVATES a broken measure, it is not the
cause. A two-way road with no one-way anywhere near it fails just as often,
because asking "can I still get from this segment's start node to its end node"
is not the question a reader is asking. They are asking whether a trip THROUGH
here still works, and a trip through here enters and leaves the closure
somewhere - at a port.

So a port is the unit of measurement this PR is built on:

    endpoint measure   u -> v, where u and v are the closed segment's own nodes
    port measure       entry port -> exit port, where those are the places a
                       vehicle actually crosses the closure boundary

For a simple two-way segment the two reduce to each other, which is the
compatibility property the fixtures pin. For a branching source-feature closure
they do not, and reducing such a closure to "the parent's endpoints" is how a
seventeen-child chain came to be measured between two arbitrary nodes.

WHAT A PORT IS
--------------
An arc that CROSSES the closure boundary, plus everything needed to identify it
again tomorrow. Entry ports carry traffic in; exit ports carry it out. Both are
directed, because the arc they come from is.

Nothing here computes a route. This module answers "where does the closure meet
the open network, and in which direction" and stops. Movement identification is
`movements.py`; routing is the replacement-path engine.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Literal, Sequence

from . import db, stableid
from .routing import Profile

#: Bump when the port DERIVATION changes shape - a different boundary rule, a
#: different notion of compatibility. Port ids embed it, so a cached port from
#: an older rule can never be mistaken for a current one.
PORT_MODEL_VERSION = "1.0.0"

_MODE_COLUMN = {"car": "mode_vehicle", "heavy": "mode_vehicle_heavy",
                "emergency": "mode_emergency"}

PortKind = Literal["entry", "exit"]


@dataclass(frozen=True)
class Port:
    """One directed crossing of the closure boundary.

    `outside_node` is on the open network, `closure_node` is on the closure.
    For an ENTRY the arc runs outside -> closure; for an EXIT, closure ->
    outside. That asymmetry is the whole content of the type: a one-way street
    into a closure is an entry and never an exit, and treating the pair as
    interchangeable is how a one-way system produces a nonsense movement.
    """

    port_id: str
    kind: PortKind
    outside_node: int
    closure_node: int
    #: The arc that crosses the boundary, and the link it belongs to.
    arc_id: int
    link_id: int
    direction: str
    #: Metres along the closure's own geometry from the SELECTED segment to
    #: this port's closure-side node. Zero when the port sits on the selected
    #: segment itself. Used by the corridor rule to prefer near ports, so that
    #: "the detour starts here" means somewhere a driver would recognise.
    #:
    #: NONE when this port sits on a piece of the closure the selected segment
    #: cannot reach ALONG THE CLOSURE - a disjoint source feature. There is no
    #: along-closure distance to report, and reporting zero says the opposite
    #: of the truth.
    #:
    #: This was `offsets.get(inside, 0.0)`. The Dijkstra that builds `offsets`
    #: is seeded only from the selected segment's own two nodes, so every node
    #: in another closure component fell through that default. A source feature
    #: with two pieces 50 km apart reported all four of the far piece's ports
    #: as 0.0 m from the selection - and they sorted FIRST, ahead of the ports
    #: on the segment the user actually clicked, taking the candidate allowance
    #: and changing which movements were evaluated.
    distance_from_selected_m: float | None
    #: Which connected piece of the CLOSURE this port is on. Components are
    #: numbered by their smallest stable node key, so the numbering survives a
    #: re-ingest. 0 is not special; `in_selected_component` is the flag that
    #: matters.
    closure_component_id: int = 0
    #: True when this port is on the same piece of the closure as the selected
    #: segment. Only these have a finite `distance_from_selected_m`.
    in_selected_component: bool = True
    #: Continuity evidence, carried rather than collapsed to a score, so the
    #: interface can say WHY a port was preferred.
    road_name: str | None = None
    route_designation: str | None = None
    is_state_highway: bool = False
    road_class: int | None = None
    #: Profile compatibility. A port a heavy vehicle cannot use is not a port
    #: for a heavy vehicle, and silently including it would offer a replacement
    #: route that profile may not drive.
    profiles: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)
    #: Identity built from what the PUBLISHER chose - the AMDS feature id, the
    #: traversal direction, and the two node positions - rather than from
    #: `arc_id`, which the noding pass hands out in ingest order.
    #:
    #: `port_id` above is unchanged and stays the identity the API reports.
    #: This is what ORDERING and TIE-BREAKS use, because a tie broken on a hash
    #: of `arc_id` is only stable until the next ingest re-numbers the graph.
    #: See `stableid.py` for the shuffled-input evidence.
    stable_key: str = ""

    @property
    def is_entry(self) -> bool:
        return self.kind == "entry"


def port_id(snapshot_id: str, kind: str, arc_id: int, closure_fingerprint: str
            ) -> str:
    """Deterministic identity for a port.

    Includes the closure fingerprint because the same arc is a boundary
    crossing only RELATIVE to a closure: arc 42 is a port of this closure and
    an ordinary arc of the next one. Includes the model version so a port
    derived under a different boundary rule cannot collide with a current one.

    Stable across runs, machines and row orders - it is a hash of identifiers,
    not of anything the database chose to return first.
    """
    payload = "|".join((
        "port", PORT_MODEL_VERSION, snapshot_id, closure_fingerprint,
        kind, str(int(arc_id)),
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


@dataclass
class ClosureBoundary:
    """The full port picture for one closure."""

    snapshot_id: str
    closure_fingerprint: str
    selected_link_id: int
    profile: Profile

    #: Nodes touched by the closure, split by whether the open network still
    #: reaches them. An INTERIOR node is one every incident link of which is
    #: closed: nothing outside can reach it, so it can never be a port.
    closure_nodes: list[int] = field(default_factory=list)
    interior_nodes: list[int] = field(default_factory=list)
    boundary_nodes: list[int] = field(default_factory=list)

    entry_ports: list[Port] = field(default_factory=list)
    exit_ports: list[Port] = field(default_factory=list)

    #: simple_chain | cycle | branching | disjoint | single_segment, from the
    #: closure itself. Reported here too because it decides whether reducing to
    #: two endpoints is even meaningful.
    shape: str = ""
    #: True when every port sits on one of the SELECTED SEGMENT's own two
    #: endpoints, and no closure node is interior. That is the case where the
    #: port measure and the endpoint measure ask the same question, and PR 2
    #: must not change the answer on it.
    #:
    #: NOT "one entry and one exit". A two-way segment in a square has a port
    #: PAIR at each endpoint - four ports in total - and still reduces
    #: perfectly. Counting ports instead of locating them would have declared
    #: the simplest case in the network irreducible.
    reduces_to_endpoints: bool = False
    #: Which piece of the closure the selected segment is on, and how many
    #: pieces there are. More than one means a disjoint closure: the pieces
    #: have no along-closure distance between them, and movements are
    #: identified within each piece separately rather than across all of them.
    selected_component_id: int = 0
    closure_component_count: int = 1
    detail: str = ""

    @property
    def is_disjoint(self) -> bool:
        return self.closure_component_count > 1

    @property
    def ports(self) -> list[Port]:
        return self.entry_ports + self.exit_ports


def derive(snapshot_id: str, removed_link_ids: Sequence[int],
           selected_link_id: int, closure_fingerprint: str, *,
           profile: Profile = "car", shape: str = "") -> ClosureBoundary:
    """Find every directed crossing of this closure's boundary.

    One query, then partitioning in Python. The query asks for arcs with
    exactly one endpoint among the closure's nodes and which are not themselves
    closed - that is the definition of a boundary crossing, expressed directly
    rather than approximated by walking outward.

    Ordering is by arc id throughout, so the port list is a function of the
    data and not of the plan the database chose.
    """
    removed = sorted(int(x) for x in removed_link_ids)
    if not removed:
        return ClosureBoundary(
            snapshot_id=snapshot_id, closure_fingerprint=closure_fingerprint,
            selected_link_id=selected_link_id, profile=profile, shape=shape,
            detail="empty closure: nothing is removed, so it has no boundary")

    mode = _MODE_COLUMN[profile]

    node_rows = db.query(
        "SELECT DISTINCT n.node_id FROM ("
        "  SELECT source_node AS node_id FROM links "
        "   WHERE snapshot_id=%s AND link_id = ANY(%s) "
        "  UNION "
        "  SELECT target_node FROM links "
        "   WHERE snapshot_id=%s AND link_id = ANY(%s)) n "
        " ORDER BY n.node_id",
        (snapshot_id, removed, snapshot_id, removed))
    closure_nodes = [int(r["node_id"]) for r in node_rows]

    # Arcs crossing the boundary: one end on a closure node, one end off it,
    # and not part of the closure.
    arc_rows = db.query(
        f"""
        SELECT a.arc_id, a.link_id, a.source, a.target, a.direction,
               a.mode_vehicle, a.mode_vehicle_heavy, a.mode_emergency,
               l.rca_code, l.model_asset_type,
               coalesce(dn.display_name, l.road_name) AS road_name,
               dn.route_designation
          FROM arcs a
          JOIN links l
            ON l.snapshot_id = a.snapshot_id AND l.link_id = a.link_id
     LEFT JOIN link_display_names dn
            ON dn.snapshot_id = l.snapshot_id AND dn.link_id = l.link_id
         WHERE a.snapshot_id = %s
           AND NOT (a.link_id = ANY(%s))
           AND a.{mode}
           AND ( (a.source = ANY(%s) AND NOT (a.target = ANY(%s)))
              OR (a.target = ANY(%s) AND NOT (a.source = ANY(%s))) )
         ORDER BY a.arc_id
        """,
        (snapshot_id, removed, closure_nodes, closure_nodes,
         closure_nodes, closure_nodes))

    offsets, component_of, selected_component = _closure_shape(
        snapshot_id, removed, selected_link_id, closure_nodes)

    # Publisher-assigned keys for every arc and node the ports will touch, in
    # two batched lookups rather than one per port.
    arc_key = stableid.arc_keys(snapshot_id, [int(r["arc_id"]) for r in arc_rows])
    node_key = stableid.node_keys(
        snapshot_id,
        [int(r["source"]) for r in arc_rows] + [int(r["target"]) for r in arc_rows])

    entries: list[Port] = []
    exits: list[Port] = []
    touched: set[int] = set()

    for r in arc_rows:
        src, tgt = int(r["source"]), int(r["target"])
        inbound = tgt in set(closure_nodes) and src not in set(closure_nodes)
        kind: PortKind = "entry" if inbound else "exit"
        outside = src if inbound else tgt
        inside = tgt if inbound else src
        touched.add(inside)

        profiles = tuple(
            name for name, col in (("car", "mode_vehicle"),
                                   ("heavy", "mode_vehicle_heavy"),
                                   ("emergency", "mode_emergency"))
            if r[col])

        p = Port(
            port_id=port_id(snapshot_id, kind, int(r["arc_id"]),
                            closure_fingerprint),
            kind=kind, outside_node=outside, closure_node=inside,
            arc_id=int(r["arc_id"]), link_id=int(r["link_id"]),
            direction=r["direction"],
            # `.get(inside)` with NO default: a node on another piece of the
            # closure has no along-closure distance, and None says so.
            distance_from_selected_m=offsets.get(inside),
            closure_component_id=component_of.get(inside, -1),
            in_selected_component=(component_of.get(inside, -1)
                                   == selected_component),
            road_name=r.get("road_name"),
            route_designation=r.get("route_designation"),
            is_state_highway=(r.get("rca_code") == 1),
            road_class=r.get("model_asset_type"),
            profiles=profiles,
            stable_key=stableid.port_key(
                arc_key.get(int(r["arc_id"]), str(r["arc_id"])),
                node_key.get(outside, str(outside)),
                node_key.get(inside, str(inside))),
        )
        (entries if inbound else exits).append(p)

    # Ordered: the selected segment's own piece of the closure first, nearest
    # first within it, then the other pieces by component id. The stable key
    # breaks every remaining tie on the AMDS feature id and the node positions.
    #
    # Two things this ordering has to survive. It used to tie-break on
    # `port_id`, a hash of `arc_id`, which the next ingest reassigns. And it
    # used to sort ports from a disjoint piece of the closure to the FRONT,
    # because their missing distance defaulted to 0.0. Ordering here decides
    # which ports survive the candidate bound, so it must be stable against
    # re-ingest and must never rank a 50 km-away piece as adjacent.
    entries.sort(key=_port_order)
    exits.sort(key=_port_order)

    boundary_nodes = sorted(touched)
    interior = [n for n in closure_nodes if n not in touched]

    sel = db.query_one(
        "SELECT source_node, target_node FROM links "
        " WHERE snapshot_id=%s AND link_id=%s", (snapshot_id, selected_link_id))
    endpoint_pair = {int(sel["source_node"]), int(sel["target_node"])} if sel else set()
    port_nodes = {p.closure_node for p in entries + exits}
    reduces = bool(endpoint_pair) and not interior and port_nodes <= endpoint_pair

    return ClosureBoundary(
        snapshot_id=snapshot_id, closure_fingerprint=closure_fingerprint,
        selected_link_id=selected_link_id, profile=profile,
        closure_nodes=closure_nodes, interior_nodes=interior,
        boundary_nodes=boundary_nodes,
        entry_ports=entries, exit_ports=exits, shape=shape,
        reduces_to_endpoints=reduces,
        selected_component_id=selected_component,
        closure_component_count=len(set(component_of.values())),
        detail=(
            f"{len(entries)} entry and {len(exits)} exit port(s) on "
            f"{len(boundary_nodes)} boundary node(s); "
            f"{len(interior)} interior node(s) are unreachable from outside"
            + (f"; the closure is in {len(set(component_of.values()))} "
               f"disconnected piece(s) and the selected segment is on one of "
               f"them, so ports on the others have no along-closure distance"
               if len(set(component_of.values())) > 1 else "")
        ),
    )


def _port_order(p: Port):
    """Ordering key for ports. Never depends on a value that could be absent.

    A port with no along-closure distance sorts AFTER every port that has one,
    rather than sorting as though it were zero.
    """
    return (
        0 if p.in_selected_component else 1,
        p.closure_component_id,
        float("inf") if p.distance_from_selected_m is None
        else p.distance_from_selected_m,
        p.stable_key,
    )


def _closure_shape(snapshot_id: str, removed_link_ids: list[int],
                   selected_link_id: int, closure_nodes: Sequence[int]
                   ) -> tuple[dict[int, float], dict[int, int], int]:
    """Along-closure distance, closure-component label, and which one is ours.

    Distance is metres, not hops. A hop count would treat a 2 m stub and a 5 km
    leg as equally far, which is exactly wrong on the Tokoroa parent where the
    children run 1.99 m to 5,201 m. The walk stays inside the closure subgraph,
    so "outward distance" means distance a driver would travel inside the
    closed stretch, not straight-line proximity.

    Distance is returned ONLY for nodes in the selected segment's own piece of
    the closure. A closure can be disjoint - a source feature split across a
    gap - and there is no along-closure distance between its pieces. The caller
    reports `None` for the rest rather than a default, because the default was
    zero and zero is the most misleading answer available.

    Components are numbered by their smallest STABLE node key, so the numbering
    is the same after a re-ingest renumbers the graph.
    """
    rows = db.query(
        "SELECT link_id, source_node, target_node, length_m FROM links "
        " WHERE snapshot_id=%s AND link_id = ANY(%s) ORDER BY link_id",
        (snapshot_id, sorted(removed_link_ids)))
    adj: dict[int, list[tuple[int, float]]] = {}
    for n in closure_nodes:
        adj.setdefault(int(n), [])
    for r in rows:
        s, t, w = int(r["source_node"]), int(r["target_node"]), float(r["length_m"])
        adj.setdefault(s, []).append((t, w))
        adj.setdefault(t, []).append((s, w))

    # --- connected pieces of the closure --------------------------------
    raw: dict[int, int] = {}
    for start in sorted(adj):
        if start in raw:
            continue
        label = start
        stack = [start]
        raw[start] = label
        while stack:
            u = stack.pop()
            for v, _w in adj.get(u, ()):
                if v not in raw:
                    raw[v] = label
                    stack.append(v)

    # Renumber by smallest stable node key so the ids survive a re-ingest.
    node_key = stableid.node_keys(snapshot_id, list(adj))
    smallest: dict[int, str] = {}
    for node, label in raw.items():
        key = node_key.get(node, str(node))
        if label not in smallest or key < smallest[label]:
            smallest[label] = key
    order = sorted(smallest, key=lambda lbl: smallest[lbl])
    renumber = {lbl: i for i, lbl in enumerate(order)}
    component_of = {node: renumber[label] for node, label in raw.items()}

    sel = db.query_one(
        "SELECT source_node, target_node FROM links "
        " WHERE snapshot_id=%s AND link_id=%s", (snapshot_id, selected_link_id))
    if sel is None:
        return {}, component_of, -1
    su, sv = int(sel["source_node"]), int(sel["target_node"])
    selected_component = component_of.get(su, component_of.get(sv, -1))

    import heapq
    dist: dict[int, float] = {}
    heap = [(0.0, su), (0.0, sv)]
    while heap:
        d, u = heapq.heappop(heap)
        if u in dist and dist[u] <= d:
            continue
        dist[u] = d
        for v, w in adj.get(u, ()):
            nd = d + w
            if v not in dist or nd < dist[v]:
                heapq.heappush(heap, (nd, v))

    # Only the selected piece gets a distance. Everything else is absent, and
    # the caller turns absence into None rather than into zero.
    offsets = {n: dist[n] for n in dist
               if component_of.get(n) == selected_component}
    return offsets, component_of, selected_component


def as_dict(b: ClosureBoundary) -> dict:
    """API shape."""

    def port(p: Port) -> dict:
        return {
            "portId": p.port_id,
            "kind": p.kind,
            "outsideNode": p.outside_node,
            "closureNode": p.closure_node,
            "arcId": p.arc_id,
            "linkId": p.link_id,
            "direction": p.direction,
            # None, never 0.0, when the port is on another piece of the
            # closure. The field docstring records what 0.0 cost.
            "distanceFromSelectedM": (
                None if p.distance_from_selected_m is None
                else round(p.distance_from_selected_m, 1)),
            "closureComponentId": p.closure_component_id,
            "inSelectedComponent": p.in_selected_component,
            "roadName": p.road_name,
            "routeDesignation": p.route_designation,
            "isStateHighway": p.is_state_highway,
            "roadClass": p.road_class,
            "profiles": list(p.profiles),
            "notes": list(p.notes),
            "stableKey": p.stable_key,
        }

    return {
        "portModelVersion": PORT_MODEL_VERSION,
        "closureFingerprint": b.closure_fingerprint,
        "selectedLinkId": b.selected_link_id,
        "vehicleProfile": b.profile,
        "shape": b.shape,
        "closureNodes": b.closure_nodes,
        "interiorNodes": b.interior_nodes,
        "boundaryNodes": b.boundary_nodes,
        "entryPorts": [port(p) for p in b.entry_ports],
        "exitPorts": [port(p) for p in b.exit_ports],
        "entryPortCount": len(b.entry_ports),
        "exitPortCount": len(b.exit_ports),
        "reducesToEndpoints": b.reduces_to_endpoints,
        "selectedComponentId": b.selected_component_id,
        "closureComponentCount": b.closure_component_count,
        "closureIsDisjoint": b.is_disjoint,
        "detail": b.detail,
    }
