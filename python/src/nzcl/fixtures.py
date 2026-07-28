"""
Synthetic networks, loaded into a real PostGIS database.

Lives in the package rather than in the test suite because two callers need it:
the known-answer tests, and CI, which has to build a snapshot for the browser
suite to query. Duplicating a hundred lines of INSERT statements so that CI
could have its own copy would guarantee the two drifted.

A synthetic network goes through the same junction splitting and node
assignment as production data, so a test cannot pass against a graph assembled
by a different route than the one users get.
"""

from __future__ import annotations

import uuid
from typing import Iterable

from . import db
from .topology import SourceLink, assign_nodes, split_at_junctions


class SyntheticNetwork:
    """A tiny hand-built network loaded into PostGIS for one test."""

    def __init__(self, snapshot_id: str, links, pairs, node_coords, by_id):
        self.snapshot_id = snapshot_id
        self.links = links
        self.pairs = pairs
        self.node_coords = node_coords
        self.by_id = by_id

    def link_id(self, amds_id: str) -> int:
        return self.by_id[amds_id]

    def nodes_of(self, amds_id: str) -> tuple[int, int]:
        return self.pairs[self.by_id[amds_id]]


def load_synthetic(
    spec: Iterable[dict],
    restrictions: Iterable[dict] = (),
    snapshot_id: str | None = None,
) -> SyntheticNetwork:
    """
    Load a synthetic network under a fresh snapshot id, or a caller-supplied one.

    The explicit id exists for CI, which needs a stable name to point the API
    at. Renaming afterwards is not an option: `nodes`, `links` and `arcs` all
    carry a foreign key to `network_snapshots`, so an UPDATE on the parent is
    rejected while any child row still references it.
    """
    snapshot_id = snapshot_id or f"test-{uuid.uuid4().hex[:12]}"
    sources = []
    for s in spec:
        sources.append(SourceLink(
            amds_id=s["id"],
            coords=[(float(x), float(y)) for x, y in s["pts"]],
            attrs={
                "oneway": 1 if s.get("oneway") else 2,
                "forward_allowed": True,
                "reverse_allowed": not s.get("oneway", False),
                "mode_vehicle": s.get("mode_vehicle", True),
                "mode_vehicle_heavy": s.get("mode_vehicle_heavy", True),
                "mode_emergency": s.get("mode_emergency", True),
                "speed_kph": s.get("speed_kph", 50.0),
                "speed_source": "estimated_asset_type",
                "road_name": s.get("road_name"),
                "quality_flags": [],
            },
        ))

    # Junction splitting runs for real: the synthetic fixtures are built the
    # same way production data is, so a test cannot pass against a graph
    # assembled by a different route than the one users get.
    split = split_at_junctions(sources)
    pairs, node_coords = assign_nodes(split.links)
    by_id = {l.amds_id: i for i, l in enumerate(split.links)}

    with db.direct_connection(autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO network_snapshots (snapshot_id, source_dataset,
                  retrieved_at_utc, source_url, layer_id, licence, attribution,
                  raw_sha256, processing_version, source_feature_count,
                  downloaded_feature_count, where_clause, status)
                VALUES (%s,'synthetic',now(),'test',1,'test','test','0'::text,
                        'test',0,0,'test','complete')
                """,
                (snapshot_id,),
            )
            for nid, (x, y) in enumerate(node_coords):
                cur.execute(
                    "INSERT INTO nodes (snapshot_id, node_id, geom_2193, geom_4326, "
                    "component_id) VALUES (%s,%s,ST_SetSRID(ST_MakePoint(%s,%s),2193),"
                    "ST_SetSRID(ST_MakePoint(0,0),4326),0)",
                    (snapshot_id, nid, x, y),
                )
            for lid, link in enumerate(split.links):
                a = link.attrs
                wkt = "LINESTRING(" + ",".join(f"{x} {y}" for x, y in link.coords) + ")"
                cur.execute(
                    """
                    INSERT INTO links (snapshot_id, link_id, amds_id,
                      closure_group_id, geom_2193, geom_4326, source_node,
                      target_node, length_m, forward_allowed, reverse_allowed,
                      mode_vehicle, mode_vehicle_heavy, mode_emergency,
                      oneway, speed_kph, speed_source, road_name)
                    VALUES (%s,%s,%s,%s, ST_GeomFromText(%s,2193),
                            ST_SetSRID(ST_GeomFromText(%s),4326),
                            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (snapshot_id, lid, link.amds_id, link.closure_group_id,
                     wkt, wkt, pairs[lid][0], pairs[lid][1], link.length_m,
                     a["forward_allowed"], a["reverse_allowed"],
                     a["mode_vehicle"], a["mode_vehicle_heavy"], a["mode_emergency"],
                     a["oneway"], a["speed_kph"], a["speed_source"],
                     a.get("road_name")),
                )
            arc_id = 0
            for lid, link in enumerate(split.links):
                u, v = pairs[lid]
                if u == v:
                    continue
                a = link.attrs
                speed = a["speed_kph"]
                time_s = link.length_m / (speed * 1000 / 3600) if speed else None
                for direction, s_, t_, ok in (
                    ("forward", u, v, a["forward_allowed"]),
                    ("reverse", v, u, a["reverse_allowed"]),
                ):
                    if not ok:
                        continue
                    cur.execute(
                        "INSERT INTO arcs (snapshot_id, arc_id, link_id, "
                        "closure_group_id, source, target, direction, "
                        "cost_distance_m, cost_time_s, time_cost_valid, "
                        "mode_vehicle, mode_vehicle_heavy, mode_emergency) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (snapshot_id, arc_id, lid, link.closure_group_id, s_, t_,
                         direction, link.length_m, time_s, time_s is not None,
                         a["mode_vehicle"], a["mode_vehicle_heavy"],
                         a["mode_emergency"]),
                    )
                    arc_id += 1

            for rid, r in enumerate(restrictions):
                cur.execute(
                    "INSERT INTO turn_restrictions (snapshot_id, restriction_id, "
                    "link_seq, restricted_vehicle, restricted_heavy, "
                    "restricted_emergency) VALUES (%s,%s,%s,%s,%s,%s)",
                    (snapshot_id, rid, [by_id[x] for x in r["seq"]],
                     r.get("vehicle", True), r.get("heavy", True),
                     r.get("emergency", False)),
                )

            # Component labelling, so the cheap negative test works.
            cur.execute(
                """
                WITH RECURSIVE comp AS (
                  SELECT node_id, node_id AS root FROM nodes WHERE snapshot_id=%(s)s
                )
                UPDATE nodes n SET component_id = c.cid
                FROM (
                  SELECT node_id, dense_rank() OVER (ORDER BY grp) - 1 AS cid FROM (
                    SELECT n.node_id, min(least(a.source, a.target)) OVER () AS grp
                    FROM nodes n LEFT JOIN arcs a
                      ON a.snapshot_id = n.snapshot_id
                     AND (a.source = n.node_id OR a.target = n.node_id)
                    WHERE n.snapshot_id = %(s)s
                  ) q
                ) c
                WHERE n.snapshot_id = %(s)s AND n.node_id = c.node_id
                """,
                {"s": snapshot_id},
            )
            # See the note in ingest.py: without statistics the planner picks a
            # nested loop for the self-join in build_arc_transitions, which is
            # quadratic. Harmless on a fixture this size, done anyway so the two
            # paths do not differ in a way that hides the problem.
            cur.execute("ANALYZE arcs")
            cur.execute("SELECT build_arc_transitions(%s)", (snapshot_id,))
        conn.commit()

    # Proper weak components (the SQL above is a placeholder that would lump
    # everything together; correctness here matters for DISCONNECTED results).
    label_components(snapshot_id, pairs, len(node_coords))
    return SyntheticNetwork(snapshot_id, split.links, pairs, node_coords, by_id)


def label_components(snapshot_id: str, pairs, node_count: int) -> None:
    parent = list(range(node_count))

    def find(v: int) -> int:
        while parent[v] != v:
            parent[v] = parent[parent[v]]
            v = parent[v]
        return v

    for a, b in pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    labels: dict[int, int] = {}
    with db.direct_connection(autocommit=False) as conn:
        with conn.cursor() as cur:
            for n in range(node_count):
                r = find(n)
                if r not in labels:
                    labels[r] = len(labels)
                cur.execute(
                    "UPDATE nodes SET component_id=%s WHERE snapshot_id=%s "
                    "AND node_id=%s", (labels[r], snapshot_id, n))
        conn.commit()


# ---------------------------------------------------------------- CI snapshot

#: Central Wellington in NZTM2000 metres. Real coordinates, so the fixture
#: projects to a plausible place and the browser tests exercise the same
#: tile-addressing arithmetic production does.
WGTN_X, WGTN_Y = 1749100.0, 5428100.0

CI_SNAPSHOT_ID = "ci-fixture-wellington"


def ci_network_spec() -> list[dict]:
    """
    A network small enough to build in seconds and rich enough to exercise the
    Explore screen's real branches.

    Deliberately contains:

      - a two-way road whose closure has a longer alternative, so the ordinary
        result path is covered;
      - a one-way link, so direction normalisation is exercised against a
        genuinely absent reverse rather than a mocked one;
      - a dead-end spur, so DISCONNECTED and isolation are reachable.

    Laid out as a rectangle with a spur:

        A ---- B ---- C
        |             |
        F ------------ D
                      |
                      E   (dead end)
    """
    x, y = WGTN_X, WGTN_Y
    d = 400.0
    A = (x, y + d)
    B = (x + d, y + d)
    C = (x + 2 * d, y + d)
    D = (x + 2 * d, y)
    E = (x + 2 * d, y - d)
    F = (x, y)

    return [
        {"id": "{ci-ab}", "pts": [A, B], "road_name": "Fixture Terrace"},
        {"id": "{ci-bc}", "pts": [B, C], "road_name": "Fixture Terrace"},
        # The long way round, so closing the top edge has a real alternative.
        {"id": "{ci-af}", "pts": [A, F], "road_name": "Harbour Road"},
        {"id": "{ci-fd}", "pts": [F, D], "road_name": "Harbour Road"},
        {"id": "{ci-cd}", "pts": [C, D], "road_name": "Harbour Road"},
        # One-way, so `reverse` is genuinely absent from the response.
        {"id": "{ci-oneway}", "pts": [B, D], "road_name": "One Way Lane",
         "oneway": True},
        # A spur to nowhere: closing its only connection strands it.
        {"id": "{ci-spur}", "pts": [D, E], "road_name": "Dead End Road"},
    ]


def build_ci_snapshot(snapshot_id: str = CI_SNAPSHOT_ID) -> str:
    """
    Build (or rebuild) the snapshot the browser suite queries.

    CI must not depend on the live NZTA feature service: it is not ours to
    depend on, a scheduled outage would fail unrelated pull requests, and a
    change upstream would alter the figures the tests assert against.
    """
    db.migrate()
    # Rebuildable: CI runs this on a fresh database, but a developer may run it
    # repeatedly against a local one. The cascade removes the children.
    db.execute("DELETE FROM network_snapshots WHERE snapshot_id=%s", (snapshot_id,))
    net = load_synthetic(ci_network_spec(), snapshot_id=snapshot_id)
    return net.snapshot_id


def _main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "build-ci-snapshot":
        snap = build_ci_snapshot()
        counts = db.query_one(
            "SELECT (SELECT count(*) FROM links WHERE snapshot_id=%(s)s) AS links,"
            "       (SELECT count(*) FROM arcs  WHERE snapshot_id=%(s)s) AS arcs",
            {"s": snap},
        )
        print(f"built {snap}: {counts['links']} links, {counts['arcs']} arcs")
        return 0
    print("usage: python -m nzcl.fixtures build-ci-snapshot", flush=True)
    return 2


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv))
