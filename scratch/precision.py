"""Compute classifier precision from the manual review, with intervals.

Two readings are reported for every rule, because 'unclear' has to go
somewhere and putting it silently on one side is how precision gets
overstated:

  optimistic  unclear verdicts are excluded from the denominator
  strict      unclear verdicts count as errors
"""
from __future__ import annotations

import collections
import json
import math
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
V = json.loads((REPO / "docs/audits/at-grade-crossings/review-verdicts.json")
               .read_text(encoding="utf-8"))

#: What the reviewer's verdict has to be for the classifier to be RIGHT.
#: GRADE_SEPARATED and UNRESOLVED both leave the crossing disconnected, so
#: 'not_a_junction' counts as correct for both: leaving two carriageways of one
#: road unjoined is the right outcome however it was reached.
ACCEPTS = {
    "AT_GRADE": {"at_grade"},
    "GRADE_SEPARATED": {"grade_separated", "not_a_junction"},
    "UNRESOLVED": {"at_grade", "grade_separated", "not_a_junction"},
}


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def report(rows, label: str) -> None:
    by = collections.defaultdict(list)
    for r in rows:
        by[r["classified"]].append(r)

    print(f"\n{'='*84}\n{label}\n{'='*84}")
    hdr = (f"{'rule':<44} {'n':>3} {'ok':>3} {'?':>2} "
           f"{'strict':>18} {'excl. unclear':>18}")
    print(hdr)
    print("-" * len(hdr))
    for rule in sorted(by):
        disp = rule.split("/")[0]
        rs = by[rule]
        n = len(rs)
        unclear = sum(1 for r in rs if r["verdict"] == "unclear")
        ok = sum(1 for r in rs if r["verdict"] in ACCEPTS[disp])
        lo1, hi1 = wilson(ok, n)
        d2 = n - unclear
        lo2, hi2 = wilson(ok, d2) if d2 else (0.0, 1.0)
        p2 = f"{100.0*ok/d2:5.1f}%" if d2 else "  n/a"
        print(f"{rule:<44} {n:>3} {ok:>3} {unclear:>2} "
              f"{100.0*ok/n:5.1f}% [{100*lo1:4.0f}-{100*hi1:3.0f}] "
              f"{p2} [{100*lo2:4.0f}-{100*hi2:3.0f}]")

    print()
    for disp in ("AT_GRADE", "GRADE_SEPARATED", "UNRESOLVED"):
        rs = [r for r in rows if r["classified"].startswith(disp + "/")]
        if not rs:
            continue
        n = len(rs)
        unclear = sum(1 for r in rs if r["verdict"] == "unclear")
        ok = sum(1 for r in rs if r["verdict"] in ACCEPTS[disp])
        lo, hi = wilson(ok, n)
        d2 = n - unclear
        lo2, hi2 = wilson(ok, d2) if d2 else (0.0, 1.0)
        print(f"  {disp:<18} {ok}/{n} = {100.0*ok/n:5.1f}% "
              f"[{100*lo:.0f}-{100*hi:.0f}]   excluding unclear "
              f"{ok}/{d2} = {100.0*ok/d2 if d2 else 0:5.1f}% "
              f"[{100*lo2:.0f}-{100*hi2:.0f}]")


def main() -> int:
    rows = V["verdicts"]
    print(f"reviewed: {len(rows)}")
    report(rows, "PRECISION BY DECIDING RULE (classifier v1, as reviewed)")

    print(f"\n{'='*84}")
    print("WHAT THE UNRESOLVED CLASS IS ACTUALLY HOLDING")
    print("=" * 84)
    for rule in sorted({r["classified"] for r in rows
                        if r["classified"].startswith("UNRESOLVED/")}):
        rs = [r for r in rows if r["classified"] == rule]
        c = collections.Counter(r["verdict"] for r in rs)
        print(f"  {rule:<44} " +
              "  ".join(f"{k}={v}" for k, v in sorted(c.items())))

    print(f"\n{'='*84}")
    print("EVERY CASE THE CLASSIFIER GOT WRONG")
    print("=" * 84)
    for r in rows:
        disp = r["classified"].split("/")[0]
        if r["verdict"] not in ACCEPTS[disp] and r["verdict"] != "unclear":
            print(f"  {r['classified']:<44} {r['linkA']} x {r['linkB']}")
            print(f"      reviewed as {r['verdict']}: {r.get('note','')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
