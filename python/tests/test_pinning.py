"""Would the strengthened validation have caught the copy that was wrong?

This file exists to answer one falsifiable question rather than to assert that
things are better now. The bounded copy used to be validated by comparing four
scalars - status, distance, bridge flag, isolated-link count - against a copy
that was ALSO missing `arc_transitions`, missing `physical_access_*`, and
carrying national `component_id` labels.

Two bugs that compound: the copy answered from a different model, and the
instrument could not see the difference. Each test below reconstructs one of
the ways that combination produces a wrong answer that looks right, and checks
that the OLD comparison passes it while the NEW one rejects it.

If a case here showed the new validation passing, the validation would still
be too weak and that would be the finding.
"""

from __future__ import annotations

import pytest

from nzcl.pinning import AnalysisPin, MovementPin, ValidationReport


def old_style_compare(a: AnalysisPin, b: AnalysisPin) -> bool:
    """The four scalars the first version compared. Kept as the control."""
    return (a.status == b.status
            and a.distance_m == b.distance_m
            and a.is_bridge == b.is_bridge
            and a.isolated_link_count == b.isolated_link_count)


def pin(**kw) -> AnalysisPin:
    kw.setdefault("closure_links", (234872,))
    kw.setdefault("profile", "car")
    kw.setdefault("metric", "distance")
    kw.setdefault("movement", MovementPin("m-1", entry_node=10, exit_node=20,
                                          entry_port_link=100,
                                          exit_port_link=200))
    kw.setdefault("route_arcs", (1, 2, 3, 4, 5))
    kw.setdefault("status", "OK")
    kw.setdefault("restrictions_checked", True)
    kw.setdefault("restrictions_applicable", 1)
    kw.setdefault("restriction_violations", 0)
    kw.setdefault("isolated_link_count", 0)
    kw.setdefault("isolated_length_m", 0.0)
    kw.setdefault("is_bridge", False)
    kw.setdefault("distance_m", 7944.4)
    return AnalysisPin(**kw)


CANON = pin()


class TestTheOldComparisonPassesThingsItShouldNot:
    """Each of these is a copy that would have been trusted."""

    def test_a_different_principal_movement_at_the_same_cost(self):
        # Structural identity: different entry/exit nodes, not a different
        # surrogate id - see MovementPin on why ids cannot be compared.
        """The headline case. Two ways out of a closure costing the same is
        not rare on a grid, and the copy is then answering a different
        question while looking validated."""
        copy = pin(movement=MovementPin("m-7", entry_node=11, exit_node=21,
                                        entry_port_link=101,
                                        exit_port_link=201),
                   route_arcs=(9, 8, 7, 6))
        assert old_style_compare(CANON, copy) is True
        assert CANON.agrees_with(copy) is False
        diffs = " ".join(CANON.differences(copy))
        assert "movement entry node" in diffs and "movement exit node" in diffs

    def test_a_different_route_of_identical_length(self):
        copy = pin(route_arcs=(11, 12, 13, 14, 15))
        assert old_style_compare(CANON, copy) is True
        assert CANON.agrees_with(copy) is False
        assert "different way round for the same cost" in \
            " ".join(CANON.differences(copy))

    def test_lost_turn_restrictions_check_nothing_and_report_nothing(self):
        """A copy whose restrictions fell outside the neighbourhood checks
        none and reports no violations, which is the shape of a clean pass."""
        copy = pin(restrictions_checked=False, restrictions_applicable=0)
        assert old_style_compare(CANON, copy) is True
        assert CANON.agrees_with(copy) is False
        assert "restrictions checked" in " ".join(CANON.differences(copy))

    def test_isolation_count_matches_but_the_length_does_not(self):
        base = pin(isolated_link_count=3, isolated_length_m=1840.0)
        copy = pin(isolated_link_count=3, isolated_length_m=420.0)
        assert old_style_compare(base, copy) is True
        assert base.agrees_with(copy) is False
        assert "isolated length" in " ".join(base.differences(copy))

    def test_a_different_profile_answering_the_same_number(self):
        copy = pin(profile="heavy")
        assert old_style_compare(CANON, copy) is True
        assert CANON.agrees_with(copy) is False

    def test_an_unresolved_reason_that_the_status_hides(self):
        base = pin(status="OK", unresolved_reason=None)
        copy = pin(status="OK", unresolved_reason="TIMEOUT")
        assert old_style_compare(base, copy) is True
        assert base.agrees_with(copy) is False


class TestWouldItHaveCaughtTheMissingDerivedStructures:
    """The concrete retrospective check.

    Three separate consequences of the copy that shipped: no
    `arc_transitions`, no `physical_access_*`, and national `component_id`.
    For each, does the new validation reject the copy?
    """

    def test_missing_arc_transitions_is_caught(self):
        """No transitions means the router has no movements at all, so the
        copy reports DISCONNECTED where the canonical answer routed. The old
        comparison catches this one too - it is the visible failure."""
        copy = pin(status="DISCONNECTED", distance_m=None, route_arcs=(),
                   unresolved_reason="NO_PATH")
        assert old_style_compare(CANON, copy) is False
        assert CANON.agrees_with(copy) is False

    def test_missing_physical_access_is_caught_ONLY_by_the_new_check(self):
        """The quiet one, and the reason four scalars were not enough.

        With `physical_access_*` absent, `is_bridge` reads False and
        `isolated_link_count` reads 0 - identical to a genuine "not a bridge,
        nothing cut off". Where the canonical answer is also False/0, the old
        comparison sees nothing wrong. The isolation LENGTH and the explicit
        unresolved reason are what separate "measured, and it is zero" from
        "not measured".
        """
        canonical = pin(is_bridge=False, isolated_link_count=0,
                        isolated_length_m=0.0)
        copy = pin(is_bridge=False, isolated_link_count=0,
                   isolated_length_m=None,
                   unresolved_reason="ISOLATION_NOT_COMPUTED")
        assert old_style_compare(canonical, copy) is True, \
            "the old comparison must actually have been blind to this"
        assert canonical.agrees_with(copy) is False
        diffs = " ".join(canonical.differences(copy))
        assert "isolated length" in diffs and "unresolved reason" in diffs

    def test_national_component_ids_short_circuiting_are_caught(self):
        """`routing._same_component` uses `nodes.component_id` to decide
        whether to search at all. National labels say two nodes are connected
        because a path exists somewhere in New Zealand - which the fragment
        does not contain. The copy either searches and fails, or answers from
        a label describing a network it does not hold."""
        copy = pin(status="DISCONNECTED", distance_m=None, route_arcs=(),
                   unresolved_reason="NOT_IN_SAME_COMPONENT")
        assert CANON.agrees_with(copy) is False
        assert "status" in " ".join(CANON.differences(copy))

    def test_the_answer_to_the_question(self):
        """Stated as a single assertion so it cannot be read ambiguously.

        Of the three consequences: missing transitions and national component
        ids were ALREADY visible to the old comparison, because both turn a
        routed answer into DISCONNECTED. Missing physical access was NOT - and
        that is the one the new validation adds.
        """
        canonical = pin(is_bridge=False, isolated_link_count=0,
                        isolated_length_m=0.0)
        silent = pin(is_bridge=False, isolated_link_count=0,
                     isolated_length_m=None,
                     unresolved_reason="ISOLATION_NOT_COMPUTED")
        assert old_style_compare(canonical, silent) is True
        assert canonical.agrees_with(silent) is False


class TestPinningIsNotTheOnlyGuard:
    """Validation compares answers. It cannot see a structure that is absent
    but happens not to change this particular answer, so the inventory check
    is a precondition rather than a fallback."""

    def test_a_report_records_missing_structures_separately(self):
        r = ValidationReport(
            agreed=True, canonical=CANON, observed=CANON,
            derived_inventory={"arcTransitions": 0, "physicalAccessRuns": 0},
            derived_missing=["arcTransitions", "physicalAccessRuns"])
        d = r.as_dict()
        assert d["derivedMissing"] == ["arcTransitions", "physicalAccessRuns"]
        assert d["agreed"] is True, (
            "agreement and completeness are different facts and must be "
            "reported separately - a copy can agree by luck")


class TestAPinAgreesWithItself:
    def test_identical_analyses_agree(self):
        assert CANON.agrees_with(pin()) is True
        assert CANON.differences(pin()) == []

    def test_the_fingerprint_is_stable_and_discriminating(self):
        assert CANON.fingerprint() == pin().fingerprint()
        assert CANON.fingerprint() != pin(distance_m=7944.5).fingerprint()
        assert CANON.fingerprint() != pin(route_arcs=(5, 4, 3, 2, 1)).fingerprint()

    @pytest.mark.parametrize("field_kw", [
        {"profile": "heavy"}, {"metric": "time"}, {"status": "DISCONNECTED"},
        {"restrictions_applicable": 2}, {"restriction_violations": 1},
        {"isolated_link_count": 1}, {"isolated_length_m": 5.0},
        {"is_bridge": True}, {"closure_links": (1, 2)},
    ])
    def test_every_pinned_field_changes_the_fingerprint(self, field_kw):
        assert CANON.fingerprint() != pin(**field_kw).fingerprint()


class TestSurrogateIdsAreNotComparedBecauseTheyCannotAgree:
    """`movements.movement_id` hashes the SNAPSHOT ID into itself, and port
    ids do the same. Comparing them made every bounded copy fail validation on
    two opaque hashes while agreeing on the nodes, all seventeen arcs and the
    distance to the last digit. It failed safe - it declined rather than
    accepting - and it made the mechanism unusable."""

    def test_the_same_movement_under_a_different_surrogate_id_agrees(self):
        same_place = pin(movement=MovementPin(
            "a-completely-different-hash", entry_node=10, exit_node=20,
            entry_port_link="other", exit_port_link="other"))
        assert CANON.agrees_with(same_place) is True

    def test_a_genuinely_different_movement_still_disagrees(self):
        elsewhere = pin(movement=MovementPin("m-1", entry_node=11,
                                             exit_node=21))
        assert CANON.agrees_with(elsewhere) is False

    def test_the_route_still_catches_a_divergence_the_nodes_would_not(self):
        same_ends = pin(route_arcs=(90, 91, 92))
        assert CANON.agrees_with(same_ends) is False

    def test_the_fingerprint_ignores_the_surrogate_but_not_the_nodes(self):
        assert CANON.fingerprint() == pin(movement=MovementPin(
            "different", entry_node=10, exit_node=20)).fingerprint()
        assert CANON.fingerprint() != pin(movement=MovementPin(
            "m-1", entry_node=99, exit_node=20)).fingerprint()
