"""WHICH crossings the corridor walk withdrew from AT_GRADE, not just how many.

`corridor_impact.py` reports the counts and six examples per transition.
The third holdout predeclares a stratum for these cases - "withdrawn only by
the corridor walk", 85 crossings nationally - and a stratum cannot be drawn
from a count. This writes the full list.

It runs the source-feature pipeline ONCE, with `corridor_polyline` stubbed
out, and diffs the result against `classified-v2.jsonl`, which is the same
pipeline with the walk ON. One pass rather than two, because the ON pass is
already on disk and re-deriving it would only give it a chance to disagree
with the record every other artefact here is drawn from.

Nothing is classified differently as a result of this script: the stub is
installed and removed inside `run()`, exactly as `corridor_impact.py` does it,
and the record it compares against is not rewritten.

    cd python && PYTHONPATH=src:../scratch python ../scratch/corridor_withdrawn.py \
        ../docs/audits/at-grade-crossings
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

from nzcl import crossings, topology
import classify_national_v2 as base


def classify_without_corridor_walk(sources, geoms, tree, owner, structures):
    attrs = [s.attrs for s in sources]
    motorway_tree, ramp_tree = topology._context_trees(sources, geoms)
    structure_tree = STRtree([g for g, _ in structures])
    structure_kinds = [k for _, k in structures]

    original = crossings.corridor_polyline
    crossings.corridor_polyline = (lambda i, along, g, t, o, **kw: (g[i], along))
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


def main(argv: list[str]) -> int:
    outdir = Path(argv[1])
    record = outdir / "classified-v2.jsonl"
    if not record.exists():
        raise SystemExit(
            f"{record} is not present. It is derived and gitignored; "
            f"regenerate it with the command in classified-v2-manifest.json.")
    # The key ignores which side is A and which is B.
    #
    # `classify_national_v2.load_sources` reads the links table with no ORDER
    # BY, so the order of `sources` - and therefore which feature of a pair is
    # index_a - is whatever the database hands back that day. Nothing about
    # the classification depends on it, but an ORDERED key does: matching on
    # (x, y, groupA, groupB) lost 567 of 22,062 points here, and every one of
    # them was the same crossing with its two sides swapped. Silently dropping
    # 2.6% of the population would have understated the withdrawn set.
    on = {}
    for line in record.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        on[(round(r["x"], 3), round(r["y"], 3),
            frozenset((r["groupA"], r["groupB"])))] = r
    print(f"{len(on)} crossing points in the record (corridor walk ON)")

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

    print("classifying with the corridor walk OFF")
    off = classify_without_corridor_walk(sources, geoms, tree, owner, structures)
    print(f"  {len(off)} crossing points")

    moved = collections.Counter()
    withdrawn = []
    unmatched = 0
    for x in off:
        k = (round(x.x, 3), round(x.y, 3), frozenset((x.amds_a, x.amds_b)))
        r = on.get(k)
        if r is None:
            unmatched += 1
            continue
        before = f"{x.disposition}/{x.classification.reason}"
        after = f"{r['disposition']}/{r['reason']}"
        if before == after:
            continue
        moved[f"{before} -> {after}"] += 1
        if x.disposition == crossings.AT_GRADE \
                and r["disposition"] != crossings.AT_GRADE:
            withdrawn.append({
                # The RECORD's side order, not this run's: consumers join on
                # classified-v2.jsonl and the two runs do not agree about
                # which side is A. See the note on the key above.
                "groupA": r["groupA"], "groupB": r["groupB"],
                "x": r["x"], "y": r["y"],
                "wasReason": x.classification.reason,
                "nowDisposition": r["disposition"], "nowReason": r["reason"],
                "angleDeg": r["angleDeg"],
            })

    print(f"\n{unmatched} crossing points had no counterpart in the record")
    print("\n=== what the corridor walk moved ===")
    for k, n in moved.most_common():
        print(f"  {k:<62} {n:>6}")
    print(f"\nAT_GRADE crossings withdrawn by the corridor walk: {len(withdrawn)}")

    withdrawn.sort(key=lambda r: (r["y"], r["x"]))
    path = outdir / "corridor-withdrawn.json"
    path.write_text(json.dumps({
        "snapshot": base.SNAP,
        "producedBy": "scratch/corridor_withdrawn.py",
        "what": ("Crossing points classified AT_GRADE with `corridor_polyline` "
                 "stubbed out, and something other than AT_GRADE with it in "
                 "place. These are exactly the junctions the corridor walk "
                 "withdraws, and the third holdout draws a decoy stratum from "
                 "them: if the rule over-fires, the evidence is here."),
        "comparedAgainst": "classified-v2.jsonl",
        "unmatchedCrossingPoints": unmatched,
        "transitions": dict(moved),
        "count": len(withdrawn),
        "crossings": withdrawn,
    }, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
