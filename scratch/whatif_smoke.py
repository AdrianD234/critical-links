"""Shake out whatif.py on the Wellington snapshot before touching anything national."""
from __future__ import annotations

from nzcl import db, whatif

SRC = "amds-wellington-2026-07-27-6ef785ad"
DST = "whatif-smoke-wellington"


def main() -> int:
    print(f"copying {SRC} -> {DST}")
    whatif.copy_snapshot(SRC, DST)
    for t in ("links", "nodes", "arcs"):
        a = db.query_one(f"SELECT count(*) n FROM {t} WHERE snapshot_id=%s", (SRC,))["n"]
        b = db.query_one(f"SELECT count(*) n FROM {t} WHERE snapshot_id=%s", (DST,))["n"]
        print(f"  {t}: src={a} copy={b} {'OK' if a == b else 'MISMATCH'}")

    row = db.query_one(
        """
        SELECT a.link_id AS la, b.link_id AS lb,
               ST_X(ST_Intersection(a.geom_2193,b.geom_2193)) AS x,
               ST_Y(ST_Intersection(a.geom_2193,b.geom_2193)) AS y,
               ST_Length(a.geom_2193) AS len_a, ST_Length(b.geom_2193) AS len_b
          FROM links a JOIN links b
            ON b.snapshot_id=a.snapshot_id AND b.link_id > a.link_id
           AND b.mode_vehicle AND ST_Intersects(a.geom_2193,b.geom_2193)
         WHERE a.snapshot_id=%s AND a.mode_vehicle
           AND NOT (ARRAY[a.source_node,a.target_node] && ARRAY[b.source_node,b.target_node])
           AND ST_GeometryType(ST_Intersection(a.geom_2193,b.geom_2193))='ST_Point'
           AND ST_Length(a.geom_2193) > 100 AND ST_Length(b.geom_2193) > 100
         LIMIT 1
        """, (DST,))
    print(f"  crossing chosen: links {row['la']} x {row['lb']} "
          f"at ({row['x']:.3f}, {row['y']:.3f}) lengths {row['len_a']:.1f}/{row['len_b']:.1f}")

    rep = whatif.node_crossings(DST, [whatif.CrossingEdit(
        link_a=row["la"], link_b=row["lb"], x=row["x"], y=row["y"])])
    print(f"  applied={rep.crossings_applied} skipped={rep.crossings_skipped} "
          f"links_split={rep.links_split} new_links={rep.new_links} "
          f"new_nodes={rep.new_nodes} arcs_rebuilt={rep.arcs_rebuilt}")
    print(f"  pieces: {rep.pieces_of_link}")

    node = list(rep.node_of_crossing.values())[0]
    deg = db.query_one(
        "SELECT count(*) n FROM arcs WHERE snapshot_id=%s AND (source=%s OR target=%s)",
        (DST, node, node))["n"]
    print(f"  new node {node} arc degree = {deg} (expect 8 for two two-way roads)")

    for lid in sorted({row["la"], row["lb"]}):
        tot = db.query_one(
            "SELECT sum(length_m) s, count(*) c FROM links WHERE snapshot_id=%s "
            "AND link_id = ANY(%s)", (DST, rep.pieces_of_link[lid]))
        orig = db.query_one(
            "SELECT length_m s FROM links WHERE snapshot_id=%s AND link_id=%s",
            (SRC, lid))["s"]
        print(f"  link {lid}: {orig:.4f} m -> {tot['c']} pieces totalling "
              f"{tot['s']:.4f} m  (delta {tot['s'] - orig:+.6f} m)")

    gaps = db.query(
        "SELECT l.link_id, ST_Distance(ST_StartPoint(l.geom_2193), ns.geom_2193) AS d0, "
        "       ST_Distance(ST_EndPoint(l.geom_2193), nt.geom_2193) AS d1 "
        "  FROM links l JOIN nodes ns ON ns.snapshot_id=l.snapshot_id AND ns.node_id=l.source_node "
        "  JOIN nodes nt ON nt.snapshot_id=l.snapshot_id AND nt.node_id=l.target_node "
        " WHERE l.snapshot_id=%s AND l.link_id = ANY(%s)",
        (DST, sorted({p for ps in rep.pieces_of_link.values() for p in ps})))
    worst = max((max(g["d0"], g["d1"]) for g in gaps), default=0.0)
    print(f"  worst endpoint-to-node gap among new pieces: {worst:.9f} m")

    trans = db.query_one(
        "SELECT count(*) n FROM arc_transitions WHERE snapshot_id=%s", (DST,))["n"]
    print(f"  arc_transitions rebuilt: {trans}")

    print("dropping copy")
    whatif.drop_snapshot(DST)
    left = db.query_one(
        "SELECT count(*) n FROM links WHERE snapshot_id=%s", (DST,))["n"]
    print(f"  links remaining after drop: {left}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
