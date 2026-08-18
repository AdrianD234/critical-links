"""Replace the 10.9 MB national crossing record with something reviewable.

The full `classified.jsonl` is derived data: it is reproducible EXACTLY from
committed code plus a recorded snapshot id, so carrying it in git buries the
topology implementation under thirteen thousand lines of output. PR #5 set the
precedent here, going from 223 files to 46 by keeping manifests and
regeneration scripts instead of bulk captures.

What is kept instead, and why each part is needed:

  classification-summary.json   the counts anyone will actually quote
  classified-manifest.json      sha256 + row count + the exact command, so the
                                full file can be regenerated and CHECKED rather
                                than trusted
  classified-sample.jsonl       a deterministic 250-row extract, drawn by hash
                                so it is the same 250 rows on any machine, for
                                reading the shape of a row without regenerating
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

SAMPLE_SEED = "at-grade-crossing-record-sample-1"
SAMPLE_N = 250


def main() -> int:
    d = Path(sys.argv[1])
    full = d / "classified.jsonl"
    raw = full.read_bytes()
    rows = [json.loads(l) for l in raw.decode("utf-8").splitlines() if l.strip()]

    manifest = {
        "file": "classified.jsonl",
        "why_not_committed":
            "Derived data, 10.9 MB, reproducible exactly from committed code "
            "plus the snapshot id below. Kept out of git so the topology "
            "change is readable; kept verifiable by the sha256 here.",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "rows": len(rows),
        "snapshot": rows[0].get("snapshot") if rows else None,
        "regenerate": (
            "cd python && PYTHONPATH=src python ../scratch/classify_national.py "
            "../docs/audits/at-grade-crossings"),
        "prerequisites": [
            "scratch/detect_national.sql   (builds scratch_crossings)",
            "scratch/cluster_national.sql  (builds scratch_xpoints)",
            "scratch/features_national.sql (builds scratch_features)",
            "scratch/fix_angle.sql",
            "scratch/load_topo50_structures.py",
            "scratch/structure_evidence.sql",
            "scratch/structure_fix.sql",
        ],
        "note":
            "The row count is CROSSING PAIRS, not places and not intersections. "
            "See classification-summary.json for all three numbers.",
    }
    (d / "classified-manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")

    def rank(r: dict) -> str:
        return hashlib.md5(
            f"{SAMPLE_SEED}|{r['linkA']}|{r['linkB']}".encode()).hexdigest()

    sample = sorted(rows, key=rank)[:SAMPLE_N]
    with (d / "classified-sample.jsonl").open("w", encoding="utf-8") as fh:
        for r in sorted(sample, key=lambda r: (r["linkA"], r["linkB"])):
            fh.write(json.dumps(r, sort_keys=True) + "\n")

    print(f"manifest sha256 {manifest['sha256'][:16]}... over {len(rows)} rows")
    print(f"sample: {len(sample)} rows, seed {SAMPLE_SEED}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
