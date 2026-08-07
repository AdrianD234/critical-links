"""Known-answer tests for the V2 closure engine.

Every expected number here is derivable on paper from the fixture geometry, and
the graph is loaded into a real PostGIS database and split at junctions exactly
the way production data is. A fixture that agreed with the engine because both
were built by the same shortcut would prove nothing.

The ten scenarios are the ones that broke V1, or that would hide the breakage:

   1  two-way road with a rectangular alternative
   2  a true bridge - sole access
   3  a cul-de-sac of two links
   4  one AMDS parent split into five children: segment vs source_feature
   5  divided carriageway
   6  a one-way endpoint artefact with NO physical isolation
   7  direction-only closure
   8  unnamed state-highway contextual label
   9  licence-withheld name
  10  conflicting road names

Tests 8-10 exercise `display_label` directly. It is pure - every input is an
argument - so putting a database in front of it would test the query, not the
rule.
"""

from __future__ import annotations

import pytest

from nzcl import closure as closure_mod
from nzcl import detourv2, physical
from nzcl.naming import display_label

from conftest import requires_db

pytestmark = requires_db


@pytest.fixture(autouse=True)
def _clear_graph_cache():
    """Gu is cached per (snapshot, profile) in-process.

    Synthetic snapshots get a fresh id each time so a stale entry cannot be
    returned, but the cache would otherwise grow for the whole session.
    """
    physical.clear_cache()
    yield
    physical.clear_cache()


def analyse(net, name: str, **kw):
    return detourv2.analyse(net.snapshot_id, net.link_id(name), use_cache=False, **kw)


# --------------------------------------------------------------------------
# 1. two-way road with a rectangular alternative
# --------------------------------------------------------------------------
SQUARE = [
    {"id": "S", "pts": [(0, 0), (100, 0)]},
    {"id": "E", "pts": [(100, 0), (100, 100)]},
    {"id": "N", "pts": [(100, 100), (0, 100)]},
    {"id": "W", "pts": [(0, 100), (0, 0)]},
]


class TestRectangularAlternative:
    def test_through_route_found_and_nothing_is_cut_off(self, synthetic):
        net = synthetic(SQUARE)
        r = analyse(net, "S")

        assert r.headline == "Through route found"
        assert r.isolation_statement == (
            "No isolation in the represented physical-access graph")

        assert r.closure.removed_link_ids == [net.link_id("S")]
        assert len(r.closure.removed_arc_ids) == 2
        assert r.closure.total_closure_length_m == pytest.approx(100, abs=1e-6)
        assert r.closure.selected_segment_length_m == pytest.approx(100, abs=1e-6)
        assert r.closure.shape == "single_segment"
        assert sorted(r.closure.boundary_nodes) == sorted(
            [r.forward.source_node, r.forward.target_node])

        assert r.forward.status == "OK"
        assert r.forward.alternative_distance_m == pytest.approx(300, abs=1e-6)
        assert r.forward.network_penalty_m == pytest.approx(200, abs=1e-6)
        assert r.reverse.alternative_distance_m == pytest.approx(300, abs=1e-6)

        assert r.isolation.physically_isolates is False
        assert r.isolation.calculation_exact is True
        assert r.isolation.closure_is_bridge is False
        assert r.isolation.method == "precomputed-not-a-bridge"
        assert r.isolation.separated_link_count == 0
        assert r.isolation.separated_length_m == 0.0

    def test_fingerprint_is_stable_and_scope_specific(self, synthetic):
        net = synthetic(SQUARE)
        a = closure_mod.resolve(net.snapshot_id, net.link_id("S"), scope="segment")
        b = closure_mod.resolve(net.snapshot_id, net.link_id("S"), scope="segment")
        c = closure_mod.resolve(net.snapshot_id, net.link_id("S"),
                                scope="source_feature")
        assert a.fingerprint == b.fingerprint
        # Same arcs removed here, but a different question was asked.
        assert a.fingerprint != c.fingerprint
        assert len(a.fingerprint) == 32


# --------------------------------------------------------------------------
# 2 & 3. sole access, and a cul-de-sac of two links
# --------------------------------------------------------------------------
#   square ... (0,0) --MOUTH-- (-100,0) --TAIL-- (-200,0)
CUL_DE_SAC = SQUARE + [
    {"id": "MOUTH", "pts": [(0, 0), (-100, 0)]},
    {"id": "TAIL", "pts": [(-100, 0), (-200, 0)]},
]


class TestSoleAccess:
    def test_closing_the_mouth_cuts_off_exactly_the_tail(self, synthetic):
        net = synthetic(CUL_DE_SAC)
        r = analyse(net, "MOUTH")

        assert r.headline == "Road cut off"
        assert r.isolation_statement == "Road cut off"
        assert r.isolation.calculation_exact is True
        assert r.isolation.closure_is_bridge is True
        assert r.isolation.method == "bridge-smaller-side-and-subtraction"

        # Exactly the tail. Not the mouth - the closure is not stranded by
        # itself - and not the square.
        assert r.isolation.separated_link_ids == [net.link_id("TAIL")]
        assert r.isolation.separated_link_count == 1
        assert r.isolation.separated_length_m == pytest.approx(100, abs=1e-6)

        # Two sides, and the square keeps the principal connection.
        assert len(r.isolation.components) == 2
        principal = [c for c in r.isolation.components
                     if c.retains_principal_connection]
        assert len(principal) == 1
        assert principal[0].link_count == 4  # the square
        assert principal[0].road_length_m == pytest.approx(400, abs=1e-6)

        assert r.forward.status == "DISCONNECTED"
        assert r.forward.headline == "Road cut off"

    def test_closing_the_tail_cuts_off_nothing_at_all(self, synthetic):
        """The far end of a cul-de-sac strands no road but itself.

        This is the case the wording gate exists for. The tail IS a bridge -
        removing it does split its component - but the only thing on the far
        side is a node with no links. No road loses access, so no reader may be
        told a road was cut off.
        """
        net = synthetic(CUL_DE_SAC)
        r = analyse(net, "TAIL")

        assert r.isolation.closure_is_bridge is True
        assert r.isolation.separated_link_count == 0
        assert r.isolation.physically_isolates is False
        assert r.isolation_statement == (
            "No isolation in the represented physical-access graph")
        assert r.headline != "Road cut off"
        assert r.headline == "No endpoint route"


# --------------------------------------------------------------------------
# 4. one AMDS parent split into five children
# --------------------------------------------------------------------------
#   MAIN runs 0 -> 500 along y=0 and is cut at x=100,200,300,400 by four side
#   roads, so junction splitting yields exactly five 100 m children.
FIVE_CHILDREN = [
    {"id": "MAIN", "pts": [(0, 0), (500, 0)]},
    {"id": "T1", "pts": [(100, -100), (100, 0)]},
    {"id": "T2", "pts": [(200, -100), (200, 0)]},
    {"id": "T3", "pts": [(300, -100), (300, 0)]},
    {"id": "T4", "pts": [(400, -100), (400, 0)]},
    {"id": "BYPASS", "pts": [(100, -100), (200, -100), (300, -100), (400, -100)]},
]


class TestParentSplitIntoFiveChildren:
    def test_segment_scope_closes_one_child_not_the_parent(self, synthetic):
        net = synthetic(FIVE_CHILDREN)
        # The child between x=100 and x=200.
        child = net.link_id("MAIN#1")
        r = detourv2.analyse(net.snapshot_id, child, scope="segment",
                             use_cache=False)

        assert r.closure.removed_link_ids == [child]
        assert r.closure.total_closure_length_m == pytest.approx(100, abs=1e-6)
        assert r.closure.selected_segment_length_m == pytest.approx(100, abs=1e-6)
        assert r.closure.excess_length_m == pytest.approx(0, abs=1e-9)
        # No warning: nothing beyond the selection was closed.
        assert r.closure.warning is None
        assert r.isolation.physically_isolates is False
        assert r.headline == "Through route found"
        # Round the bypass: 100 down, 100 across, 100 up.
        assert r.forward.alternative_distance_m == pytest.approx(300, abs=1e-6)

    def test_source_feature_scope_closes_all_five_and_says_so(self, synthetic):
        net = synthetic(FIVE_CHILDREN)
        child = net.link_id("MAIN#1")
        r = detourv2.analyse(net.snapshot_id, child, scope="source_feature",
                             use_cache=False)

        assert len(r.closure.removed_link_ids) == 5
        assert r.closure.total_closure_length_m == pytest.approx(500, abs=1e-6)
        assert r.closure.selected_segment_length_m == pytest.approx(100, abs=1e-6)
        assert r.closure.excess_length_m == pytest.approx(400, abs=1e-6)
        assert r.closure.shape == "simple_chain"

        w = r.closure.warning
        assert w is not None
        assert w["code"] == "SOURCE_FEATURE_SCOPE_EXCEEDS_SELECTION"
        assert w["removedLinkCount"] == 5
        assert "0.50 km across 5 graph segments" in w["headline"]
        # It must not present an AMDS source feature AS a physical road. It is
        # required to say the opposite, in as many words.
        assert "not a physical road" in w["detail"]

    def test_the_two_scopes_do_not_share_a_cache_entry(self, synthetic):
        net = synthetic(FIVE_CHILDREN)
        child = net.link_id("MAIN#1")
        a = closure_mod.resolve(net.snapshot_id, child, scope="segment")
        b = closure_mod.resolve(net.snapshot_id, child, scope="source_feature")
        assert a.fingerprint != b.fingerprint


# --------------------------------------------------------------------------
# 5 & 6. divided carriageway, and the one-way endpoint artefact
# --------------------------------------------------------------------------
#   NB runs north on x=0, SB runs south on x=100, joined at both ends.
DIVIDED = [
    {"id": "NB", "pts": [(0, 0), (0, 200)], "oneway": True},
    {"id": "SB", "pts": [(100, 200), (100, 0)], "oneway": True},
    {"id": "XN", "pts": [(0, 200), (100, 200)]},
    {"id": "XS", "pts": [(100, 0), (0, 0)]},
]


class TestDividedCarriageway:
    def test_closing_one_carriageway_leaves_the_road_physically_connected(
            self, synthetic):
        net = synthetic(DIVIDED)
        r = analyse(net, "NB")

        assert r.closure.removed_link_ids == [net.link_id("NB")]
        # A one-way link has exactly one arc.
        assert len(r.closure.removed_arc_ids) == 1

        # Gu keeps one undirected edge per link, so the remaining three still
        # form a path. Nothing is separated.
        assert r.isolation.calculation_exact is True
        assert r.isolation.physically_isolates is False
        assert r.isolation.separated_link_count == 0
        assert r.isolation_statement == (
            "No isolation in the represented physical-access graph")


class TestOneWayEndpointArtefact:
    def test_never_says_cut_off_when_nothing_is_cut_off(self, synthetic):
        """The defect this whole PR exists to stop.

        The endpoint pair of a one-way carriageway has no directed path once
        the carriageway is closed, because the only way round is the opposing
        one-way. That is a fact about direction. V1 turned it into an isolation
        headline; here it must not be able to.
        """
        net = synthetic(DIVIDED)
        r = analyse(net, "NB")

        assert r.forward.status == "DISCONNECTED"
        assert r.headline != "Road cut off"
        assert r.forward.headline != "Road cut off"
        assert r.headline in detourv2.HEADLINES
        assert r.headline == "No endpoint route"
        assert r.isolation_statement == (
            "No isolation in the represented physical-access graph")
        # The directed finding is reported, and reported as directed. A one-way
        # link has no reverse traversal, so mutual reachability was never
        # tested and must be reported as unknown rather than as a failure.
        assert r.directed_access.same_scc_after_closure is None
        assert r.directed_access.asymmetric is False
        assert "only one direction exists" in r.directed_access.detail
        assert "physically cut off is a separate question" in (
            r.directed_access.detail)


# --------------------------------------------------------------------------
# 7. direction-only closure
# --------------------------------------------------------------------------
class TestDirectionOnlyClosure:
    def test_removes_one_arc_and_nothing_physical(self, synthetic):
        net = synthetic(SQUARE)
        r = detourv2.analyse(net.snapshot_id, net.link_id("S"),
                             scope="direction", direction="forward",
                             use_cache=False)

        assert r.closure.scope == "direction"
        assert r.closure.direction == "forward"
        assert len(r.closure.removed_arc_ids) == 1

        # Withdrawing one traversal does not remove a road. Gu is untouched, so
        # there is nothing for isolation to find - and claiming otherwise would
        # be the same category error as V1's.
        assert r.isolation.physically_isolates is False
        assert r.isolation.method == "empty-closure"
        assert r.isolation_statement == (
            "No isolation in the represented physical-access graph")

        assert r.forward.status == "OK"
        assert r.forward.alternative_distance_m == pytest.approx(300, abs=1e-6)
        assert r.headline == "Through route found"

    def test_requires_a_direction(self, synthetic):
        net = synthetic(SQUARE)
        with pytest.raises(ValueError):
            detourv2.analyse(net.snapshot_id, net.link_id("S"),
                             scope="direction", direction="both",
                             use_cache=False)


# --------------------------------------------------------------------------
# 8, 9, 10. display wording
# --------------------------------------------------------------------------
class TestDisplayLabel:
    """The map must never render the bare string "No name"."""

    def test_unnamed_state_highway_gets_a_contextual_label(self):
        lab = display_label(
            road_name=None, route_designation=None, name_status="unresolved",
            rca_code=1, rca_name="Waka Kotahi NZ Transport Agency",
            locality="Tokoroa",
            amds_id="{1073a927-4c97-4c9a-b41a-bf6f5edf0cad}#12")
        assert lab.label == "State-highway section near Tokoroa"
        assert lab.kind == "contextual"
        assert lab.secondary == "1073a927#12"
        assert "No name" not in lab.label

    def test_differing_side_localities_name_both(self):
        """LINZ publishes a LEFT and a RIGHT locality per road section.

        They are two sides of the road, not a value and a fallback. The
        reported Tokoroa link has Kinleith on one side and Tokoroa on the
        other; taking the left one alone was reproducible and threw away half
        of what is known, picking the less recognisable name in exactly the
        case the user reported.
        """
        lab = display_label(
            name_status="unresolved", rca_code=1,
            rca_name="Waka Kotahi NZ Transport Agency",
            locality="Kinleith", locality_alt="Tokoroa")
        assert lab.label == "State-highway section between Kinleith and Tokoroa"

    def test_side_localities_are_ordered_independently_of_which_is_left(self):
        """The phrase must not depend on which side LINZ called left."""
        a = display_label(rca_code=1, rca_name="NZTA",
                          locality="Kinleith", locality_alt="Tokoroa").label
        b = display_label(rca_code=1, rca_name="NZTA",
                          locality="Tokoroa", locality_alt="Kinleith").label
        assert a == b

    def test_matching_side_localities_collapse_to_one(self):
        lab = display_label(rca_code=1, rca_name="NZTA",
                            locality="Tokoroa", locality_alt="Tokoroa")
        assert lab.label == "State-highway section near Tokoroa"

    def test_state_highway_without_a_locality_still_says_what_it_is(self):
        lab = display_label(name_status="unresolved", rca_code=1,
                            rca_name="Waka Kotahi NZ Transport Agency")
        assert lab.label == "State-highway section"

    def test_local_road_uses_locality_then_authority(self):
        assert display_label(rca_code=76, rca_name="Auckland Council",
                             locality="Onehunga").label == (
            "Local-road section near Onehunga")
        assert display_label(rca_code=76,
                             rca_name="Waitomo District Council").label == (
            "Road section managed by Waitomo District")

    def test_route_designation_outranks_a_contextual_label(self):
        lab = display_label(route_designation="State Highway 3", rca_code=1,
                            rca_name="Waka Kotahi NZ Transport Agency",
                            locality="Te Kuiti")
        assert lab.label == "State Highway 3"
        assert lab.kind == "route_designation"

    def test_licence_withheld_names_the_authority_not_the_source(self):
        lab = display_label(
            name_status="unresolved", withheld_source="nzta_street_names",
            rca_code=64, rca_name="Waitomo District Council")
        assert lab.label == "Name withheld - Waitomo District"
        assert lab.kind == "withheld"
        # The source system is a licensing detail, not something to show here.
        assert "nzta_street_names" not in lab.label

    def test_conflicting_names_are_reported_as_a_conflict(self):
        lab = display_label(name_status="ambiguous_conflict", rca_code=76,
                            rca_name="Auckland Council", locality="Onehunga")
        assert lab.label == "Name disputed"
        assert lab.kind == "conflict"

    def test_the_four_unnamed_states_stay_distinguishable(self):
        """A regression guard against re-collapsing them.

        `unresolved`, `officially_unnamed`, `ambiguous_conflict` and a withheld
        name are four different facts a reader can act on differently. The map
        chip used to render all four as "No name".
        """
        labels = {
            display_label(name_status="unresolved", rca_code=76,
                          rca_name="Auckland Council", locality="Onehunga").label,
            display_label(name_status="officially_unnamed", rca_code=76,
                          rca_name="Auckland Council", locality="Onehunga").label,
            display_label(name_status="ambiguous_conflict", rca_code=76,
                          rca_name="Auckland Council", locality="Onehunga").label,
            display_label(name_status="unresolved", withheld_source="x",
                          rca_code=76, rca_name="Auckland Council",
                          locality="Onehunga").label,
        }
        assert len(labels) == 4
        assert "No name" not in labels

    def test_never_returns_no_name_or_an_empty_string(self):
        for kwargs in (
            {}, {"name_status": "unresolved"}, {"rca_code": 1},
            {"link_id": 7}, {"road_name": "  "},
            {"name_status": "ambiguous_conflict"},
        ):
            lab = display_label(**kwargs)
            assert lab.label
            assert lab.label.strip()
            assert lab.label != "No name"
