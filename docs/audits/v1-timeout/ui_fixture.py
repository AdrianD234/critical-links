"""Build the reproduction network as a servable snapshot, and serve it.

Two commands:

    python ui_fixture.py build     load the network at Wellington coordinates
                                   under snapshot id `v1-timeout-repro`
    python ui_fixture.py serve     run the real API against it, with the
                                   statement-timeout budget squeezed to the
                                   value that lets the endpoint searches finish
                                   and not the multi-target corridor search

The squeeze is the ONLY thing this changes about the running product, and it
changes it the way a loaded database would: the corridor query is cancelled by
PostgreSQL, exactly as captured in `observed-before.txt` step 1. Nothing is
stubbed and no result is fabricated.
"""

from __future__ import annotations

import sys

from nzcl import db
from nzcl.fixtures import WGTN_X, WGTN_Y, load_synthetic

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from reproduce import CORRIDOR_SQUEEZE_MS, spec  # noqa: E402

SNAPSHOT = "v1-timeout-repro"

#: Names, so the screenshot shows a road rather than an identifier.
NAMES = {"EB1": "Bypass Motorway", "EB2": "Bypass Motorway",
         "CONN_W": "West Ramp", "CONN_E": "East Ramp"}


def nz_spec() -> list[dict]:
    out = []
    for link in spec():
        name = NAMES.get(link["id"], "Grid Street")
        out.append({**link,
                    "pts": [(x + WGTN_X, y + WGTN_Y) for x, y in link["pts"]],
                    "road_name": name})
    return out


def build() -> None:
    db.migrate()
    db.execute("DELETE FROM network_snapshots WHERE snapshot_id=%s", (SNAPSHOT,))
    net = load_synthetic(nz_spec(), snapshot_id=SNAPSHOT,
                         coverage_name="V1 timeout reproduction",
                         require_nz=True)
    print(f"built {net.snapshot_id}: {len(net.links)} links")
    print(f"  closed link amds_id: EB2 (link_id {net.link_id('EB2')})")


def serve(squeeze: int | None) -> None:
    import os

    import uvicorn

    from nzcl import detour

    os.environ["SNAPSHOT_ID"] = os.environ.get("NZCL_REPRO_SNAPSHOT", SNAPSHOT)
    if squeeze is not None:
        original = detour.compute

        def squeezed(*args, **kwargs):
            kwargs["statement_timeout_ms"] = squeeze
            return original(*args, **kwargs)

        detour.compute = squeezed
        import nzcl.api as api_mod
        api_mod.compute = squeezed
        print(f"statement_timeout squeezed to {squeeze} ms")

    uvicorn.run("nzcl.api:app", host="0.0.0.0",
                port=int(os.environ.get("API_PORT", "8000")), log_level="warning")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "build":
        build()
    elif cmd == "serve":
        arg = sys.argv[2] if len(sys.argv) > 2 else str(CORRIDOR_SQUEEZE_MS)
        serve(None if arg == "none" else int(arg))
    else:
        print(__doc__)
        raise SystemExit(2)
