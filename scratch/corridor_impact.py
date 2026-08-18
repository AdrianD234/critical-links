"""How much of the national record does `corridor_polyline` move, and where?

DUPLICATE_GEOMETRY fires on 9,830 of 22,062 national crossing points under the
shipping code path. The old link-level record had it at 712 of 13,056. Two
changes happened at once - the record moved from graph links to source
features, and `corridor_polyline` was added - and only one of them is a
classifier change. Attributing the difference to the wrong one would either
hide an over-firing rule or condemn a correct one.

So this runs the SAME source-feature pipeline twice, with the corridor walk on
and off, and reports what moved. Over-firing here is not a cosmetic problem:
DUPLICATE_GEOMETRY is a NEVER_NODE reason, so every crossing it takes is a
junction the canonical graph will not have - which is the Greendale defect
running in reverse.
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

from shapely import wkb
from shapely.geometry import LineString, Point
from shapely.ops import linemerge
from shapely.strtree import STRtree

from nzcl import crossings, topology
import classify_national_v2 as base


def run(with_corridor: bool, sources, geoms, tree, owner, structures):
    attrs = [s.attrs for s in sources]
    motorway_tree, ramp_tree = topology._context_trees(sources, geoms)
    structure_tree = STRtree([g for g, _ in structures])
    structure_kinds = [k for _, k in structures]

    original = crossings.corridor_polyline
    if not with_corridor:
        crossings.corridor_polyline = (
            lambda i, along, g, t, o, **kw: (g[i], along))
    try:
        found = crossings.detect(geoms, [s.amds_id for s in sources],
                                 end_guard_m=0.05)
        for x in found:
            x.classification = crossings.classify(crossings.build_context(
                x, geoms, attrs, endpoint_tree=tree, endpoint_owner=owner,
                motorway_tree=motorway_tree, ramp_tree=ramp_tree,
                structure_tree=structure_tree,
                structure_kinds=structure_kinds))
        crossings.demote_mixed_places(found)
    finally:
        crossings.corridor_polyline = original
    return found


def main(argv):
    sources, unmerged, meta = base.load_sources()
    structures = base.load_structures()
    geoms = [LineString(s.coords) for s in sources]
    endpoints, owner = [], []
    for i, s in enumerate(sources):
        endpoints.append(Point(s.coords[0]))
        owner.append(i)
        endpoints.append(Point(s.coords[-1]))
        owner.append(i)
    tree = STRtree(endpoints)

    print("without the corridor walk")
    off = run(False, sources, geoms, tree, owner, structures)
    print("with the corridor walk")
    on = run(True, sources, geoms, tree, owner, structures)

    def tally(found):
        return collections.Counter(
            (x.disposition, x.classification.reason) for x in found)

    a, b = tally(off), tally(on)
    print()
    print(f"{'rule':<40} {'off':>7} {'on':>7} {'delta':>7}")
    for k in sorted(set(a) | set(b), key=lambda k: -(b[k] - a[k])):
        print(f"{k[0]+'/'+k[1]:<40} {a[k]:>7} {b[k]:>7} {b[k]-a[k]:>+7}")

    key = lambda x: (round(x.x, 3), round(x.y, 3), x.amds_a, x.amds_b)
    off_by = {key(x): x for x in off}
    moved = collections.Counter()
    examples: dict[str, list] = collections.defaultdict(list)
    for x in on:
        y = off_by.get(key(x))
        if y is None:
            continue
        before = f"{y.disposition}/{y.classification.reason}"
        after = f"{x.disposition}/{x.classification.reason}"
        if before != after:
            moved[f"{before} -> {after}"] += 1
            if len(examples[f"{before} -> {after}"]) < 6:
                examples[f"{before} -> {after}"].append(
                    {"x": round(x.x, 1), "y": round(x.y, 1),
                     "a": x.amds_a, "b": x.amds_b,
                     "angle": round(x.angle_deg, 1)})

    print()
    print("=== what the corridor walk moved ===")
    for k, n in moved.most_common():
        print(f"  {k:<62} {n:>6}")

    at_grade_lost = sum(n for k, n in moved.items() if k.startswith("AT_GRADE"))
    print()
    print(f"AT_GRADE crossings withdrawn by the corridor walk: {at_grade_lost}")
    print(f"AT_GRADE total, walk off: {sum(v for k, v in a.items() if k[0]=='AT_GRADE')}")
    print(f"AT_GRADE total, walk on:  {sum(v for k, v in b.items() if k[0]=='AT_GRADE')}")

    out = Path(argv[1]) if len(argv) > 1 else None
    if out:
        out.mkdir(parents=True, exist_ok=True)
        (out / "corridor-impact.json").write_text(json.dumps({
            "byRuleWalkOff": {f"{d}/{r}": n for (d, r), n in a.items()},
            "byRuleWalkOn": {f"{d}/{r}": n for (d, r), n in b.items()},
            "transitions": dict(moved),
            "examples": {k: v for k, v in examples.items()},
        }, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {out/'corridor-impact.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
