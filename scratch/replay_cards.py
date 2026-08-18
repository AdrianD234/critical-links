"""Replay the SHIPPED splitter on the real neighbourhood of a crossing card.

Rebuilds AMDS SOURCE FEATURES from the links table (the pieces of one
closure_group_id merged back together), hands them to
`topology.split_at_junctions` exactly as the ingest does, and reports what the
shipping code actually decides at the card's coordinate.

This exists because the national audit record in `scratch_features` was built
in SQL over the LINKS table - i.e. after splitting - with a different angle
window and one row per pair rather than one per intersection point. That record
is what the holdout was drawn and scored from. Whether the classifier that
ships agrees with it is a separate question, and this answers it.
"""
from __future__ import annotations
import sys
from shapely import wkb
from shapely.geometry import LineString, Point
from shapely.ops import linemerge
from nzcl import crossings, db, topology

SNAP = "amds-national-2026-07-28-5b359d84"


def build_sources(x: float, y: float, radius: float):
    rows = db.query(
        "SELECT l.link_id, l.amds_id, l.closure_group_id, l.road_name, l.rca_code,"
        "       l.model_asset_type, l.oneway, l.quality_flags,"
        "       COALESCE(ln.is_ramp, false) AS is_ramp,"
        "       ST_AsBinary(l.geom_2193) g"
        "  FROM links l"
        "  LEFT JOIN link_names ln ON ln.snapshot_id=l.snapshot_id"
        "                         AND ln.closure_group_id=l.closure_group_id"
        " WHERE l.snapshot_id=%s AND l.mode_vehicle"
        "   AND ST_DWithin(l.geom_2193, ST_SetSRID(ST_MakePoint(%s,%s),2193), %s)",
        (SNAP, x, y, radius))
    by_group: dict[str, list] = {}
    for r in rows:
        by_group.setdefault(r["closure_group_id"], []).append(r)
    sources, unmerged = [], 0
    for grp, rs in by_group.items():
        geoms = [wkb.loads(bytes(r["g"])) for r in rs]
        merged = linemerge(geoms) if len(geoms) > 1 else geoms[0]
        parts = [merged] if merged.geom_type == "LineString" else list(merged.geoms)
        if len(parts) > 1:
            unmerged += 1
        r0 = rs[0]
        for part in parts:
            sources.append(topology.SourceLink(
                amds_id=grp, coords=[(c[0], c[1]) for c in part.coords],
                attrs={"road_name": r0["road_name"], "rca_code": r0["rca_code"],
                       "model_asset_type": r0["model_asset_type"],
                       "oneway": r0["oneway"], "is_ramp": bool(r0["is_ramp"]),
                       "quality_flags": list(r0["quality_flags"] or [])}))
    return sources, unmerged, by_group


def structures_near(x, y, radius):
    rows = db.query(
        "SELECT kind, ST_AsBinary(geom_2193) g FROM ext_structures"
        " WHERE ST_DWithin(geom_2193, ST_SetSRID(ST_MakePoint(%s,%s),2193), %s)",
        (x, y, radius))
    return [(wkb.loads(bytes(r["g"])), r["kind"]) for r in rows]


def replay(code, x, y, group_a, group_b, radius=1500.0):
    sources, unmerged, by_group = build_sources(x, y, radius)
    structs = structures_near(x, y, radius)
    res = topology.split_at_junctions(sources, structures=structs,
                                      crossing_policy="confirmed")
    p = Point(x, y)
    print(f"=== {code} at ({x:.1f},{y:.1f}) ===")
    print(f"  {len(sources)} source features in {radius:.0f} m "
          f"({unmerged} did not merge to one line), {len(structs)} structures")
    print(f"  {len(res.crossings)} crossings found; {res.crossing_cuts} cut; "
          f"{res.mixed_place_demotions} withdrawn as MIXED_PLACE")
    hits = [c for c in res.crossings
            if Point(c.x, c.y).distance(p) <= 30.0
            and {c.amds_a, c.amds_b} == {group_a, group_b}]
    if not hits:
        print("  *** NO crossing between these two features within 30 m ***")
    for c in hits:
        cl = c.classification
        was = c.classification_before_place_rule
        print(f"  ({c.x:.1f},{c.y:.1f}) d={Point(c.x,c.y).distance(p):.1f} m "
              f"angle={c.angle_deg:.2f}")
        print(f"     -> {cl.disposition} / {cl.reason}  "
              f"safe_to_node={cl.safe_to_node}"
              + (f"   (was {was.disposition}/{was.reason})" if was else ""))
    # every crossing at this PLACE, whatever the pair
    place = [c for c in res.crossings if Point(c.x, c.y).distance(p) <= 30.0]
    if len(place) > len(hits):
        print(f"  other crossings within 30 m of the card:")
        for c in place:
            if c in hits:
                continue
            print(f"     ({c.x:.1f},{c.y:.1f}) angle={c.angle_deg:.2f} "
                  f"{c.classification.disposition}/{c.classification.reason}")
    print()
    return res


CARDS = [
    ("H001", 1818104.2, 5544309.7,
     "{f8c74d55-4545-414e-858d-f2fdb89412b4}", "{fd9d590f-1ab4-427a-b256-4de5a17bb1eb}"),
    ("H040", 1264028.8, 5007479.9,
     "{cd4afb4e-0e0e-465b-9bc7-8a817c2d6ade}", "{e33cb64f-f21d-495f-b25e-ffcf6de8aabc}"),
    ("H192", 1808992.9, 5548507.8,
     "{7d966e5b-051a-48aa-8742-ddb253b4bdc8}", "{61c2fcad-60fe-4a9a-bf5b-6897a6345b1d}"),
]

if __name__ == "__main__":
    only = sys.argv[1:] or None
    for c in CARDS:
        if only and c[0] not in only:
            continue
        replay(*c)


def replay_pair(label: str, link_a: int, link_b: int, radius: float = 1500.0):
    """Same replay, addressed by the link pair the audit record names."""
    r = db.query_one("SELECT px, py FROM scratch_features "
                     "WHERE link_a=%s AND link_b=%s", (link_a, link_b))
    ga = db.query_one("SELECT closure_group_id c FROM links "
                      "WHERE snapshot_id=%s AND link_id=%s", (SNAP, link_a))
    gb = db.query_one("SELECT closure_group_id c FROM links "
                      "WHERE snapshot_id=%s AND link_id=%s", (SNAP, link_b))
    return replay(label, float(r["px"]), float(r["py"]), ga["c"], gb["c"], radius)
