"""Candidate search: corridor-derived, not a buffer round the clicked link.

The case that defines the requirement: the crossing that causes the Greendale
result is Clintons Road x McLaughlins Road, ~2.8 km from the closed link and
ON the canonical detour. The crossing beside the closure - Clintons x
Greendale - is a genuine missing junction and connecting it alone leaves the
route unchanged at 7,944.4 m.

So a buffer finds the wrong one and misses the right one. These tests pin the
search to the evidence instead: closure, ports, canonical corridor, and the
interior of the circuit the detour encloses.
"""

from __future__ import annotations

import pytest

from nzcl import candidates as cand_mod
from nzcl.candidates import MAX_CANDIDATES, CandidateSearch, find
from nzcl.sensitivity import Candidate


def fake_db(monkeypatch, by_clause, catalogue=999):
    """Answer each source's query from a table keyed on a marker in the SQL.

    `catalogue` is the row count `catalogue_rows` reports. It defaults to a
    non-zero value so these tests exercise the PRECOMPUTED_CATALOGUE path;
    pass 0 to exercise on-demand detection.
    """
    calls: list[str] = []

    def _query(sql, params=None):
        calls.append(sql)
        for marker, rows in by_clause.items():
            if marker in sql:
                return rows
        return []

    def _query_one(sql, params=None):
        if "count(*)" in sql and "crossings" in sql:
            return {"n": catalogue}
        return None

    monkeypatch.setattr(cand_mod.db, "query", _query)
    monkeypatch.setattr(cand_mod.db, "query_one", _query_one)
    return calls


def row(cid, **kw):
    return {"crossing_id": cid, "source_a": f"A{cid}", "source_b": f"B{cid}",
            "x": float(cid), "y": 0.0,
            "disposition": kw.get("disposition", "UNRESOLVED"),
            "reason": kw.get("reason", "NO_EVIDENCE_EITHER_WAY"),
            "confidence": "MEDIUM",
            "name_a": kw.get("name_a"), "name_b": kw.get("name_b")}


class TestTheSearchIsDerivedFromTheAnalysis:
    def test_the_canonical_route_is_searched_along(self, monkeypatch):
        """The source that finds the crossing 2.8 km away."""
        calls = fake_db(monkeypatch, {
            "link_id = ANY(%(closure)s)": [row(1)],
            "link_id = ANY(%(route)s)": [row(99, name_a="Clintons Road",
                                             name_b="McLaughlins Road")],
        })
        s = find("snap", closure_link_ids=[234872],
                 route_link_ids=[232709, 234053, 111])
        ids = {c.crossing_id for c in s.candidates}
        assert 99 in ids, "the corridor source must be searched"
        assert s.by_source["corridor"] == 1
        assert any("route" in c for c in calls)

    def test_the_causal_crossing_is_found_even_though_it_is_far_away(
            self, monkeypatch):
        """Distance from the closure is not what makes a crossing matter."""
        fake_db(monkeypatch, {
            "link_id = ANY(%(closure)s)": [row(1, name_a="Clintons Road",
                                               name_b="Greendale Road")],
            "link_id = ANY(%(route)s)": [row(99, name_a="Clintons Road",
                                             name_b="McLaughlins Road")],
        })
        s = find("snap", closure_link_ids=[234872],
                 route_link_ids=[232709, 234053])
        by_id = {c.crossing_id: c for c in s.candidates}
        assert by_id[99].label == "Clintons Road x McLaughlins Road"
        assert by_id[1].label == "Clintons Road x Greendale Road"

    def test_the_interior_of_the_circuit_is_searched(self, monkeypatch):
        fake_db(monkeypatch, {"ST_ConvexHull": [row(42)]})
        s = find("snap", closure_link_ids=[1], route_link_ids=[2, 3])
        assert 42 in {c.crossing_id for c in s.candidates}
        assert s.by_source["inside_circuit"] == 1

    def test_the_ports_are_searched_when_given(self, monkeypatch):
        fake_db(monkeypatch, {"node_id = ANY(%(ports)s)": [row(7)]})
        s = find("snap", closure_link_ids=[1], port_node_ids=[10, 20])
        assert 7 in {c.crossing_id for c in s.candidates}
        assert "ports" in s.sources_used

    def test_all_four_sources_are_used(self, monkeypatch):
        fake_db(monkeypatch, {
            "link_id = ANY(%(closure)s)": [row(1)],
            "link_id = ANY(%(route)s)": [row(2)],
            "node_id = ANY(%(ports)s)": [row(3)],
            "ST_ConvexHull": [row(4)],
        })
        s = find("snap", closure_link_ids=[1], route_link_ids=[2],
                 port_node_ids=[3])
        assert {c.crossing_id for c in s.candidates} == {1, 2, 3, 4}
        assert s.sources_used == list(cand_mod.SOURCES)

    def test_a_crossing_found_twice_is_attributed_to_the_first_source(
            self, monkeypatch):
        fake_db(monkeypatch, {
            "link_id = ANY(%(closure)s)": [row(5)],
            "link_id = ANY(%(route)s)": [row(5)],
        })
        s = find("snap", closure_link_ids=[1], route_link_ids=[2])
        assert len(s.candidates) == 1
        assert s.by_source["closure"] == 1 and s.by_source["corridor"] == 0


class TestOnlyRealCandidatesAreOffered:
    def test_already_noded_and_unrepresentable_crossings_are_excluded(self):
        """In the SQL, so the exclusion cannot be forgotten by a caller."""
        assert "NOT c.noded" in cand_mod._SELECT
        assert "c.safe_to_node" in cand_mod._SELECT

    def test_the_exclusion_is_explained_where_it_is_written(self):
        assert "unrepresentable" in cand_mod._SELECT


class TestTheBoundIsReported:
    """A search that quietly returns the first N can miss the causal crossing
    and report a confident 'not topology-sensitive'."""

    def test_truncation_is_flagged(self, monkeypatch):
        fake_db(monkeypatch, {
            "link_id = ANY(%(closure)s)": [row(i) for i in range(200)]})
        s = find("snap", closure_link_ids=[1])
        assert s.truncated is True
        assert len(s.candidates) == MAX_CANDIDATES
        assert s.considered == 200

    def test_the_report_warns_that_robust_cannot_be_concluded(self,
                                                              monkeypatch):
        fake_db(monkeypatch, {
            "link_id = ANY(%(closure)s)": [row(i) for i in range(200)]})
        d = find("snap", closure_link_ids=[1]).as_dict()
        assert d["truncated"] is True
        assert "would NOT mean this answer is robust" in " ".join(d["notes"])
        assert "does NOT mean the answer is robust" in d["ifTruncated"]

    def test_an_untruncated_search_says_so(self, monkeypatch):
        fake_db(monkeypatch, {"link_id = ANY(%(closure)s)": [row(1)]})
        d = find("snap", closure_link_ids=[1], route_link_ids=[2]).as_dict()
        assert d["truncated"] is False and d["notes"] == []

    def test_a_custom_bound_is_honoured(self, monkeypatch):
        fake_db(monkeypatch, {
            "link_id = ANY(%(closure)s)": [row(i) for i in range(10)]})
        s = find("snap", closure_link_ids=[1], max_candidates=3)
        assert len(s.candidates) == 3 and s.truncated is True


class TestADisconnectedAnswerHasNoCorridor:
    def test_the_narrower_search_is_stated_rather_than_silent(self,
                                                              monkeypatch):
        fake_db(monkeypatch, {"link_id = ANY(%(closure)s)": [row(1)]})
        s = find("snap", closure_link_ids=[1])
        assert "corridor" not in s.by_source
        assert any("narrower than usual" in n for n in s.notes)

    def test_it_still_returns_what_it_can(self, monkeypatch):
        fake_db(monkeypatch, {"link_id = ANY(%(closure)s)": [row(1)]})
        assert len(find("snap", closure_link_ids=[1]).candidates) == 1


class TestTheSearchReportsItsShape:
    def test_the_radii_are_recorded(self, monkeypatch):
        fake_db(monkeypatch, {})
        d = find("snap", closure_link_ids=[1]).as_dict()
        assert d["radii"]["corridorM"] == cand_mod.CORRIDOR_RADIUS_M
        assert d["bound"] == MAX_CANDIDATES

    def test_it_says_why_a_buffer_is_the_wrong_search(self, monkeypatch):
        fake_db(monkeypatch, {})
        assert "2.8 km" in find("snap", closure_link_ids=[1]).as_dict()["why"]


class TestTheNamingGateIsCheckedInBothDirections:
    """A cleared source resolves to its name; an uncleared one resolves to the
    withheld state.

    Both halves, because I got this wrong in both directions at once. I read
    the VIEW DEFINITION, saw a licence gate in the CASE expression, and
    concluded the gate was closed - without querying `name_source_licences` to
    see that `linz_road_sections` has display_cleared = TRUE. Then I diagnosed
    from a stale `link_names` query instead of re-running against the governed
    view, which had been returning the names all along.

    The lesson is narrow and worth a test: check the gate STATE, not the gate
    MECHANISM. `link_names.display_name` holds only the higher tier and is not
    the governed answer; `link_display_names.display_name` is.
    """

    def test_a_cleared_source_resolves_to_its_name(self):
        assert cand_mod._label("Clintons Road", None) == "Clintons Road"

    def test_an_uncleared_source_resolves_to_the_withheld_state(self):
        out = cand_mod._label(None, "nzta_street_names")
        assert "withheld" in out and "nzta_street_names" in out

    def test_a_genuinely_unnamed_road_is_neither(self):
        assert cand_mod._label(None, None) is None

    def test_a_name_wins_even_if_a_withheld_source_is_also_recorded(self):
        assert cand_mod._label("Clintons Road", "nzta_ramm_carriageway") ==             "Clintons Road"

    def test_the_query_reads_the_governed_view_not_the_raw_table(self):
        """`link_names` holds only the higher tier. Reading it directly is the
        bug this test exists to prevent recurring."""
        assert "link_display_names" in cand_mod._SELECT
        assert "LEFT JOIN link_names " not in cand_mod._SELECT

    def test_the_withheld_source_is_selected_so_it_can_be_reported(self):
        assert "withheld_name_source" in cand_mod._SELECT


class TestTheExclusionRuleIsPinned:
    """The decoy is excluded by a RULE, and a defensible rule with no test is
    one refactor away from being an accidental one.

    Measured against the national snapshot: Clintons x Greendale is 1,685.9 m
    from the closed link (radius 600 m), 654.0 m from the canonical route
    (radius 250 m), and outside the circuit hull. The causal crossing is 0.0 m
    from the route and inside the hull.
    """

    def test_the_corridor_radius_excludes_the_decoy_and_admits_the_causal(self):
        assert cand_mod.CORRIDOR_RADIUS_M < 654.0, (
            "widening the corridor past 654 m would admit the Greendale decoy "
            "and this test should fail so that becomes a decision")
        assert cand_mod.CORRIDOR_RADIUS_M > 0.0

    def test_the_closure_radius_excludes_the_decoy(self):
        assert cand_mod.CLOSURE_RADIUS_M < 1685.9

    def test_the_rule_is_the_four_named_sources_and_nothing_else(self):
        """If a fifth relevance source is added, this fails and the exclusion
        has to be re-argued rather than silently changing."""
        assert cand_mod.SOURCES == ("closure", "corridor", "ports",
                                    "inside_circuit")

    def test_a_forced_candidate_is_labelled_as_not_selected_by_the_rule(
            self, monkeypatch):
        fake_db(monkeypatch, {"ST_MakePoint(%(fx)s": [row(77)]})
        s = find("snap", closure_link_ids=[1], force_near=[(1526312.0,
                                                           5181822.6)])
        assert 77 in {c.crossing_id for c in s.candidates}
        assert s.by_source["forced"] == 1
        assert any("not there because the relevance rule selected it" in n
                   for n in s.notes)

    def test_forcing_bypasses_the_bound_because_it_is_an_audit_route(
            self, monkeypatch):
        fake_db(monkeypatch, {
            "link_id = ANY(%(closure)s)": [row(i) for i in range(200)],
            "ST_MakePoint(%(fx)s": [row(999)]})
        s = find("snap", closure_link_ids=[1], force_near=[(0.0, 0.0)])
        assert 999 in {c.crossing_id for c in s.candidates}


class TestAnEmptyCatalogueTriggersOnDemandDetectionNotAFalseZero:
    """The two facts that wear the same number.

    An empty catalogue and a search that ran and found nothing both produce
    zero candidates. Reported the same way, the first reads as "this answer is
    robust" when nothing was looked at. The national snapshot has zero
    crossings rows, so this is the live case, not a hypothetical.
    """

    def test_an_empty_catalogue_runs_on_demand_detection(self, monkeypatch):
        fake_db(monkeypatch, {}, catalogue=0)
        monkeypatch.setattr(
            cand_mod, "detect_on_demand",
            lambda snap, **kw: ([Candidate(crossing_id=-1, source_a="A",
                                           source_b="B", x=0.0, y=0.0)], True))
        s = find("snap", closure_link_ids=[1], route_link_ids=[2])
        assert s.source_kind == cand_mod.ON_DEMAND_NEIGHBOURHOOD
        assert s.source_complete is True
        assert len(s.candidates) == 1

    def test_a_populated_catalogue_is_used_directly(self, monkeypatch):
        fake_db(monkeypatch, {"link_id = ANY(%(closure)s)": [row(1)]},
                catalogue=22062)
        s = find("snap", closure_link_ids=[1], route_link_ids=[2])
        assert s.source_kind == cand_mod.PRECOMPUTED_CATALOGUE
        assert s.source_complete is True
        assert s.catalogue_rows == 22062

    def test_the_source_is_declared_on_every_response(self, monkeypatch):
        fake_db(monkeypatch, {}, catalogue=5)
        d = find("snap", closure_link_ids=[1], route_link_ids=[2]).as_dict()
        assert d["candidateSource"] in (cand_mod.PRECOMPUTED_CATALOGUE,
                                        cand_mod.ON_DEMAND_NEIGHBOURHOOD,
                                        cand_mod.UNAVAILABLE)
        assert "candidateSourceComplete" in d

    def test_nothing_detectable_reports_unavailable_not_robust(self,
                                                               monkeypatch):
        fake_db(monkeypatch, {}, catalogue=0)
        monkeypatch.setattr(cand_mod, "detect_on_demand",
                            lambda snap, **kw: ([], False))
        s = find("snap", closure_link_ids=[1], route_link_ids=[2])
        assert s.source_kind == cand_mod.UNAVAILABLE
        assert s.source_complete is False
        assert "NOT a finding that the answer is" in s.as_dict()["aboutTheSource"]

    def test_the_outcome_states_are_exactly_five(self):
        assert cand_mod.OUTCOMES == (
            "TESTED_CHANGED", "TESTED_UNCHANGED",
            "EXCLUDED_BY_RELEVANCE_RULE",
            "UNTESTED_MATERIALISATION_FAILED", "UNTESTED_CANCELLED")
