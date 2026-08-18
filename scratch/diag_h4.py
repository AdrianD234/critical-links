"""Does a corridor test WALKED THROUGH connected links catch the three misses?"""
from __future__ import annotations
import math
from shapely import wkb
from shapely.geometry import Point, LineString
from nzcl import crossings, db

SNAP = "amds-national-2026-07-28-5b359d84"
CASES = [("H001", 83156, 83590), ("H040", 278923, 279112), ("H192", 82774, 327541)]

def links_near(x, y, radius=400.0):
    return db.query(
        "SELECT link_id, source_node, target_node, road_name, length_m,"
        "       ST_AsBinary(geom_2193) g FROM links"
        " WHERE snapshot_id=%s AND ST_DWithin(geom_2193,"
        "       ST_SetSRID(ST_MakePoint(%s,%s),2193), %s)", (SNAP, x, y, radius))

def bearing(line, along, w=10.0):
    lo, hi = max(0.0, along - w), min(line.length, along + w)
    p0, p1 = line.interpolate(lo), line.interpolate(hi)
    return math.atan2(p1.y - p0.y, p1.x - p0.x)

def extend(lid, along, pool, want=60.0, tol_deg=45.0):
    """Grow a polyline from `lid` through connected, roughly-collinear links."""
    me = pool[lid]
    line = wkb.loads(bytes(me["g"]))
    coords = list(line.coords)
    # forward (increasing measure) then backward
    for direction in (1, -1):
        need = want - (line.length - along if direction > 0 else along)
        cur = me
        cur_line = line
        # which node is the far end in this direction?
        far_node = cur["target_node"] if direction > 0 else cur["source_node"]
        guard = 0
        while need > 0 and guard < 12:
            guard += 1
            cands = [o for o in pool.values()
                     if o["link_id"] != cur["link_id"]
                     and far_node in (o["source_node"], o["target_node"])]
            if not cands:
                break
            b_cur = bearing(cur_line, cur_line.length if direction > 0 else 0.0)
            best = None
            for o in cands:
                ol = wkb.loads(bytes(o["g"]))
                at_start = (o["source_node"] == far_node)
                b_o = bearing(ol, 0.0 if at_start else ol.length)
                d = abs(math.degrees(b_o - b_cur)) % 360.0
                d = min(d, 360.0 - d)
                if d > tol_deg:
                    continue
                if best is None or d < best[0]:
                    best = (d, o, ol, at_start)
            if best is None:
                break
            _, o, ol, at_start = best
            seg = list(ol.coords) if at_start else list(reversed(ol.coords))
            if direction > 0:
                coords = coords + seg[1:]
            else:
                coords = list(reversed(seg))[:-1] + coords
            need -= ol.length
            cur, cur_line = o, ol
            far_node = o["target_node"] if at_start else o["source_node"]
    return LineString(coords)

for code, la, lb in CASES:
    r = db.query_one("SELECT * FROM scratch_features WHERE link_a=%s AND link_b=%s",
                     (la, lb))
    x, y = float(r["px"]), float(r["py"])
    pool = {int(o["link_id"]): o for o in links_near(x, y)}
    p = Point(x, y)
    ga = wkb.loads(bytes(pool[la]["g"])); gb = wkb.loads(bytes(pool[lb]["g"]))
    ea = extend(la, ga.project(p), pool); eb = extend(lb, gb.project(p), pool)
    print(f"=== {code} ===")
    print(f"  link A {ga.length:7.1f} m -> corridor {ea.length:7.1f} m")
    print(f"  link B {gb.length:7.1f} m -> corridor {eb.length:7.1f} m")
    print(f"  link-only  dup = {crossings.is_duplicate_corridor(ga, ga.project(p), gb, gb.project(p))}")
    print(f"  corridor   dup = {crossings.is_duplicate_corridor(ea, ea.project(p), eb, eb.project(p))}")
    for tag, line, other in (("A", ea, eb), ("B", eb, ea)):
        along = line.project(p)
        for sign in (-1.0, 1.0):
            far = along + sign * 60.0
            if far < 0 or far > line.length:
                print(f"    {tag}{'+' if sign>0 else '-'}: still too short "
                      f"({far:.1f} of {line.length:.1f})")
                continue
            ds = [other.distance(line.interpolate(along + sign*60.0*f))
                  for f in (0.25, 0.5, 0.75, 1.0)]
            print(f"    {tag}{'+' if sign>0 else '-'}: " + " ".join(f"{d:6.2f}" for d in ds))
    print()
