"""Trace corridor_polyline on the Kimbolton case."""
from __future__ import annotations
from shapely.geometry import Point
from shapely.strtree import STRtree
from nzcl import crossings
import replay_cards as rc

x, y = 1808992.9, 5548507.8
sources, unmerged, _ = rc.build_sources(x, y, 1500.0)
from shapely.geometry import LineString
geoms = [LineString(s.coords) for s in sources]
eps, owner = [], []
for i, s in enumerate(sources):
    eps.append(Point(s.coords[0])); owner.append(i)
    eps.append(Point(s.coords[-1])); owner.append(i)
tree = STRtree(eps)
found = crossings.detect(geoms, [s.amds_id for s in sources], end_guard_m=0.05)
p = Point(x, y)
for c in found:
    if Point(c.x, c.y).distance(p) > 1.0:
        continue
    print(f"crossing {c.amds_a} x {c.amds_b} angle={c.angle_deg:.2f}")
    for tag, idx, along in (("A", c.index_a, c.along_a), ("B", c.index_b, c.along_b)):
        line = geoms[idx]
        ext, ealong = crossings.corridor_polyline(idx, along, geoms, tree, owner)
        print(f"  {tag}: feature len {line.length:.1f} along {along:.1f}"
              f"  -> corridor len {ext.length:.1f} along {ealong:.1f}")
    ea, aa = crossings.corridor_polyline(c.index_a, c.along_a, geoms, tree, owner)
    eb, ab = crossings.corridor_polyline(c.index_b, c.along_b, geoms, tree, owner)
    print(f"  dup(features)={crossings.is_duplicate_corridor(geoms[c.index_a], c.along_a, geoms[c.index_b], c.along_b)}")
    print(f"  dup(corridors)={crossings.is_duplicate_corridor(ea, aa, eb, ab)}")
    for tag, line, along, other in (("A", ea, aa, eb), ("B", eb, ab, ea)):
        for sign in (-1.0, 1.0):
            far = along + sign * 60.0
            if far < 0 or far > line.length:
                print(f"    {tag}{'+' if sign>0 else '-'}: too short "
                      f"({far:.1f} of {line.length:.1f})")
                continue
            ds = [other.distance(line.interpolate(along + sign*60.0*f))
                  for f in (0.25, 0.5, 0.75, 1.0)]
            print(f"    {tag}{'+' if sign>0 else '-'}: " + " ".join(f"{d:6.2f}" for d in ds))
