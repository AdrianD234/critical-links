"""Property tests for request-local identifier allocation.

The defect this module exists to prevent did not look like a collision. An arc
was issued the id -1, pgRouting uses -1 to mark the end of a path, and the
router filtered the arc out of its own route: the search succeeded and the
total was quietly 100 m short.

So these tests are not only about two ids being equal. They are about the
reserved values, the disjointness of the bands, and the range - the assumptions
that were previously carried in someone's head.

The generated cases use a seeded `random`, so a failure is reproducible from
the seed printed in the parameter id rather than being a coin toss in CI.
"""

from __future__ import annotations

import random

import pytest

from nzcl import routing, vids
from nzcl.vids import VirtualIds

KINDS = ("arc", "link", "node")


class TestReservedValues:

    def test_zero_and_minus_one_are_reserved(self):
        assert vids.RESERVED_IDS == {0, -1}

    def test_the_pgrouting_sentinel_is_one_of_them(self):
        """The reservation and the reason for it must not drift apart."""
        assert routing.RESERVED_EDGE_SENTINEL in vids.RESERVED_IDS

    @pytest.mark.parametrize("kind", KINDS)
    def test_no_band_can_reach_a_reserved_value(self, kind):
        """Structural, not incidental: the bands start a trillion away."""
        assert vids.band_top(kind) < -1
        for reserved in vids.RESERVED_IDS:
            assert not (vids.band_floor(kind) < reserved <= vids.band_top(kind))

    def test_a_long_run_never_issues_a_reserved_id(self):
        ids = VirtualIds()
        issued = [getattr(ids, k)() for _ in range(2_000) for k in KINDS]
        assert not (set(issued) & vids.RESERVED_IDS)


class TestBandsAreDisjoint:
    """Several of these travel through code that types them all as `int`."""

    def test_kinds_never_share_an_id(self):
        ids = VirtualIds()
        by_kind = {k: {getattr(ids, k)() for _ in range(5_000)} for k in KINDS}

        assert by_kind["arc"].isdisjoint(by_kind["link"])
        assert by_kind["arc"].isdisjoint(by_kind["node"])
        assert by_kind["link"].isdisjoint(by_kind["node"])

    @pytest.mark.parametrize("kind", KINDS)
    def test_an_id_reports_the_band_it_came_from(self, kind):
        ids = VirtualIds()
        for _ in range(100):
            assert vids.kind_of(getattr(ids, kind)()) == kind

    def test_a_real_id_belongs_to_no_band(self):
        for real in (0, 1, 42, 375_695, 731_285, 2**31):
            assert vids.kind_of(real) is None
            assert vids.is_real(real)
            assert not vids.is_virtual(real)

    def test_virtual_and_real_never_overlap(self):
        ids = VirtualIds()
        for _ in range(500):
            for kind in KINDS:
                i = getattr(ids, kind)()
                assert vids.is_virtual(i)
                assert not vids.is_real(i)


class TestRange:

    @pytest.mark.parametrize("kind", KINDS)
    def test_every_band_stays_inside_signed_bigint(self, kind):
        assert vids.band_floor(kind) > vids.BIGINT_MIN

    def test_a_long_run_stays_in_range(self):
        ids = VirtualIds()
        for _ in range(10_000):
            for kind in KINDS:
                assert getattr(ids, kind)() > vids.BIGINT_MIN

    def test_exhausting_a_band_raises_rather_than_wrapping(self):
        """A runaway loop must fail, not spill into the next band."""
        ids = VirtualIds()
        ids._issued["arc"] = vids.BAND_WIDTH
        with pytest.raises(OverflowError):
            ids.arc()


class TestDeterminism:
    """Same call sequence, same ids - which is what a fingerprint rests on."""

    def test_two_allocators_agree(self):
        def run():
            ids = VirtualIds()
            return [ids.node(), ids.arc(), ids.arc(), ids.link(), ids.node()]

        assert run() == run()

    @pytest.mark.parametrize("seed", [1, 7, 13, 99, 2026])
    def test_a_random_call_sequence_replays_identically(self, seed):
        def run():
            rng = random.Random(seed)
            ids = VirtualIds()
            return [getattr(ids, rng.choice(KINDS))() for _ in range(300)]

        assert run() == run()

    def test_allocators_are_independent(self):
        """One request's numbering says nothing about another's - and must not,
        or two concurrent splits would have to coordinate."""
        first, second = VirtualIds(), VirtualIds()
        assert first.arc() == second.arc()

    def test_counts_are_reported_per_kind(self):
        ids = VirtualIds()
        ids.arc(); ids.arc(); ids.node()
        assert ids.issued("arc") == 2
        assert ids.issued("node") == 1
        assert ids.issued("link") == 0
        assert ids.total_issued == 3


class TestDescribe:
    """Error messages have to be able to say what an id is."""

    def test_it_names_each_case(self):
        ids = VirtualIds()
        assert "real" in vids.describe(17)
        assert "virtual arc" in vids.describe(ids.arc())
        assert "virtual node" in vids.describe(ids.node())
        # Between the reserved values and the first band: not a real id, and
        # not one this module could have issued.
        assert "neither" in vids.describe(-5)
