"""Does the invented-movement audit measure what it claims to measure?

WHY THIS EXISTS
---------------
A national ingest currently refuses to load:

    refusing to load: 387 crossing(s) left disconnected by the classifier are
    connected in the graph anyway.

The audit's stated property is narrow and correct:

    for every crossing NOT noded, the two source features must share no graph
    node AT THAT CROSSING POINT.

The implementation searches a different question. Nodes are merged at
`node_tolerance_m`, which is **0.01 m**, but the audit accepts any shared node
within **1.0 m** of the crossing:

    if (px - x.x) ** 2 + (py - x.y) ** 2 > 1.0:   # squared -> a 1.0 m radius
        continue

That is a hundred times the tolerance at which a node actually exists. So a
crossing that was correctly refused can be reported as connected because the
two roads legitimately meet at a DIFFERENT point up to a metre away - which on
a real network is an ordinary T-junction beside an overbridge.

These fixtures separate the two readings exactly. They are synthetic and
millimetre-precise, so nothing here depends on a national download, and each
one is a case a reviewer can picture.

WHAT IS DELIBERATELY NOT DONE
-----------------------------
Nothing here changes the audit. The gate stays exactly as strict as it is until
the evidence says which side is wrong; these tests document the behaviour and
mark the one case that looks like a false positive with an explicit xfail, so
the suite stays green and the claim stays visible.
"""

from __future__ import annotations

import pytest

from nzcl.topology import (CANONICAL_CROSSING_POLICY, SourceLink,
                           audit_no_invented_movements, split_at_junctions)

#: The tolerance a node actually exists at, and the radius the audit searches.
NODE_TOLERANCE_M = 0.01
AUDIT_RADIUS_M = 1.0


def road(amds_id: str, *coords: tuple[float, float]) -> SourceLink:
    """A two-way ordinary road. Attributes are the minimum the classifier reads."""
    return SourceLink(
        amds_id=amds_id,
        coords=[(float(x), float(y)) for x, y in coords],
        attrs={"oneway": 2, "rca_code": 2, "model_asset_type": 1,
               "forward_allowed": True, "reverse_allowed": True},
    )


def split(sources, **kw):
    return split_at_junctions(
        sources, crossing_policy=CANONICAL_CROSSING_POLICY, **kw)


def violations(sources, **kw) -> list[str]:
    """Run the canonical split and return the audit's complaints."""
    return audit_no_invented_movements(split(sources, **kw),
                                       node_tolerance_m=NODE_TOLERANCE_M)


# ---------------------------------------------------------------------------
# 1. An exact unauthorised crossing node. The audit MUST fail this.
# ---------------------------------------------------------------------------
#
#         C ends exactly on the crossing point
#                    |
#     A  ------------+------------
#                    |
#                    B
#
# C's endpoint at (50, 0) cuts BOTH A and B there, so A and B share a node at
# the crossing itself. Traffic can now turn from A onto B at a place the policy
# refused to connect. That is a real invented movement.
EXACT = [
    road("A", (0, 0), (100, 0)),
    road("B", (50, -50), (50, 50)),
    road("C", (50, 0), (50 + 0.0, 30.0)),
]


class TestAnExactUnauthorisedNodeIsCaught:

    def test_the_crossing_is_detected(self):
        result = split(EXACT)
        assert result.crossings, "no interior crossing was detected at all"

    def test_nothing_was_noded_under_the_canonical_policy(self):
        """With no overrides, `evidence` nodes no interior crossing."""
        result = split(EXACT)
        assert result.crossing_cuts == 0

    def test_the_audit_reports_it(self):
        found = violations(EXACT)
        assert found, "an exact shared node at the crossing must be a violation"
        assert "A" in found[0] and "B" in found[0]


# ---------------------------------------------------------------------------
# 2. A legitimate junction NEAR an unrelated crossing. The audit must NOT fail.
# ---------------------------------------------------------------------------
#
#     A  -----------------+--+-----------------
#                         |  |
#                    crossing  T-junction 0.5 m away
#                         |  |
#                         B--+
#
# B crosses A's interior at (50, 0) - refused, correctly. B then loops back and
# ENDS on A at (50.5, 0), which is an ordinary T-junction and is cut normally.
# A and B share a node at (50.5, 0), which is 0.5 m from the crossing.
#
# The stated property is not violated: they share no node AT the crossing. The
# implementation's 1.0 m radius sees the T-junction node and reports one.
NEARBY = [
    road("A", (0, 0), (100, 0)),
    road("B", (50, -40), (50, 20), (50.5, 20), (50.5, 0)),
]


class TestALegitimateJunctionNearACrossing:

    def test_the_crossing_is_detected(self):
        result = split(NEARBY)
        assert result.crossings

    def test_the_shared_node_is_half_a_metre_from_the_crossing(self):
        """Guards the fixture: if the two coincided, the case would be case 1."""
        result = split(NEARBY)
        crossing = result.crossings[0]
        assert abs(crossing.x - 50.0) < 1e-6
        assert abs(crossing.y - 0.0) < 1e-6
        # The T-junction is at (50.5, 0): outside node tolerance, inside the
        # audit radius. That gap is the whole question.
        separation = 0.5
        assert separation > NODE_TOLERANCE_M
        assert separation < AUDIT_RADIUS_M

    @pytest.mark.xfail(
        reason="The audit searches a 1.0 m radius while nodes merge at 0.01 m, "
               "so it reports a legitimate T-junction 0.5 m away as a "
               "connection AT the crossing. Suspected cause of a share of the "
               "national 387. Left failing rather than fixed: the gate stays "
               "strict until the national distance distribution says which "
               "side is wrong.",
        strict=True)
    def test_the_audit_should_not_report_it(self):
        assert violations(NEARBY) == []

    def test_it_is_reported_today_and_this_is_the_symptom(self):
        """The same fact, asserted positively, so the behaviour is recorded
        even if the xfail above is later removed."""
        found = violations(NEARBY)
        assert found, "fixture no longer reproduces the suspected false positive"
        assert "50.000" in found[0], found[0]


# ---------------------------------------------------------------------------
# 3. An endpoint-on-interior T-junction close to an interior crossing.
# ---------------------------------------------------------------------------
#
# Like case 2, but the near node is created by a THIRD road ending on A, and B
# does not touch it. Only one of the two crossing partners is on the node, so
# there is no movement between A and B - and the audit correctly says nothing.
# This is the control: it shows the audit is not simply flagging any node.
THIRD_ROAD_NEAR = [
    road("A", (0, 0), (100, 0)),
    road("B", (50, -50), (50, 50)),
    road("D", (50.5, 0), (50.5, -30)),
]


class TestAThirdRoadJunctionNearACrossing:

    def test_only_one_partner_touches_the_nearby_node(self):
        found = violations(THIRD_ROAD_NEAR)
        assert found == [], (
            "D's endpoint connects A and D, not A and B - there is no movement "
            f"between the crossing partners: {found}")


# ---------------------------------------------------------------------------
# 4. A third road ending at the exact crossing coordinate.
# ---------------------------------------------------------------------------
#
# The mixed-place hazard the audit docstring names: some OTHER cut lands on the
# crossing coordinate and both roads touch it. Must fail.
THIRD_ROAD_EXACT = [
    road("A", (0, 0), (100, 0)),
    road("B", (50, -50), (50, 50)),
    road("D", (50, 0), (80, -30)),
]


class TestAThirdRoadNodeAtTheCrossingCoordinate:

    def test_the_audit_reports_it(self):
        found = violations(THIRD_ROAD_EXACT)
        assert found, (
            "a third road ending exactly on the crossing connects both "
            "partners there, which is a real invented movement")


# ---------------------------------------------------------------------------
# 5. Determinism. The audit must not depend on the order sources arrive in.
# ---------------------------------------------------------------------------
ORDERS = [(0, 1, 2), (2, 1, 0), (1, 0, 2), (2, 0, 1), (0, 2, 1), (1, 2, 0)]


def _pairs(found: list[str]) -> set[frozenset[str]]:
    """The crossing pairs a violation list names, without their ordering."""
    out = set()
    for v in found:
        left = v.split(" at ")[0]
        a, b = left.split(" x ")
        out.add(frozenset((a.strip(), b.strip())))
    return out


class TestRowOrderDeterminism:
    """The VERDICT is order-independent. The MESSAGE is not."""

    @pytest.mark.parametrize("order", ORDERS)
    def test_the_verdict_does_not_depend_on_row_order(self, order):
        shuffled = [EXACT[i] for i in order]

        baseline = violations(EXACT)
        other = violations(shuffled)

        assert len(other) == len(baseline)
        assert _pairs(other) == _pairs(baseline)

    @pytest.mark.parametrize("order", ORDERS)
    def test_the_control_case_stays_clean_whatever_the_row_order(self, order):
        assert violations([THIRD_ROAD_NEAR[i] for i in order]) == []

    def test_the_message_names_the_pair_in_row_order(self):
        """A separate finding, and it matters for triage rather than for
        correctness.

        The same crossing is reported either way, so no graph is accepted or
        refused differently. But the TEXT flips between "A x B" and "B x A"
        depending on which source was read first, and the violation list is the
        only record of the cohort. Deduplicating 387 messages into unique
        physical places - which is the first thing anyone investigating them
        does - cannot key on that string without collapsing or double-counting
        depending on ingest order.
        """
        forward = violations(EXACT)
        reversed_rows = violations([EXACT[1], EXACT[0], EXACT[2]])

        assert _pairs(forward) == _pairs(reversed_rows)
        assert forward != reversed_rows, (
            "the message is no longer order-sensitive; this finding is fixed "
            "and the test should become a plain equality assertion")


# ---------------------------------------------------------------------------
# 6. The mismatch itself, asserted directly against the code.
# ---------------------------------------------------------------------------
class TestTheRadiusMismatchIsReal:
    """So the finding survives as an executable claim, not a comment."""

    def test_the_audit_radius_is_a_hundred_times_the_node_tolerance(self):
        import inspect

        source = inspect.getsource(audit_no_invented_movements)

        # The default the ingest calls it with.
        signature = inspect.signature(audit_no_invented_movements)
        assert signature.parameters["node_tolerance_m"].default == NODE_TOLERANCE_M
        # And the radius baked into the scan.
        assert "> 1.0" in source, (
            "the 1.0 m audit radius has moved; this finding needs re-measuring")
        assert AUDIT_RADIUS_M / NODE_TOLERANCE_M == 100.0
