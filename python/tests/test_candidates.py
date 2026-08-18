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


def fake_db(monkeypatch, by_clause):
    """Answer each source's query from a table keyed on a marker in the SQL."""
    calls: list[str] = []

    def _query(sql, params=None):
        calls.append(sql)
        for marker, rows in by_clause.items():
            if marker in sql:
                return rows
        return []

    monkeypatch.setattr(cand_mod.db, "query", _query)
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
