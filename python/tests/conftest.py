"""Test fixtures.

Synthetic networks are loaded into a real PostGIS database under a
test-only snapshot id, so the known-answer tests exercise the actual pgRouting
path rather than a stand-in. Each fixture cleans up after itself.

Tests are skipped (not failed) when no database is reachable, so a checkout
without a provisioned database still runs the pure-Python suite.

The loader itself now lives in `nzcl.fixtures`, because CI needs it too: it
builds the snapshot the browser suite queries. Keeping one implementation means
the network those tests run against is assembled exactly the way the
known-answer tests assemble theirs — a second copy would have drifted.
"""

from __future__ import annotations

from typing import Callable

import pytest

from nzcl import db
from nzcl.fixtures import SyntheticNetwork, load_synthetic


def _database_available() -> bool:
    try:
        db.query_one("SELECT 1 AS ok")
        return True
    except Exception:  # noqa: BLE001
        return False


DB_AVAILABLE = _database_available()
requires_db = pytest.mark.skipif(
    not DB_AVAILABLE, reason="no PostgreSQL/PostGIS database reachable"
)


@pytest.fixture(scope="session", autouse=True)
def _migrated():
    if DB_AVAILABLE:
        db.migrate()


@pytest.fixture
def synthetic() -> Callable[..., SyntheticNetwork]:
    created: list[str] = []

    def build(spec, restrictions=()) -> SyntheticNetwork:
        net = load_synthetic(spec, restrictions)
        created.append(net.snapshot_id)
        return net

    yield build

    for snap in created:
        try:
            db.execute("DELETE FROM network_snapshots WHERE snapshot_id=%s", (snap,))
        except Exception:  # noqa: BLE001
            pass
