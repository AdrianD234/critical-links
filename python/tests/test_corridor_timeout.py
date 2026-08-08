"""A corridor search that did not finish must not be reported as a finding.

V1's corridor measure runs one multi-target pgRouting call and reads an absent
pair as "no route". `routing.route_many` used to return a bare `{}` for a search
that completed with nothing AND for a search PostgreSQL cancelled, so
`detour._corridor` could not tell them apart and answered DISCONNECTED - which
the interface presents as the most important finding the tool produces - for a
query that never ran to completion. Reproduced end to end, with timings and
screenshots, in docs/audits/v1-timeout/.

WHERE THESE RUN
---------------
The first class needs no database at all, so it executes in BOTH CI Python jobs
rather than only in the one that provisions PostGIS. That is deliberate. The
timeout contract in test_routing.py was `realdata`-marked for the whole of its
life and therefore ran nowhere, and a stop-condition contract with no mandatory
guard is not guarded. The status mapping is the load-bearing part, and it is
exercisable without a graph.

The rest need PostGIS, and are `requires_db` rather than `realdata`: they run on
every runner that has a database, including CI's browser job, where
NZCL_REQUIRE_NO_SKIPS turns a skip into a failure. Their timeouts are genuine
PostgreSQL cancellations on a graph big enough that a 1 ms budget cannot be met
by two orders of magnitude - not a mocked exception.
"""

from __future__ import annotations

import pytest

from nzcl import detour
from nzcl.detour import compute
from nzcl.routing import route_many

from conftest import requires_db


class _Raising:
    """A `db.connection()` that fails the way the database fails."""

    def __init__(self, message: str) -> None:
        self.message = message

    def __enter__(self):
        raise RuntimeError(self.message)

    def __exit__(self, *exc):
        return False


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *args, **kwargs):
        return None

    def fetchall(self):
        return self.rows


class _Connection:
    """A `db.connection()` that answers with exactly the rows it was given."""

    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def transaction(self):
        return self

    def cursor(self):
        return _Cursor(self.rows)


class TestTheSearchStatusIsCarried:
    """No database needed: the mapping is the contract."""

    #: The message PostgreSQL actually produced, captured from psycopg in
    #: docs/audits/v1-timeout/observed-before.txt.
    CANCELLED = ("canceling statement due to statement timeout\n"
                 'CONTEXT:  SQL function "pgr_dijkstra" statement 1')

    def test_a_cancelled_search_is_unresolved_and_not_an_empty_result(
            self, monkeypatch):
        from nzcl import routing

        monkeypatch.setattr(routing.db, "connection",
                            lambda: _Raising(self.CANCELLED))
        res = route_many("any-snapshot", [1], [2], statement_timeout_ms=1)

        assert res.status == "UNRESOLVED_TIMEOUT"
        assert res.status != "DISCONNECTED"
        assert res.resolved is False
        assert res.costs == {}
        assert "timeout" in (res.detail or "").lower()

    def test_a_failed_search_is_an_api_error_and_not_an_empty_result(
            self, monkeypatch):
        from nzcl import routing

        monkeypatch.setattr(
            routing.db, "connection",
            lambda: _Raising('relation "arcs" does not exist'))
        res = route_many("any-snapshot", [1], [2])

        assert res.status == "API_ERROR"
        assert res.resolved is False
        assert res.costs == {}

    def test_a_completed_search_with_no_pair_is_resolved(self, monkeypatch):
        """The other half of the contract, and the one that stops this being a
        relabelling exercise: an empty result from a search that FINISHED still
        means no route, and must stay readable as one."""
        from nzcl import routing

        monkeypatch.setattr(routing.db, "connection", lambda: _Connection([]))
        res = route_many("any-snapshot", [1], [2])

        assert res.status == "OK"
        assert res.resolved is True
        assert res.costs == {}

    def test_a_completed_search_returns_the_costs_it_found(self, monkeypatch):
        from nzcl import routing

        monkeypatch.setattr(routing.db, "connection", lambda: _Connection(
            [{"start_vid": 1, "end_vid": 2, "cost": 300.0},
             {"start_vid": 1, "end_vid": 3, "cost": 450.5}]))
        res = route_many("any-snapshot", [1], [2, 3])

        assert res.resolved is True
        assert res.costs == {(1, 2): 300.0, (1, 3): 450.5}

    def test_an_empty_request_is_resolved_not_failed(self, monkeypatch):
        """No source or no target is not a database problem, and the caller must
        keep reading it as 'nothing to route'."""
        from nzcl import routing

        def explode():
            raise AssertionError("no query should be issued")

        monkeypatch.setattr(routing.db, "connection", explode)
        assert route_many("any-snapshot", [], [1]).resolved is True
        assert route_many("any-snapshot", [1], []).resolved is True


def _grid(n: int, step: int = 100) -> list[dict]:
    """A plain two-way street grid.

    Ballast. `pgr_dijkstra` runs one search per source vid off a single edge-set
    load, so an all-pairs call over a few hundred arcs takes long enough that a
    1 ms budget is missed by two orders of magnitude on any machine - which is
    what makes a REAL cancellation deterministic rather than a race.
    """
    links = []
    for r in range(n):
        for c in range(n - 1):
            links.append({"id": f"H{r}-{c}",
                          "pts": [(c * step, r * step), ((c + 1) * step, r * step)]})
    for c in range(n):
        for r in range(n - 1):
            links.append({"id": f"V{r}-{c}",
                          "pts": [(c * step, r * step), (c * step, (r + 1) * step)]})
    return links


#: The reproduction network, reduced to what the contract needs.
#:
#:   (0,0) --CONN_W--> (0,-400) ==EB1==> (300,-400) ==EB2==> (600,-400)
#:                                                              |
#:                                                           CONN_E
#:                                                              v
#:                                                          (1100,0) in the grid
#:
#: EB1 and EB2 are one-way, so closing EB2 leaves (300,-400) with no outgoing
#: arc and the ENDPOINT measure is DISCONNECTED - correct, and routine on a
#: one-way carriageway. The THROUGH trip is fine: the grid carries it 259.7 m
#: further. That is what the corridor search is for, and what a swallowed
#: timeout used to report as "no route to target".
BYPASS = _grid(12) + [
    {"id": "CONN_W", "pts": [(0, 0), (0, -400)]},
    {"id": "EB1", "pts": [(0, -400), (300, -400)], "oneway": True},
    {"id": "EB2", "pts": [(300, -400), (600, -400)], "oneway": True},
    {"id": "CONN_E", "pts": [(600, -400), (1100, 0)]},
]

#: 1500.0 m round the grid against 1240.3 m along the bypass. Both are exact:
#: the grid path is 400 + 1100 and the bypass is 300 + 300 + sqrt(500^2 + 400^2).
THROUGH_TRIP_PENALTY_M = 1500.0 - (300.0 + 300.0 + (500.0 ** 2 + 400.0 ** 2) ** 0.5)


def _closure_arcs(snapshot_id: str, link_id: int) -> list[int]:
    from nzcl import db

    return [r["arc_id"] for r in db.query(
        "SELECT arc_id FROM arcs WHERE snapshot_id=%s AND closure_group_id="
        "(SELECT closure_group_id FROM links WHERE snapshot_id=%s AND link_id=%s)",
        (snapshot_id, snapshot_id, link_id))]


@requires_db
class TestARealStatementTimeout:
    def test_a_cancelled_multi_target_search_says_so(self, synthetic):
        """A genuine PostgreSQL cancellation, no patching anywhere.

        The second half is what makes the first half mean something: the SAME
        call, on the SAME graph, resolves with an adequate budget. So the
        1 ms result is about the clock and not about the network.
        """
        net = synthetic(_grid(12))
        nodes = list(range(len(net.node_coords)))

        cancelled = route_many(net.snapshot_id, nodes, nodes,
                               statement_timeout_ms=1)
        assert cancelled.status == "UNRESOLVED_TIMEOUT"
        assert cancelled.status != "DISCONNECTED"
        assert cancelled.resolved is False

        resolved = route_many(net.snapshot_id, nodes, nodes,
                              statement_timeout_ms=60_000)
        assert resolved.resolved is True
        assert len(resolved.costs) > 1_000

    def test_a_cancelled_corridor_search_is_not_reported_as_no_route(
            self, synthetic):
        """The defect itself.

        Under the old implementation this returns DISCONNECTED with the detail
        "search space exhausted with no route to target" - a claim about the
        road network, made about a query the database killed.
        """
        net = synthetic(BYPASS)
        snap = net.snapshot_id
        u, v = net.nodes_of("EB2")
        excluded = _closure_arcs(snap, net.link_id("EB2"))

        cancelled = detour._corridor(snap, u, v, excluded, "distance", "car",
                                     300.0, 1)
        assert cancelled.status == "UNRESOLVED_TIMEOUT"
        assert cancelled.status != "DISCONNECTED"
        assert cancelled.penalty_m is None
        assert cancelled.alternative_distance_m is None
        assert "timeout" in (cancelled.detail or "").lower()

        # The same corridor, given the time, finds the through trip. Without
        # this the assertion above would be satisfied by a network that really
        # has no corridor.
        resolved = detour._corridor(snap, u, v, excluded, "distance", "car",
                                    300.0, 60_000)
        assert resolved.status == "OK"
        assert resolved.penalty_m == pytest.approx(THROUGH_TRIP_PENALTY_M,
                                                   abs=1e-6)

    def test_the_endpoint_measure_is_untouched_by_the_corridor_status(
            self, synthetic):
        """The direction result still reports what it found: the endpoint
        measure resolved and there genuinely is no path. Only the corridor is
        unresolved."""
        net = synthetic(BYPASS)
        f = compute(net.snapshot_id, net.link_id("EB2"),
                    directions=["forward"]).forward
        assert f.status == "DISCONNECTED"
        assert f.corridor is not None
        assert f.corridor.status == "OK"
        assert f.corridor.penalty_m == pytest.approx(THROUGH_TRIP_PENALTY_M,
                                                     abs=1e-6)


@requires_db
class TestAGenuineNoRouteIsStillDisconnected:
    """Not everything negative was relabelled.

    A search that finishes and finds nothing must still say DISCONNECTED, or
    the fix would have traded a false finding for a useless one.
    """

    def test_a_dead_end_spur_still_reports_no_corridor(self, synthetic):
        net = synthetic([
            {"id": "MAIN", "pts": [(0, 0), (100, 0)]},
            {"id": "SPUR", "pts": [(100, 0), (180, 0)]},
        ])
        snap = net.snapshot_id
        u, v = net.nodes_of("SPUR")
        excluded = _closure_arcs(snap, net.link_id("SPUR"))

        c = detour._corridor(snap, u, v, excluded, "distance", "car", 80.0,
                             60_000)
        assert c.status == "DISCONNECTED"
        assert c.penalty_m is None

    def test_a_dead_end_spur_still_reports_disconnected_end_to_end(
            self, synthetic):
        net = synthetic([
            {"id": "MAIN", "pts": [(0, 0), (100, 0)]},
            {"id": "SPUR", "pts": [(100, 0), (180, 0)]},
        ])
        f = compute(net.snapshot_id, net.link_id("SPUR")).forward
        assert f.status == "DISCONNECTED"
        assert f.corridor is not None
        assert f.corridor.status == "DISCONNECTED"

    def test_a_separate_island_still_reports_disconnected(self, synthetic):
        net = synthetic([
            {"id": "MAINLAND", "pts": [(0, 0), (100, 0)]},
            {"id": "ISLAND", "pts": [(9000, 9000), (9100, 9000)]},
        ])
        f = compute(net.snapshot_id, net.link_id("ISLAND")).forward
        assert f.status == "DISCONNECTED"
