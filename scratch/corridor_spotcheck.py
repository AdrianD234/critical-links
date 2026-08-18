"""Are the AT_GRADE crossings the corridor walk withdraws really duplicates?

`is_duplicate_corridor` asks for 60 m of parallel running within 8 m. That is
the threshold, so by construction every case it takes satisfies it - which
proves nothing about whether the threshold is in the right place. A genuine
pair of records of one road runs parallel for as long as the road does; a false
fire is a walk that turned a corner and happened to shadow the other line for
just over the required distance.

So this measures how far past the threshold each case actually goes, on the
crossings the walk MOVED out of AT_GRADE. Reported as a distribution, because
one example proves nothing either way.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

from nzcl import crossings
import classify_national_v2 as base

CORRIDOR_M = crossings.DUPLICATE_CORRIDOR_M


def parallel_run(line_a: LineString, along_a: float,
                 line_b: LineString, along_b: float, step: float = 5.0,
                 cap: float = 2000.0) -> float:
    """How far either side of the crossing the two lines stay within 8 m.

    The best of the four directions, which is the same "any one direction"
    rule `is_duplicate_corridor` applies - measured rather than thresholded.
    """
    best = 0.0
    for line, along, other in ((line_a, along_a, line_b),
                               (line_b, along_b, line_a)):
        for sign in (-1.0, 1.0):
            d = 0.0
            while d + step <= cap:
                t = along + sign * (d + step)
                if t < 0.0 or t > line.length:
                    break
                if other.distance(line.interpolate(t)) > CORRIDOR_M:
                    break
                d += step
            best = max(best, d)
    return best


def main(argv):
    sources, _, _ = base.load_sources()
    geoms = [LineString(s.coords) for s in sources]
    endpoints, owner = [], []
    for i, s in enumerate(sources):
        endpoints.append(Point(s.coords[0]))
        owner.append(i)
        endpoints.append(Point(s.coords[-1]))
        owner.append(i)
    tree = STRtree(endpoints)

    impact = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    by_group: dict[str, list[int]] = {}
    for i, s in enumerate(sources):
        by_group.setdefault(s.amds_id, []).append(i)

    print("re-detecting to locate the moved crossings")
    found = crossings.detect(geoms, [s.amds_id for s in sources],
                             end_guard_m=0.05)
    by_point = {(round(x.x, 1), round(x.y, 1), x.amds_a, x.amds_b): x
                for x in found}

    runs = []
    for label in ("AT_GRADE/ORDINARY_CROSSROADS -> UNRESOLVED/DUPLICATE_GEOMETRY",
                  "AT_GRADE/JUNCTION_WITNESS -> UNRESOLVED/DUPLICATE_GEOMETRY"):
        print()
        print(label)
        for e in impact["examples"].get(label, []):
            x = by_point.get((e["x"], e["y"], e["a"], e["b"]))
            if x is None:
                print(f"   {e['x']},{e['y']}: not re-found")
                continue
            ca, aa = crossings.corridor_polyline(x.index_a, x.along_a, geoms,
                                                 tree, owner)
            cb, ab = crossings.corridor_polyline(x.index_b, x.along_b, geoms,
                                                 tree, owner)
            feat_run = parallel_run(geoms[x.index_a], x.along_a,
                                    geoms[x.index_b], x.along_b)
            corr_run = parallel_run(ca, aa, cb, ab)
            runs.append(corr_run)
            print(f"   ({e['x']},{e['y']}) angle={e['angle']:>5}  "
                  f"feature lengths {geoms[x.index_a].length:7.1f} / "
                  f"{geoms[x.index_b].length:7.1f}  "
                  f"corridor lengths {ca.length:8.1f} / {cb.length:8.1f}")
            print(f"       parallel run within {CORRIDOR_M:.0f} m: "
                  f"{feat_run:6.0f} m on the features, "
                  f"{corr_run:6.0f} m on the corridors "
                  f"(threshold {crossings.DUPLICATE_RUN_M:.0f} m)")

    if runs:
        runs.sort()
        print()
        print(f"parallel run on the corridors, {len(runs)} sampled cases: "
              f"min {runs[0]:.0f} m, median {runs[len(runs)//2]:.0f} m, "
              f"max {runs[-1]:.0f} m")
        print("A case that only just clears 60 m is the one to doubt. A case "
              "running parallel for hundreds of metres is one road recorded "
              "twice and nothing else looks like that.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
