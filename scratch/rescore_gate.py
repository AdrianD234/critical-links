"""Re-evaluate a recorded holdout against the promotion gate, from its verdicts.

Rewrites the `promotionGate` block in `holdout-result.json` using
`nzcl.promotion.evaluate`, so the recorded verdict is produced by the same code
the tests pin rather than typed alongside the numbers.

The block it replaces encoded ONE of the agreed conditions and reported the
whole gate as met. The counts it recorded were correct; the verdict drawn from
them was not.

Nothing about the review changes: the verdicts, the answer key and every
per-cell figure stay exactly as they were. This reads them and re-derives the
one field that was wrong.

    python ../scratch/rescore_gate.py ../docs/audits/at-grade-crossings
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from nzcl.promotion import evaluate


def main(argv: list[str]) -> int:
    outdir = Path(argv[1]) if len(argv) > 1 else Path(".")
    path = outdir / "holdout-result.json"
    doc = json.loads(path.read_text(encoding="utf-8"))

    ag = doc["byDisposition"]["AT_GRADE"]
    contradictions = [c for c in doc["contradictions"]
                      if c["disposition"] == "AT_GRADE"]
    gs_fp = [c for c in contradictions if c["verdict"] == "grade_separated"]
    nj_fp = [c for c in contradictions if c["verdict"] == "not_a_junction"]
    other = [c for c in contradictions
             if c["verdict"] not in ("grade_separated", "not_a_junction")]
    if other:
        raise SystemExit(
            f"unclassified AT_GRADE contradiction verdicts: "
            f"{sorted({c['verdict'] for c in other})}. The gate splits "
            f"contradictions by what the reviewer said instead, and a verdict "
            f"it does not know about must not be silently dropped.")

    result = evaluate(
        confirmed=ag["confirmed"],
        contradicted=ag["contradicted"],
        unreviewable=ag["unreviewable"],
        grade_separated_false_positives=len(gs_fp),
        not_a_junction_false_positives=len(nj_fp),
    )

    block = result.as_dict()
    block["falsePositiveCodes"] = {
        "gradeSeparated": [c["code"] for c in gs_fp],
        "notAJunction": [c["code"] for c in nj_fp],
    }
    # Kept from the old block, because they are the evidence and only the
    # verdict drawn from them was wrong.
    block["counts"] = {"confirmed": ag["confirmed"],
                       "contradicted": ag["contradicted"],
                       "unreviewable": ag["unreviewable"]}
    block["supersedes"] = (
        "an earlier block that encoded only "
        "confirmedGradeSeparatedFalsePositives and reported met: true. The "
        "agreed gate was always both conditions; the counts were right and the "
        "verdict drawn from them was not.")

    doc["promotionGate"] = block
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")

    print(f"{path}: gate met = {result.met}")
    for c in result.conditions:
        print(f"  [{'PASS' if c.met else 'FAIL'}] {c.name}: {c.observed}")
    print(f"  {result.detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
