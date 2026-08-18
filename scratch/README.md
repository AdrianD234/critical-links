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
