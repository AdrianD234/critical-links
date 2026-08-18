# scratch/

Working scripts for the at-grade crossing investigation. These are the exact
commands behind the figures reported in `docs/audits/`. They are kept in the
branch as evidence, not as product code; anything that earns a permanent place
moves into `python/src/nzcl/`.

## Regenerating the national crossing record

`docs/audits/at-grade-crossings/classified.jsonl` is NOT committed: it is
10.9 MB of derived data, reproducible exactly from the code in this branch. Its
sha256, row count and the exact command are in `classified-manifest.json`, and
`classified-sample.jsonl` holds a deterministic 250-row extract for reading the
shape of a row.

To rebuild it in full, in order:

    psql -f scratch/detect_national.sql
    psql -f scratch/cluster_national.sql
    psql -f scratch/features_national.sql
    psql -f scratch/fix_angle.sql
    python ../scratch/load_topo50_structures.py
    psql -f scratch/structure_evidence.sql
    psql -f scratch/structure_fix.sql
    python ../scratch/classify_national.py ../docs/audits/at-grade-crossings

then check the sha256 in the manifest still matches.

That is the OLD, SQL-built record, kept because two published packs were drawn
from it. The record the classifier actually produces is
`classified-v2.jsonl`, built by `classify_national_v2.py` over AMDS source
features. See `classification-summary-v2.json` and section 5a of the audit for
why the two are not comparable.

    cd python && PYTHONPATH=src python ../scratch/classify_national_v2.py \
        ../docs/audits/at-grade-crossings

Its row order, and which side of each crossing is A, both inherit a read of
the links table with no `ORDER BY`, so a rebuild is not guaranteed to be byte
for byte identical to the sha256 in `classified-v2-manifest.json`. Join
crossings on the UNORDERED pair plus the point, never on `(groupA, groupB)`.

## The third holdout

    cd python && PYTHONPATH=src:../scratch python ../scratch/corridor_withdrawn.py \
        ../docs/audits/at-grade-crossings
    cd python && PYTHONPATH=src python ../scratch/holdout3_review.py plan
    cd python && PYTHONPATH=src python ../scratch/holdout3_review.py build ../scratch/holdout3

`corridor_withdrawn.py` first: it writes the 85 crossings the corridor walk
withdrew from AT_GRADE, and the pack has a predeclared stratum drawn from
them. `plan` draws without rendering. `build` draws, fetches ~3,900 LINZ tiles
into the gitignored `scratch/_tiles/`, and renders 440 self-contained JPEG
cards into `scratch/holdout3/cards/` — about 50 MB, gitignored, with every
file's sha256 in `holdout3-cards-manifest.json`.

The reviewer is given `scratch/holdout3/cards/` and nothing else. The answer
key is `docs/audits/at-grade-crossings/holdout3-answer-key.json` and must not
reach them.

Scoring, once verdicts exist, runs the gate in `nzcl/promotion.py`:

    cd python && PYTHONPATH=src python ../scratch/holdout3_review.py score \
        ../scratch/holdout3 <verdicts>

Seal the pack BEFORE the reviewer receives anything, and push the checkpoint:

    cd python && PYTHONPATH=src python ../scratch/holdout3_review.py seal ../scratch/holdout3

`seal` re-verifies independence against every prior pack, checks every card's
bytes against the manifest, refuses to seal if any card has an unresolved tile
failure, and writes `holdout3-seal.json` — card SHAs, classifier SHAs,
generator SHAs, record SHAs, and the answer key as a SHA ONLY, so the
checkpoint can be published without disclosing a disposition.

Scoring refuses to score an incomplete review. The completeness rules live in
`nzcl/holdout.py` and are pinned by `tests/test_holdout.py`; a missing verdict
must never leave the denominator.

    cd python && PYTHONPATH=src python ../scratch/holdout3_review.py score \
        ../scratch/holdout3 <verdicts>                      # exits 2 if short
    cd python && PYTHONPATH=src python ../scratch/holdout3_review.py score \
        ../scratch/holdout3 <verdicts> --materialise-missing  # each as `unclear`
