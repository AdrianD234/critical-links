"""Test fixtures.

Synthetic networks are loaded into a real PostGIS database under a
test-only snapshot id, so the known-answer tests exercise the actual pgRouting
path rather than a stand-in. Each fixture cleans up after itself.

Tests are skipped (not failed) when no database is reachable, so a checkout
without a provisioned database still runs the pure-Python suite.
"""

from __future__ import annotations

import uuid
from typing import Callable, Iterable

import pytest

from nzcl import db
from nzcl.geo import polyline_length
from nzcl.topology import SourceLink, assign_nodes, split_at_junctions


def _database_available() -> bool:
    try:
        db.query_one("SELECT 1 AS ok")
        return True
    except Exception:  # noqa: BLE001
        return False


DB_AVAILABLE = _database_available()
requires_db = pytest.mark.skipif(
    not DB_AVAILABLE, reason="no PostgreSQL/PostGIS database reachable"
)


@pytest.fixture(scope="session", autouse=True)
def _migrated():
    if DB_AVAILABLE:
        db.migrate()


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


def _load_synthetic(spec: Iterable[dict], restrictions: Iterable[dict] = ()) -> SyntheticNetwork:
    snapshot_id = f"test-{uuid.uuid4().hex[:12]}"
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
            cur.execute("SELECT build_arc_transitions(%s)", (snapshot_id,))
        conn.commit()

    # Proper weak components (the SQL above is a placeholder that would lump
    # everything together; correctness here matters for DISCONNECTED results).
    _label_components(snapshot_id, pairs, len(node_coords))
    return SyntheticNetwork(snapshot_id, split.links, pairs, node_coords, by_id)


def _label_components(snapshot_id: str, pairs, node_count: int) -> None:
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


@pytest.fixture
def synthetic() -> Callable[..., SyntheticNetwork]:
    created: list[str] = []

    def build(spec, restrictions=()) -> SyntheticNetwork:
        net = _load_synthetic(spec, restrictions)
        created.append(net.snapshot_id)
        return net

    yield build

    for snap in created:
        try:
            db.execute("DELETE FROM network_snapshots WHERE snapshot_id=%s", (snap,))
        except Exception:  # noqa: BLE001
            pass
