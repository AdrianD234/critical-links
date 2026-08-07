"""Regressions from the V2 review: cache identity, exactness, wording.

Separate from test_closure_v2.py, which covers the ten scenarios the engine was
designed against. These cover four defects found by reading the code afterwards
- three of which produced a plausible-looking wrong answer rather than an
error, which is why each one gets a test rather than a fix and a promise.
"""

from __future__ import annotations

import pytest

from nzcl import closure as closure_mod
from nzcl import db, detourv2, physical

from conftest import requires_db
from test_closure_v2 import CUL_DE_SAC, FIVE_CHILDREN, SQUARE, analyse

pytestmark = requires_db


@pytest.fixture(autouse=True)
def _clear_graph_cache():
    physical.clear_cache()
    yield
    physical.clear_cache()


def _nodes_of(net, link_id: int) -> tuple[int, int]:
    row = db.query_one(
        "SELECT source_node, target_node FROM links "
        " WHERE snapshot_id=%s AND link_id=%s", (net.snapshot_id, link_id))
    return int(row["source_node"]), int(row["target_node"])


# --------------------------------------------------------------------------
# cache identity - the P0
# --------------------------------------------------------------------------
class TestSiblingCacheIdentity:
    """Two children of one AMDS parent must not be served each other's answer.

    Under source_feature scope every child removes the SAME arc set, so they
    share a closure fingerprint - correctly, because they do close the same
    roads. Keying the whole RESULT on that fingerprint meant the second sibling
    queried was served the first one's link id, segment length, endpoints and
    replacement metrics.

    The response stayed internally consistent while describing a road the user
    did not click. Nothing looked wrong, which is exactly why it needs a test
    rather than a reviewer.
    """

    @staticmethod
    def _pair(net) -> tuple[int, int]:
        a, b = net.link_id("MAIN#1"), net.link_id("MAIN#3")
        assert a != b
        return a, b

    def test_siblings_do_share_a_closure_fingerprint(self, synthetic):
        """The precondition. If this stops holding, the tests below are vacuous."""
        net = synthetic(FIVE_CHILDREN)
        a, b = self._pair(net)
        ca = closure_mod.resolve(net.snapshot_id, a, scope="source_feature")
        cb = closure_mod.resolve(net.snapshot_id, b, scope="source_feature")
        assert ca.fingerprint == cb.fingerprint
        assert ca.removed_link_ids == cb.removed_link_ids
        # ...and the closure-invariant identity is shared too, which is where
        # sharing is correct and where the expensive work actually is.
        iso_a = closure_mod.isolation_fingerprint(
            net.snapshot_id, "car", physical.DERIVATION_VERSION,
            ca.removed_link_ids)
        iso_b = closure_mod.isolation_fingerprint(
            net.snapshot_id, "car", physical.DERIVATION_VERSION,
            cb.removed_link_ids)
        assert iso_a == iso_b

    @pytest.mark.parametrize("order", [("a", "b"), ("b", "a")])
    def test_each_sibling_keeps_its_own_answer_in_either_order(
            self, synthetic, order):
        net = synthetic(FIVE_CHILDREN)
        a, b = self._pair(net)
        detourv2.invalidate_cache(net.snapshot_id)

        by_key = {"a": a, "b": b}
        seen = {}
        for key in order:
            link = by_key[key]
            r = detourv2.analyse(net.snapshot_id, link, scope="source_feature",
                                 direction="both", use_cache=True)
            seen[key] = r
            assert r.link_id == link, (
                f"asked about link {link}, got an answer about {r.link_id}")
            assert r.closure.selected_link_id == link
            u, v = _nodes_of(net, link)
            assert {r.forward.source_node, r.forward.target_node} == {u, v}

        # The two answers must genuinely differ where they describe the
        # selection, or the assertions above could pass by coincidence.
        ra, rb = seen["a"], seen["b"]
        assert ra.link_id != rb.link_id
        assert (ra.forward.source_node, ra.forward.target_node) != (
            rb.forward.source_node, rb.forward.target_node)

        # ...while the closure-invariant half stays shared: same roads closed,
        # same isolation result.
        assert ra.closure.removed_link_ids == rb.closure.removed_link_ids
        assert ra.isolation.separated_link_ids == rb.isolation.separated_link_ids

    def test_a_warm_cache_still_returns_the_right_sibling(self, synthetic):
        net = synthetic(FIVE_CHILDREN)
        a, b = self._pair(net)
        detourv2.invalidate_cache(net.snapshot_id)

        first = detourv2.analyse(net.snapshot_id, a, scope="source_feature",
                                 use_cache=True)
        assert first.cached is False and first.link_id == a

        again = detourv2.analyse(net.snapshot_id, a, scope="source_feature",
                                 use_cache=True)
        assert again.cached is True and again.link_id == a

        # A is now warm under the shared fingerprint. B must still get B.
        other = detourv2.analyse(net.snapshot_id, b, scope="source_feature",
                                 use_cache=True)
        assert other.link_id == b
        u, v = _nodes_of(net, b)
        assert {other.forward.source_node, other.forward.target_node} == {u, v}


# --------------------------------------------------------------------------
# exactness is two claims
# --------------------------------------------------------------------------
class TestExactnessVocabulary:
    def test_calculation_exact_is_not_graph_exact(self, synthetic):
        net = synthetic(SQUARE)
        r = analyse(net, "S")
        assert r.isolation.calculation_exact is True
        assert r.isolation.partition_exact is True
        # Gu is inferred topology. This must never become True.
        assert r.isolation.graph_exact is False

    def test_topology_confidence_is_never_high(self, synthetic):
        net = synthetic(CUL_DE_SAC)
        r = analyse(net, "MOUTH")
        assert r.isolation.topology_confidence in ("medium", "low")
        assert r.isolation.topology_confidence_reason

    def test_the_isolation_statement_names_the_graph_not_the_world(
            self, synthetic):
        net = synthetic(SQUARE)
        r = analyse(net, "S")
        assert r.isolation_statement == (
            "No isolation in the represented physical-access graph")
        assert r.isolation_statement in detourv2.HEADLINES


# --------------------------------------------------------------------------
# the principal side is a policy, not a theorem
# --------------------------------------------------------------------------
#: Two four-link loops joined by a single link, no state highway anywhere.
#: Removing the join splits the network into two components of identical size.
SYMMETRIC_SPLIT = [
    {"id": "L1", "pts": [(0, 0), (100, 0)]},
    {"id": "L2", "pts": [(100, 0), (100, 100)]},
    {"id": "L3", "pts": [(100, 100), (0, 100)]},
    {"id": "L4", "pts": [(0, 100), (0, 0)]},
    {"id": "BR", "pts": [(100, 0), (300, 0)]},
    {"id": "R1", "pts": [(300, 0), (400, 0)]},
    {"id": "R2", "pts": [(400, 0), (400, 100)]},
    {"id": "R3", "pts": [(400, 100), (300, 100)]},
    {"id": "R4", "pts": [(300, 100), (300, 0)]},
]


class TestPrincipalSideIsPolicy:
    def test_an_unambiguous_split_says_cut_off_and_says_why(self, synthetic):
        net = synthetic(CUL_DE_SAC)
        r = analyse(net, "MOUTH")
        assert r.isolation.physically_isolates is True
        assert r.isolation.principal_side_ambiguous is False
        assert r.isolation.principal_side_confidence == "high"
        assert r.isolation.principal_side_rule
        assert r.headline == "Road cut off"

    def test_a_symmetric_split_refuses_to_name_a_stranded_side(self, synthetic):
        """The graph split. Mathematics does not say which half is cut off.

        A bridge yields two components; without an external anchor nothing
        privileges either. Milford Sound is obvious because a state highway and
        the rest of the South Island sit on one side. Most of the network is
        not like that, and the interface must be able to say so.
        """
        net = synthetic(SYMMETRIC_SPLIT)
        r = analyse(net, "BR")
        assert r.isolation.physically_isolates is True
        assert r.isolation.principal_side_ambiguous is True
        assert r.isolation.principal_side_confidence == "low"
        assert r.headline == "Network split into two represented components"
        assert r.isolation_statement == (
            "Network split into two represented components")
        # The partition itself is still exact. Only the naming is withheld.
        assert r.isolation.partition_exact is True
        assert r.isolation.calculation_exact is True


# --------------------------------------------------------------------------
# a partly unresolved request must say so
# --------------------------------------------------------------------------
def _ok(direction="forward", u=1, v=2):
    return detourv2.DirectionResult(
        direction=direction, status="OK", source_node=u, target_node=v,
        selected_segment_length_m=100.0, alternative_distance_m=300.0)


def _bad(status, direction="reverse", u=2, v=1):
    return detourv2.DirectionResult(
        direction=direction, status=status, source_node=u, target_node=v,
        selected_segment_length_m=100.0)


def _clean_iso():
    return physical.IsolationResult(
        calculation_exact=True, physically_isolates=False,
        method="precomputed-not-a-bridge")


class TestPartialAnalysis:
    """A failure in one direction must not be hidden by success in the other.

    The overall headline previously said "unresolved" only when EVERY requested
    direction failed, so forward=OK with reverse timing out surfaced as
    "Through route found" while half the requested analysis had not happened.
    """

    @pytest.mark.parametrize("bad_status", ["UNRESOLVED_TIMEOUT", "API_ERROR"])
    def test_ok_plus_unresolved_is_partial_not_a_finding(self, bad_status):
        results = {"forward": _ok(), "reverse": _bad(bad_status)}
        access = detourv2._directed_access(results, 1, 2)
        headline, _ = detourv2._classify(results, _clean_iso(), access)
        assert headline == "Partial analysis"
        assert headline in detourv2.HEADLINES
        # Mutual reachability was never established, so it must not be claimed.
        assert access.same_scc_after_closure is None
        # And an unresolved direction is not an asymmetry.
        assert access.asymmetric is False

    def test_both_unresolved_stays_unresolved_and_scc_unknown(self):
        results = {"forward": _bad("UNRESOLVED_TIMEOUT", "forward"),
                   "reverse": _bad("API_ERROR")}
        access = detourv2._directed_access(results, 1, 2)
        headline, statement = detourv2._classify(results, _clean_iso(), access)
        assert headline == "Analysis unresolved"
        assert statement == "Analysis unresolved"
        # Previously False: two timed-out searches were read as proof the
        # endpoints are not mutually reachable, which they are not.
        assert access.same_scc_after_closure is None

    def test_isolation_still_stands_when_a_route_search_fails(self):
        """Isolation is computed on Gu and depends on neither route search.

        A timeout cannot undermine it, so a real separation is still reported
        rather than being softened to "Partial analysis".
        """
        iso = physical.IsolationResult(
            calculation_exact=True, physically_isolates=True,
            method="bridge-subtree-and-subtraction",
            separated_link_ids=[7], separated_link_count=1,
            separated_length_m=100.0)
        results = {"forward": _ok(), "reverse": _bad("UNRESOLVED_TIMEOUT")}
        access = detourv2._directed_access(results, 1, 2)
        headline, _ = detourv2._classify(results, iso, access)
        assert headline == "Road cut off"

    def test_two_conclusive_directions_do_report_mutual_reachability(self):
        results = {"forward": _ok(), "reverse": _ok("reverse", 2, 1)}
        access = detourv2._directed_access(results, 1, 2)
        assert access.same_scc_after_closure is True
        assert access.asymmetric is False
