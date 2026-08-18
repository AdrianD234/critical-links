"""Expand the compact verdict string into the shape blind_review.score wants."""
from __future__ import annotations

import json
import sys
from pathlib import Path

LEGEND = {"a": "at_grade", "g": "grade_separated",
          "n": "not_a_junction", "u": "unclear"}


def main() -> int:
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])
    d = json.loads(src.read_text(encoding="utf-8"))
    toks = d["verdicts"].split()
    notes = d.get("notesOnHardCases", {})
    out = []
    for code, v in zip(toks[0::2], toks[1::2]):
        out.append({"code": code, "verdict": LEGEND[v],
                    "note": notes.get(code, "")})
    dst.write_text(json.dumps({"verdicts": out}, indent=2), encoding="utf-8")
    print(f"{len(out)} verdicts -> {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
