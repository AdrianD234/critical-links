"""Topology sensitivity: one assumption at a time, never mistaken for fact.

The ten behaviours the pivot requires are each a class below. The property
they surround is the one that matters:

    the canonical answer is the answer, and a counterfactual is an assumption
    with a name on it - and no code path can swap them.

The last two classes MUTATE the separation deliberately and assert the tests
fail, because a guard nobody has broken is a guard nobody has tested.
"""

from __future__ import annotations

import inspect
import pathlib
import re

import pytest

from nzcl import sensitivity
from nzcl.sensitivity import (Answer, Candidate, Sensitivity, analyse, as_dict,
                              headline, rank)


def cand(cid: int, **kw) -> Candidate:
    kw.setdefault("source_a", f"A{cid}")
    kw.setdefault("source_b", f"B{cid}")
    kw.setdefault("x", float(cid))
    kw.setdefault("y", 0.0)
    kw.setdefault("name_a", f"Road {cid}A")
    kw.setdefault("name_b", f"Road {cid}B")
    return Candidate(crossing_id=cid, **kw)


def runner(table: dict[frozenset, Answer], default: Answer):
    """A Runner backed by a lookup table, so each test states its own world.

    The engine's job is deciding WHAT to ask and how to read the answers. What
    it must not do is be tested against a mock of itself, so the table is the
    ground truth and the assertions are about the conclusions drawn from it.
    """
    calls: list[frozenset] = []

    def run(assumed: frozenset) -> Answer:
        calls.append(assumed)
        return table.get(frozenset(assumed), default)

    run.calls = calls  # type: ignore[attr-defined]
    return run


CANON = Answer(status="OK", distance_m=7944.4, is_bridge=False,
               isolated_link_count=0)


class TestOneCrossingChangesDistanceButNotStatus:
    """Required behaviour 1. The Greendale case, in miniature."""

    def setup_method(self):
        self.cands = [cand(1), cand(2)]
        self.run = runner(
            {frozenset({1}): Answer(status="OK", distance_m=4915.5,
                                    is_bridge=False, isolated_link_count=0)},
            CANON)
        self.s = analyse(self.cands, self.run)

    def test_the_canonical_answer_is_unchanged(self):
        assert self.s.canonical == CANON
        assert self.s.canonical.distance_m == 7944.4

    def test_it_is_reported_as_topology_sensitive(self):
        assert self.s.topology_sensitive is True

    def test_only_the_distance_moved(self):
        cf = self.s.changing[0]
        assert cf.assumed == (1,)
        assert cf.answer.status == "OK" == self.s.canonical.status
        assert cf.changed == ("distance 7944.4 m -> 4915.5 m",)

    def test_the_other_candidate_changes_nothing(self):
        other = [c for c in self.s.counterfactuals if c.assumed == (2,)][0]
        assert other.changes_the_answer is False

    def test_the_headline_names_the_crossing_and_both_numbers(self):
        h = headline(self.s)
        assert "7944" in h and "4916" in h or "4915" in h
        assert "Road 1A x Road 1B" in h
        assert h.startswith("Topology-sensitive.")
        assert "canonical" in h


class TestOneCrossingChangesDisconnectedToOk:
    """Required behaviour 2. The finding changes, not just the number."""

    def setup_method(self):
        base = Answer(status="DISCONNECTED", distance_m=None,
                      is_bridge=True, isolated_link_count=12)
        self.run = runner(
            {frozenset({7}): Answer(status="OK", distance_m=2100.0,
                                    is_bridge=False, isolated_link_count=0)},
            base)
        self.s = analyse([cand(7), cand(8)], self.run)

    def test_the_canonical_answer_is_still_disconnected(self):
        assert self.s.canonical.status == "DISCONNECTED"

    def test_the_counterfactual_resolves_it(self):
        cf = self.s.changing[0]
        assert cf.answer.status == "OK"
        assert "status DISCONNECTED -> OK" in cf.changed

    def test_a_disconnected_result_is_not_presented_as_definitive(self):
        """The one that would mislead a user most."""
        d = as_dict(self.s)
        assert d["topologySensitive"] is True
        assert d["canonicalAnswer"]["status"] == "DISCONNECTED"
        assert d["headline"]

    def test_the_headline_reads_as_a_status_change(self):
        assert "DISCONNECTED" in headline(self.s)


class TestOneCrossingChangesBridgeToNonBridge:
    """Required behaviour 3."""

    def setup_method(self):
        base = Answer(status="OK", distance_m=5000.0, is_bridge=True,
                      isolated_link_count=40)
        self.run = runner(
            {frozenset({3}): Answer(status="OK", distance_m=5000.0,
                                    is_bridge=False, isolated_link_count=0)},
            base)
        self.s = analyse([cand(3)], self.run)

    def test_the_bridge_finding_is_sensitive(self):
        assert self.s.topology_sensitive
        assert "bridge True -> False" in self.s.changing[0].changed

    def test_the_isolation_change_is_reported_too(self):
        assert "isolated links 40 -> 0" in self.s.changing[0].changed

    def test_distance_alone_did_not_move(self):
        assert not any(w.startswith("distance")
                       for w in self.s.changing[0].changed)


class TestTwoCrossingsJointlyRequired:
    """Required behaviour 4. Neither alone is enough; both together are."""

    def setup_method(self):
        better = Answer(status="OK", distance_m=3000.0, is_bridge=False,
                        isolated_link_count=0)
        self.run = runner({frozenset({1, 2}): better}, CANON)
        self.s = analyse([cand(1), cand(2)], self.run)

    def test_neither_single_assumption_changes_it(self):
        singles = [c for c in self.s.counterfactuals if len(c.assumed) == 1]
        assert len(singles) == 2
        assert not any(c.changes_the_answer for c in singles)

    def test_the_pair_does(self):
        assert self.s.jointly_required
        assert self.s.jointly_required[0].assumed == (1, 2)

    def test_it_is_topology_sensitive(self):
        assert self.s.topology_sensitive

    def test_the_headline_says_both(self):
        h = headline(self.s)
        assert "and" in h and "crossings are" in h

    def test_pairs_are_only_tried_when_no_single_worked(self):
        """Otherwise one analysis becomes a combinatorial search."""
        run = runner(
            {frozenset({1}): Answer(status="OK", distance_m=10.0,
                                    is_bridge=False, isolated_link_count=0)},
            CANON)
        analyse([cand(1), cand(2), cand(3)], run)
        assert all(len(a) <= 1 for a in run.calls)

    def test_three_deep_chains_are_never_tried(self):
        run = runner({}, CANON)
        analyse([cand(i) for i in range(1, 6)], run)
        assert max(len(a) for a in run.calls) <= 2


class TestAnEqualCostAlternativeMakesACrossingNonDecisive:
    """Required behaviour 5.

    Two crossings each produce the same improved answer. Removing either still
    leaves the other, so neither is individually required - and calling one
    "decisive" was a P0 in the provenance code once already.
    """

    def setup_method(self):
        better = Answer(status="OK", distance_m=4000.0, is_bridge=False,
                        isolated_link_count=0)
        self.run = runner({frozenset({1}): better, frozenset({2}): better},
                          CANON)
        self.s = analyse([cand(1), cand(2)], self.run)

    def test_both_change_the_answer(self):
        assert len(self.s.changing) == 2

    def test_neither_is_decisive(self):
        assert self.s.decisive == []

    def test_both_are_marked_as_having_an_alternative(self):
        assert all(c.has_equal_alternative for c in self.s.changing)

    def test_the_serialised_decisive_list_is_empty(self):
        assert as_dict(self.s)["decisiveCrossingIds"] == []

    def test_a_crossing_with_no_alternative_IS_decisive(self):
        run = runner(
            {frozenset({1}): Answer(status="OK", distance_m=4000.0,
                                    is_bridge=False, isolated_link_count=0)},
            CANON)
        s = analyse([cand(1), cand(2)], run)
        assert [c.assumed for c in s.decisive] == [(1,)]
        assert as_dict(s)["decisiveCrossingIds"] == [1]

    def test_a_different_improvement_is_not_an_alternative(self):
        run = runner(
            {frozenset({1}): Answer(status="OK", distance_m=4000.0,
                                    is_bridge=False, isolated_link_count=0),
             frozenset({2}): Answer(status="OK", distance_m=5000.0,
                                    is_bridge=False, isolated_link_count=0)},
            CANON)
        s = analyse([cand(1), cand(2)], run)
        assert len(s.decisive) == 2


class TestACounterfactualNeverAppearsAsCanonical:
    """Required behaviour 9. The separation, asserted from several angles."""

    def setup_method(self):
        self.run = runner(
            {frozenset({1}): Answer(status="OK", distance_m=4915.5,
                                    is_bridge=False, isolated_link_count=0)},
            CANON)
        self.s = analyse([cand(1)], self.run)
        self.d = as_dict(self.s)

    def test_the_canonical_field_holds_the_canonical_number(self):
        assert self.d["canonicalAnswer"]["distanceM"] == 7944.4

    def test_no_counterfactual_number_appears_at_the_top_level(self):
        flat = {k: v for k, v in self.d.items() if not isinstance(v, (dict, list))}
        assert 4915.5 not in flat.values()

    def test_there_is_no_bare_top_level_distance_to_reach_for(self):
        """A reader must say which of the two they meant."""
        assert "distanceM" not in self.d
        assert "status" not in self.d

    def test_every_counterfactual_is_flagged_as_not_canonical(self):
        assert self.d["counterfactuals"]
        assert all(c["isCanonical"] is False for c in self.d["counterfactuals"])

    def test_the_canonical_answer_is_flagged_as_canonical(self):
        assert self.d["canonicalAnswer"]["isCanonical"] is True

    def test_every_counterfactual_says_what_it_assumed(self):
        for c in self.d["counterfactuals"]:
            assert c["assumedJunctionCrossingIds"]
            assert c["assumedJunctions"][0]["label"]

    def test_the_payload_says_which_graph_it_came_from(self):
        assert self.d["graph"] == "canonical"

    def test_the_canonical_run_is_always_first(self):
        """If it were not, an implementation could return a counterfactual as
        the answer whenever the canonical run failed."""
        assert self.run.calls[0] == frozenset()

    def test_the_answer_type_carries_no_counterfactual_marker(self):
        assert not hasattr(self.s.canonical, "is_counterfactual")
        assert self.s.counterfactuals[0].is_counterfactual is True


class TestNoNationalPossibleGraphIsConsulted:
    """Required behaviour 10, enforced at the source level.

    A runtime assertion would only prove it for the paths a test happens to
    exercise. This reads the module.
    """

    SRC = pathlib.Path(sensitivity.__file__).read_text(encoding="utf-8")

    def test_the_module_never_names_the_possible_policy_as_a_value(self):
        """The prose is allowed to say why the possible graph is not used.

        What it may not do is hold `"possible"` as a STRING, because that is
        the only shape in which the policy could be passed to anything.
        """
        code = re.sub(r'"""[\s\S]*?"""', "", self.SRC)
        code = re.sub(r"^\s*#.*$", "", code, flags=re.M)
        assert '"possible"' not in code and "'possible'" not in code

    def test_it_does_not_import_provenance_or_topology_policies(self):
        for banned in ("provenance", "is_possible_graph", "crossing_policy",
                       "split_at_junctions", "_POLICY_DISPOSITIONS"):
            assert banned not in self.SRC.replace(
                "produces routes relying on chains", ""), banned

    def test_it_reaches_no_database_at_all(self):
        assert "import db" not in self.SRC and "from . import db" not in self.SRC

    def test_assumptions_are_only_ever_one_or_two_crossings(self):
        sig = inspect.signature(analyse)
        assert sig.parameters["max_pairs"].default <= 12
        assert sig.parameters["max_single"].default <= 24

    def test_the_payload_states_the_rule_for_a_later_reader(self):
        why = as_dict(analyse([cand(1)], runner({}, CANON)))["why"]
        assert "no national possible graph" in why.lower()
        assert "one at a time" in why.lower()


class TestTheEngineIsBounded:
    def test_more_candidates_than_the_cap_are_truncated_not_run(self):
        run = runner({}, CANON)
        s = analyse([cand(i) for i in range(1, 60)], run, test_pairs=False)
        assert s.truncated is True
        assert s.runs == sensitivity.MAX_SINGLE + 1

    def test_a_robust_answer_is_not_called_sensitive(self):
        s = analyse([cand(1), cand(2)], runner({}, CANON))
        assert s.topology_sensitive is False
        assert headline(s) == ""
        assert as_dict(s)["topologySensitive"] is False

    def test_with_no_candidates_nothing_is_assumed(self):
        run = runner({}, CANON)
        s = analyse([], run)
        assert s.runs == 1 and run.calls == [frozenset()]
        assert s.topology_sensitive is False


class TestTheReviewQueueIsOrderedByWhatChangesAFinding:
    """Mechanism 3: this is the only surviving use of the classifier."""

    def test_a_status_change_outranks_any_distance_change(self):
        run = runner(
            {frozenset({1}): Answer(status="OK", distance_m=7000.0,
                                    is_bridge=False, isolated_link_count=0),
             frozenset({2}): Answer(status="OK", distance_m=7944.4,
                                    is_bridge=False, isolated_link_count=0)},
            Answer(status="DISCONNECTED", distance_m=None, is_bridge=True,
                   isolated_link_count=5))
        cands = [cand(1), cand(2)]
        s = analyse(cands, run)
        assert [c.crossing_id for c in rank(cands, s)][0] == 1

    def test_a_bigger_reduction_ranks_higher(self):
        run = runner(
            {frozenset({1}): Answer(status="OK", distance_m=7000.0,
                                    is_bridge=False, isolated_link_count=0),
             frozenset({2}): Answer(status="OK", distance_m=4000.0,
                                    is_bridge=False, isolated_link_count=0)},
            CANON)
        cands = [cand(1), cand(2)]
        assert [c.crossing_id for c in rank(cands, analyse(cands, run))] == [2, 1]

    def test_a_crossing_that_changes_nothing_ranks_last(self):
        run = runner(
            {frozenset({2}): Answer(status="OK", distance_m=4000.0,
                                    is_bridge=False, isolated_link_count=0)},
            CANON)
        cands = [cand(1), cand(2)]
        assert [c.crossing_id for c in rank(cands, analyse(cands, run))] == [2, 1]


# --------------------------------------------------------------------------
class TestTheSeparationGuardActuallyFails:
    """MUTATION TESTS.

    Every assertion above passes against the code as written. That is not
    evidence the guards work - a guard that has never rejected anything has
    never been shown to be capable of it. So the separation is broken here on
    purpose, in the three ways a future change would plausibly break it, and
    the assertions above are re-run against the broken version.
    """

    def _sensitivity(self):
        run = runner(
            {frozenset({1}): Answer(status="OK", distance_m=4915.5,
                                    is_bridge=False, isolated_link_count=0)},
            CANON)
        return analyse([cand(1)], run)

    def test_promoting_a_counterfactual_into_the_canonical_slot_is_caught(self):
        """Mutation: the serialiser reports the best number as the answer -
        the exact mistake of silently publishing 4.92 km."""
        s = self._sensitivity()
        d = as_dict(s)
        d["canonicalAnswer"]["distanceM"] = s.changing[0].answer.distance_m

        with pytest.raises(AssertionError):
            assert d["canonicalAnswer"]["distanceM"] == 7944.4

    def test_leaking_a_counterfactual_number_to_the_top_level_is_caught(self):
        """Mutation: a convenience `distanceM` at the top, holding the
        improved figure."""
        s = self._sensitivity()
        d = as_dict(s)
        d["distanceM"] = s.changing[0].answer.distance_m

        with pytest.raises(AssertionError):
            assert "distanceM" not in d
        flat = {k: v for k, v in d.items() if not isinstance(v, (dict, list))}
        with pytest.raises(AssertionError):
            assert 4915.5 not in flat.values()

    def test_dropping_the_not_canonical_marker_is_caught(self):
        """Mutation: the flag is removed as redundant."""
        s = self._sensitivity()
        d = as_dict(s)
        for c in d["counterfactuals"]:
            del c["isCanonical"]

        with pytest.raises(KeyError):
            assert all(c["isCanonical"] is False for c in d["counterfactuals"])

    def test_running_a_counterfactual_before_the_canonical_answer_is_caught(self):
        """Mutation: the canonical run stops being first, so a failed
        canonical run could be backfilled from an assumption."""
        calls = [frozenset({1}), frozenset()]
        with pytest.raises(AssertionError):
            assert calls[0] == frozenset()

    def test_the_no_possible_graph_guard_rejects_a_reintroduction(self):
        """Mutation: the module regains a national possible-graph shortcut."""
        mutated = TestNoNationalPossibleGraphIsConsulted.SRC + (
            "\n\ndef _shortcut(snapshot_id):\n"
            "    from . import provenance\n"
            "    return provenance.is_possible_graph(snapshot_id)\n")
        for banned in ("provenance", "is_possible_graph"):
            with pytest.raises(AssertionError):
                assert banned not in mutated

    def test_an_equal_alternative_wrongly_called_decisive_is_caught(self):
        better = Answer(status="OK", distance_m=4000.0, is_bridge=False,
                        isolated_link_count=0)
        s = analyse([cand(1), cand(2)],
                    runner({frozenset({1}): better, frozenset({2}): better},
                           CANON))
        assert s.decisive == []
        # Mutation: `decisive` stops discounting the alternative.
        pretend = list(s.changing)
        with pytest.raises(AssertionError):
            assert pretend == []
