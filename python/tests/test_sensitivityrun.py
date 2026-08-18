"""The production runner: canonical first, assumptions after, copies always gone.

`analyse_fn` and `pin_fn` are injected, so every behaviour here is exercised
without a snapshot. What is being tested is the ORDER and the GUARANTEES - the
canonical analysis runs first and is returned unchanged, the frozen movement
is injected rather than rediscovered, unavailability is never dressed up as
robustness, and every transient copy is dropped on every path including
cancellation.
"""

from __future__ import annotations

import pytest

from nzcl import candidates as candidates_mod
from nzcl import neighbourhood, pinning, sensitivityrun, whatif
from nzcl.pinning import AnalysisPin, MovementPin
from nzcl.sensitivity import Candidate

CANON_PIN = AnalysisPin(
    closure_links=(234872,), profile="car", metric="distance",
    movement=MovementPin("m-1", entry_node=10, exit_node=20),
    route_arcs=(1, 2, 3), status="OK", distance_m=7944.4,
    is_bridge=False, isolated_link_count=0, isolated_length_m=0.0,
    restrictions_checked=True)

BETTER_PIN = AnalysisPin(
    closure_links=(234872,), profile="car", metric="distance",
    movement=MovementPin("m-1", entry_node=10, exit_node=20),
    route_arcs=(1, 9, 3), status="OK", distance_m=4915.5,
    is_bridge=False, isolated_link_count=0, isolated_length_m=0.0,
    restrictions_checked=True)


class Result:
    def __init__(self, pin, route_link_ids=(), port_node_ids=()):
        self.pin = pin
        self.route_link_ids = route_link_ids
        self.port_node_ids = port_node_ids


def wire(monkeypatch, *, candidates, cf_pin_by_crossing=None,
         cancel_after=None):
    """Patch every boundary the runner touches, and record what happened."""
    state = {"analyse_calls": [], "copies": [], "dropped": [], "noded": []}
    cf_pin_by_crossing = cf_pin_by_crossing or {}

    monkeypatch.setattr(
        candidates_mod, "find",
        lambda snap, **kw: candidates_mod.CandidateSearch(
            candidates=list(candidates), by_source={"corridor": len(candidates)},
            considered=len(candidates), sources_used=["corridor"]))

    monkeypatch.setattr(
        neighbourhood, "extract_validated",
        lambda src, link_id, **kw: neighbourhood.Extraction(
            snapshot_id="cf-nb", source_snapshot_id=src, radius_m=5000.0,
            link_count=800, node_count=1600, seconds=0.2, validated=True,
            derived_built=True, transition_count=2650, component_count=10))
    monkeypatch.setattr(neighbourhood, "derived_inventory",
                        lambda sid: {"arcTransitions": 2650,
                                     "physicalAccessRuns": 1})

    def _copy(src, dst, **kw):
        state["copies"].append(dst)

    def _drop(sid):
        state["dropped"].append(sid)

    monkeypatch.setattr(whatif, "copy_snapshot", _copy)
    monkeypatch.setattr(whatif, "drop_snapshot", _drop)
    monkeypatch.setattr(whatif, "node_crossings",
                        lambda sid, edits, **kw: state["noded"].append(
                            (sid, [(e.link_a, e.link_b) for e in edits])))
    monkeypatch.setattr(
        sensitivityrun, "_crossing_links",
        lambda sid, c: (c.crossing_id * 10, c.crossing_id * 10 + 1))

    def analyse_fn(snap, link_id, pinned_movement=None):
        state["analyse_calls"].append((snap, link_id, pinned_movement))
        if snap.startswith("cf-") and state["noded"]:
            last_sid, edits = state["noded"][-1]
            if last_sid == snap:
                cid = edits[0][0] // 10
                return Result(cf_pin_by_crossing.get(cid, CANON_PIN))
        return Result(CANON_PIN, route_link_ids=(1, 2), port_node_ids=(10, 20))

    return state, analyse_fn, (lambda r: r.pin)


def cand(cid):
    return Candidate(crossing_id=cid, source_a=f"A{cid}", source_b=f"B{cid}",
                     x=float(cid), y=0.0, name_a=f"Road {cid}A",
                     name_b=f"Road {cid}B")


class TestTheCanonicalAnalysisComesFirstAndIsUnchanged:
    def test_it_is_the_very_first_analysis_run(self, monkeypatch):
        state, analyse_fn, pin_fn = wire(monkeypatch, candidates=[cand(1)])
        sensitivityrun.run("snap", 234872, analyse_fn=analyse_fn,
                           pin_fn=pin_fn)
        first = state["analyse_calls"][0]
        assert first[0] == "snap", "the canonical run is on the REAL snapshot"
        assert first[2] is None, "and it is not itself pinned to anything"

    def test_the_canonical_answer_is_reported_unchanged(self, monkeypatch):
        state, analyse_fn, pin_fn = wire(
            monkeypatch, candidates=[cand(1)],
            cf_pin_by_crossing={1: BETTER_PIN})
        out = sensitivityrun.run("snap", 234872, analyse_fn=analyse_fn,
                                 pin_fn=pin_fn).as_dict()
        assert out["canonicalAnswer"]["distanceM"] == 7944.4
        assert out["canonicalAnswer"]["isCanonical"] is True

    def test_the_counterfactual_never_occupies_the_canonical_slot(
            self, monkeypatch):
        state, analyse_fn, pin_fn = wire(
            monkeypatch, candidates=[cand(1)],
            cf_pin_by_crossing={1: BETTER_PIN})
        out = sensitivityrun.run("snap", 234872, analyse_fn=analyse_fn,
                                 pin_fn=pin_fn).as_dict()
        assert out["canonicalAnswer"]["distanceM"] != 4915.5
        assert any(c["distanceM"] == 4915.5 for c in out["counterfactuals"])
        assert all(c["isCanonical"] is False for c in out["counterfactuals"])


class TestTheFrozenMovementIsInjected:
    """Construction, not detection. A copy told which movement to answer
    cannot answer a different one and return a coincidentally equal cost."""

    def test_every_copy_analysis_is_given_the_pinned_movement(self,
                                                              monkeypatch):
        state, analyse_fn, pin_fn = wire(monkeypatch,
                                         candidates=[cand(1), cand(2)])
        sensitivityrun.run("snap", 234872, analyse_fn=analyse_fn,
                           pin_fn=pin_fn)
        copy_calls = [c for c in state["analyse_calls"] if c[0] != "snap"]
        assert copy_calls, "counterfactuals must have run"
        for _, _, pinned in copy_calls:
            assert pinned == CANON_PIN.movement

    def test_the_validation_pass_is_pinned_too(self, monkeypatch):
        """The copy must not choose its own movement while proving itself
        either - that is the pass the whole trust chain rests on."""
        seen = {}

        def _extract_validated(src, link_id, *, canonical, answer_of, **kw):
            answer_of("cf-probe")
            return neighbourhood.Extraction(
                snapshot_id="cf-nb", source_snapshot_id=src, radius_m=5000.0,
                link_count=800, node_count=1600, seconds=0.2, validated=True,
                derived_built=True)

        state, analyse_fn, pin_fn = wire(monkeypatch, candidates=[cand(1)])
        monkeypatch.setattr(neighbourhood, "extract_validated",
                            _extract_validated)
        sensitivityrun.run("snap", 234872, analyse_fn=analyse_fn,
                           pin_fn=pin_fn)
        probe = [c for c in state["analyse_calls"] if c[0] == "cf-probe"]
        assert probe and probe[0][2] == CANON_PIN.movement


class TestEveryCopyIsDropped:
    def test_on_the_happy_path(self, monkeypatch):
        state, analyse_fn, pin_fn = wire(monkeypatch,
                                         candidates=[cand(1), cand(2)])
        sensitivityrun.run("snap", 234872, analyse_fn=analyse_fn,
                           pin_fn=pin_fn)
        assert set(state["copies"]) <= set(state["dropped"])
        assert "cf-nb" in state["dropped"], "the neighbourhood too"

    def test_on_cancellation(self, monkeypatch):
        calls = {"n": 0}

        def cancel():
            calls["n"] += 1
            return calls["n"] > 1

        state, analyse_fn, pin_fn = wire(
            monkeypatch, candidates=[cand(1), cand(2), cand(3)])
        out = sensitivityrun.run("snap", 234872, analyse_fn=analyse_fn,
                                 pin_fn=pin_fn, should_cancel=cancel)
        assert out.available is False
        assert "cancelled" in out.unavailable_reason
        assert set(state["copies"]) <= set(state["dropped"])
        assert "cf-nb" in state["dropped"]

    def test_a_cancelled_run_never_returns_partial_sensitivity(self,
                                                               monkeypatch):
        state, analyse_fn, pin_fn = wire(monkeypatch,
                                         candidates=[cand(1), cand(2)])
        out = sensitivityrun.run("snap", 234872, analyse_fn=analyse_fn,
                                 pin_fn=pin_fn, should_cancel=lambda: True)
        assert out.sensitivity is None
        assert out.as_dict()["available"] is False


class TestUnavailabilityIsNeverDressedUpAsRobustness:
    def test_no_candidates_says_so_without_claiming_robust(self, monkeypatch):
        state, analyse_fn, pin_fn = wire(monkeypatch, candidates=[])
        out = sensitivityrun.run("snap", 234872, analyse_fn=analyse_fn,
                                 pin_fn=pin_fn).as_dict()
        assert out["available"] is False
        assert "NOT the same as the answer being robust" in out["why"]

    def test_a_truncated_search_with_no_candidates_says_that_too(self,
                                                                 monkeypatch):
        monkeypatch.setattr(
            candidates_mod, "find",
            lambda snap, **kw: candidates_mod.CandidateSearch(
                candidates=[], truncated=True, considered=200))
        state, analyse_fn, pin_fn = wire(monkeypatch, candidates=[])
        monkeypatch.setattr(
            candidates_mod, "find",
            lambda snap, **kw: candidates_mod.CandidateSearch(
                candidates=[], truncated=True, considered=200))
        out = sensitivityrun.run("snap", 234872, analyse_fn=analyse_fn,
                                 pin_fn=pin_fn)
        assert "TRUNCATED" in out.unavailable_reason

    def test_a_neighbourhood_that_cannot_reproduce_canonical_is_unavailable(
            self, monkeypatch):
        state, analyse_fn, pin_fn = wire(monkeypatch, candidates=[cand(1)])

        def _fail(src, link_id, **kw):
            raise neighbourhood.NeighbourhoodTooSmall(
                (5000.0, 12000.0), (400, 900), "the boundary bit")

        monkeypatch.setattr(neighbourhood, "extract_validated", _fail)
        out = sensitivityrun.run("snap", 234872, analyse_fn=analyse_fn,
                                 pin_fn=pin_fn)
        assert out.available is False
        assert "reproduced the canonical answer" in out.unavailable_reason

    def test_an_incomplete_copy_is_unavailable_not_silently_used(self,
                                                                 monkeypatch):
        state, analyse_fn, pin_fn = wire(monkeypatch, candidates=[cand(1)])

        def _fail(src, link_id, **kw):
            raise neighbourhood.DerivedStructuresMissing(
                "cf-x", {"arcTransitions": 0}, ["arcTransitions"])

        monkeypatch.setattr(neighbourhood, "extract_validated", _fail)
        out = sensitivityrun.run("snap", 234872, analyse_fn=analyse_fn,
                                 pin_fn=pin_fn)
        assert out.available is False
        assert "incomplete" in out.unavailable_reason


class TestTimingIsMeasuredPerStage:
    def test_each_stage_is_reported_separately(self, monkeypatch):
        state, analyse_fn, pin_fn = wire(monkeypatch,
                                         candidates=[cand(1), cand(2)])
        d = sensitivityrun.run("snap", 234872, analyse_fn=analyse_fn,
                               pin_fn=pin_fn).as_dict()["timing"]
        for k in ("canonicalMs", "candidateSearchMs",
                  "neighbourhoodExtractAndValidateMs",
                  "singleCounterfactualMs", "cleanupMs", "totalMs"):
            assert k in d
        # Two singles, plus the pair the engine tries because neither single
        # moved the answer.
        assert d["singleCounterfactualMs"]["runs"] == 3

    def test_the_total_is_the_sum_of_the_stages(self, monkeypatch):
        state, analyse_fn, pin_fn = wire(monkeypatch, candidates=[cand(1)])
        t = sensitivityrun.run("snap", 234872, analyse_fn=analyse_fn,
                               pin_fn=pin_fn).timing
        assert t.total_ms == (t.canonical_ms + t.candidates_ms
                              + t.extract_validate_ms
                              + sum(t.counterfactual_ms) + t.cleanup_ms)


class TestTheAssumptionIsNamedInTheOutput:
    def test_the_block_carries_the_crossing_and_what_changed(self,
                                                             monkeypatch):
        state, analyse_fn, pin_fn = wire(
            monkeypatch, candidates=[cand(1)],
            cf_pin_by_crossing={1: BETTER_PIN})
        out = sensitivityrun.run("snap", 234872, analyse_fn=analyse_fn,
                                 pin_fn=pin_fn).as_dict()
        cf = [c for c in out["counterfactuals"]
              if c["individuallyChangesAnswer"]][0]
        assert cf["assumedJunctions"][0]["label"] == "Road 1A x Road 1B"
        assert cf["whatChanged"]
        assert out["headline"].startswith("Topology-sensitive.")
        assert out["candidateSearch"]["bySource"]["corridor"] == 1


class TestAnUntestedCandidateIsNeverReportedAsNonMaterial:
    """The fail-closed bug, from every angle.

    `_crossing_links` returning None used to make the runner return the
    canonical answer, so the candidate came out individuallyChangesAnswer
    False and was reported non-material - having never been assumed and never
    routed. Three outcomes must stay distinguishable:

        tested and unchanged                 -> genuinely non-material
        excluded by a tested relevance rule  -> explicitly excluded
        could not be represented or run      -> UNTESTED, never False
    """

    def _wire_missing(self, monkeypatch, missing_side):
        state, analyse_fn, pin_fn = wire(monkeypatch,
                                         candidates=[cand(1), cand(2)])

        def _links(sid, c):
            if c.crossing_id == 1:
                return (None, 11) if missing_side == "a" else (10, None)
            return (c.crossing_id * 10, c.crossing_id * 10 + 1)

        monkeypatch.setattr(sensitivityrun, "_crossing_links", _links)
        return state, analyse_fn, pin_fn

    def test_both_links_found_runs_normally(self, monkeypatch):
        state, analyse_fn, pin_fn = wire(monkeypatch, candidates=[cand(1)])
        out = sensitivityrun.run("snap", 234872, analyse_fn=analyse_fn,
                                 pin_fn=pin_fn).as_dict()
        assert out["analysisComplete"] is True
        assert out["untestedCrossingIds"] == []
        assert out["counterfactuals"][0]["tested"] is True

    @pytest.mark.parametrize("side", ["a", "b"])
    def test_a_missing_source_link_is_untested_not_no_effect(self, monkeypatch,
                                                             side):
        state, analyse_fn, pin_fn = self._wire_missing(monkeypatch, side)
        out = sensitivityrun.run("snap", 234872, analyse_fn=analyse_fn,
                                 pin_fn=pin_fn).as_dict()
        assert 1 in out["untestedCrossingIds"]
        assert 1 not in out["materialCrossingIds"]
        cf = [c for c in out["counterfactuals"] if c["assumedJunctionCrossingIds"] == [1]][0]
        assert cf["tested"] is False
        # The critical assertion: it must NOT read as a tested negative.
        assert cf["individuallyChangesAnswer"] is not False
        assert cf["individuallyChangesAnswer"] is None

    @pytest.mark.parametrize("side", ["a", "b"])
    def test_the_reason_names_which_side_was_missing(self, monkeypatch, side):
        state, analyse_fn, pin_fn = self._wire_missing(monkeypatch, side)
        out = sensitivityrun.run("snap", 234872, analyse_fn=analyse_fn,
                                 pin_fn=pin_fn).as_dict()
        cf = [c for c in out["counterfactuals"] if c["assumedJunctionCrossingIds"] == [1]][0]
        want = "source A" if side == "a" else "source B"
        assert want in cf["untestedReason"]
        d = cf["untestedDetail"]
        assert d["crossingId"] == 1 and d["boundedSnapshotId"]
        assert d["sourceA"] and d["sourceB"] and "x" in d

    def test_the_analysis_is_marked_partial(self, monkeypatch):
        state, analyse_fn, pin_fn = self._wire_missing(monkeypatch, "a")
        out = sensitivityrun.run("snap", 234872, analyse_fn=analyse_fn,
                                 pin_fn=pin_fn).as_dict()
        assert out["analysisComplete"] is False
        assert out["analysisPartial"] is True
        assert "not non-material" in out["ifPartial"].lower()

    def test_the_other_candidates_are_still_tested(self, monkeypatch):
        """One unrepresentable candidate must not cost the whole analysis."""
        state, analyse_fn, pin_fn = self._wire_missing(monkeypatch, "a")
        out = sensitivityrun.run("snap", 234872, analyse_fn=analyse_fn,
                                 pin_fn=pin_fn).as_dict()
        other = [c for c in out["counterfactuals"]
                 if c["assumedJunctionCrossingIds"] == [2]][0]
        assert other["tested"] is True

    def test_every_transient_copy_is_still_dropped(self, monkeypatch):
        """The failing path must not leak a snapshot."""
        state, analyse_fn, pin_fn = self._wire_missing(monkeypatch, "a")
        sensitivityrun.run("snap", 234872, analyse_fn=analyse_fn,
                           pin_fn=pin_fn)
        assert set(state["copies"]) <= set(state["dropped"])
        assert "cf-nb" in state["dropped"]

    def test_a_candidate_the_extraction_omitted_is_untested(self, monkeypatch):
        """Both sides missing - the bounded copy simply does not hold it."""
        state, analyse_fn, pin_fn = wire(monkeypatch, candidates=[cand(1)])
        monkeypatch.setattr(sensitivityrun, "_crossing_links",
                            lambda sid, c: (None, None))
        out = sensitivityrun.run("snap", 234872, analyse_fn=analyse_fn,
                                 pin_fn=pin_fn).as_dict()
        assert out["untestedCrossingIds"] == [1]
        assert out["materialCrossingIds"] == []
        assert out["analysisComplete"] is False

    def test_untested_does_not_make_the_answer_look_robust(self, monkeypatch):
        """topologySensitive False with an untested candidate is not a
        statement that the answer is robust."""
        state, analyse_fn, pin_fn = wire(monkeypatch, candidates=[cand(1)])
        monkeypatch.setattr(sensitivityrun, "_crossing_links",
                            lambda sid, c: (None, None))
        out = sensitivityrun.run("snap", 234872, analyse_fn=analyse_fn,
                                 pin_fn=pin_fn).as_dict()
        assert out["topologySensitive"] is False
        assert out["analysisComplete"] is False, (
            "a False sensitivity verdict alongside an untested candidate must "
            "be qualified, or it reads as robust")


class TestATestedUnchangedCandidateIsGenuinelyNonMaterial:
    """The other side of the same distinction - the Greendale decoy case."""

    def test_it_is_tested_and_not_material(self, monkeypatch):
        state, analyse_fn, pin_fn = wire(monkeypatch, candidates=[cand(1)])
        out = sensitivityrun.run("snap", 234872, analyse_fn=analyse_fn,
                                 pin_fn=pin_fn).as_dict()
        cf = out["counterfactuals"][0]
        assert cf["tested"] is True
        assert cf["individuallyChangesAnswer"] is False
        assert out["materialCrossingIds"] == []
        assert out["untestedCrossingIds"] == []
        assert out["analysisComplete"] is True
