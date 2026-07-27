# Baseline audit — product v1

Evidence captured before and after the backend-seam fixes, so the claims in the
handover can be checked rather than taken on trust.

| file | what it shows |
| --- | --- |
| `baseline-commit.txt` | commit the audit started from |
| `health-python-before.json` | FastAPI `/health` before identity fields were added |
| `health-typescript-before.json` | the TypeScript API answering on 8787 at the same moment |
| `tile-python-before.txt` | decoded Python tile: snake_case, client contract FAILS |
| `health-python-after.json` | `/health` with implementation, commit, branch, versions |
| `tile-python-after.txt` | decoded Python tile: camelCase + feature id, contract PASSES |

## Confirmed findings

**Split-brain backend — CONFIRMED.** Ports 8000 and 8787 were both listening.
`.env.example` (what a fresh clone gets) pointed the client at **8787**, the
TypeScript reference API. Local testing had only reached FastAPI because the
working `.env` differed. A new clone would have run against the wrong backend
while appearing to work.

**MVT contract mismatch — CONFIRMED.** The decoded Python tile carried
`link_id`, `state_highway`, `road_name`; the MapLibre client reads `linkId`,
`stateHighway`, `roadName`. Every map click would have resolved
`properties.linkId === undefined` and state-highway styling would have fallen
back silently. The map-style test could not catch this: it validates the style
definition and never decodes a tile.

Both are fixed, and both are now covered by tests that decode real bytes
(`python/tests/test_tiles.py`).

## Proof the application now runs on FastAPI

With the TypeScript API stopped and port 8787 free, from inside the browser:

```json
{
  "apiBaseUsedByClient": "http://localhost:5173",
  "healthFromRelativeUrl": {
    "implementation": "python-fastapi-postgis",
    "commit": "93be413",
    "algorithm": "pgr-dijkstra-arc",
    "snapshot": "amds-wellington-2026-07-27-6ef785ad",
    "tileSchemaVersion": 2
  },
  "tileStatus": 200,
  "tileType": "application/x-protobuf",
  "typescriptApiReachable": false
}
```
