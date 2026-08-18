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

    def build(spec, restrictions=(), **kw) -> SyntheticNetwork:
        # `**kw` reaches `crossing_policy`, which is what lets a test build the
        # POSSIBLE graph the provenance module exists to describe.
        net = load_synthetic(spec, restrictions, **kw)
        created.append(net.snapshot_id)
        return net

    yield build

    for snap in created:
        try:
            db.execute("DELETE FROM network_snapshots WHERE snapshot_id=%s", (snap,))
        except Exception:  # noqa: BLE001
            pass


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Fail a run that passed nothing, and name anything that skipped.

    Two ways a green run can be meaningless. A collection filter that matches
    nothing exits 0 with no tests, which reads as success. And a deterministic
    `pytest.skip` is not a passing test - it is a test that stopped running,
    which is why this repository's pytest config says the mandatory suite must
    never skip.

    Skips are REPORTED rather than failed: `requires_db` legitimately skips the
    whole database-backed suite on a checkout with no PostGIS, and CI's Python
    job runs exactly that way on purpose. Printing them keeps that visible
    instead of letting a silent 100-skip run pass for a full one.
    """
    import os

    passed = len(terminalreporter.stats.get("passed", []))
    skipped = terminalreporter.stats.get("skipped", [])

    if skipped:
        terminalreporter.write_sep(
            "-", f"{len(skipped)} skipped - each one is a test that did not run")

    # On a runner that HAS a database there is no legitimate skip, so the job
    # that provides PostGIS sets this and turns the report into a gate. Without
    # it a database-backed job could quietly degrade to the no-database one and
    # still go green - which is exactly how 110 tests came to never run on CI.
    if skipped and os.environ.get("NZCL_REQUIRE_NO_SKIPS") == "1":
        terminalreporter.write_sep(
            "=", f"NZCL_REQUIRE_NO_SKIPS=1 and {len(skipped)} test(s) skipped",
            red=True)
        for report in skipped:
            terminalreporter.write_line(f"  skipped: {report.nodeid}")
        session = getattr(terminalreporter, "_session", None)
        if session is not None:
            session.exitstatus = 1

    if passed == 0 and exitstatus == 0:
        terminalreporter.write_sep(
            "=", "no tests passed; an empty run is not a green run", red=True)
        # 5 is pytest's own "no tests collected" code.
        session = getattr(terminalreporter, "_session", None)
        if session is not None:
            session.exitstatus = 5
