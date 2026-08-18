"""The Greendale acceptance, through the ORDINARY API.

One integration test, as agreed - not a suite. It exercises the shape the
browser will consume: the canonical answer comes from `/boundary-analysis` and
is untouched, topology sensitivity comes from its own endpoint afterwards, and
the counterfactual never occupies the canonical route slot.

Marked `realdata` because it needs a snapshot with the Darfield network in it.
The default suite deselects it; CI runs it where a snapshot exists.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.realdata

LINK = 234872
CANONICAL_M = 7944.412441731978
COUNTERFACTUAL_M = 4915.5332100612995
CAUSAL = "Clintons Road x McLaughlins Road"
DECOY_A, DECOY_B = "Clintons Road", "Greendale Road"


@pytest.fixture(scope="module")
def client():
    from nzcl import db
    # The national snapshot specifically: synthetic test snapshots are newer
    # and would win on recency, then skip on the link check.
    snap = db.query_one(
        "SELECT snapshot_id FROM network_snapshots "
        " WHERE NOT is_transient AND status='complete' "
        "   AND coverage_kind = 'national' "
        " ORDER BY retrieved_at_utc DESC LIMIT 1")
    if not snap:
        pytest.skip("no complete snapshot ingested")
    if not db.query_one("SELECT 1 AS ok FROM links WHERE snapshot_id=%s "
                        " AND link_id=%s", (snap["snapshot_id"], LINK)):
        pytest.skip("this snapshot does not contain the Darfield fixture")
    os.environ["SNAPSHOT_ID"] = snap["snapshot_id"]
    from nzcl.api import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def canonical(client):
    r = client.get(f"/api/v2/links/{LINK}/boundary-analysis",
                   params={"scope": "segment", "metric": "distance",
                           "vehicle": "car", "geometry": "true"})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module")
def sensitivity(client):
    r = client.get(f"/api/v2/links/{LINK}/topology-sensitivity",
                   params={"scope": "segment", "metric": "distance",
                           "vehicle": "car", "token": "sel-1"})
    assert r.status_code == 200, r.text
    return r.json()


class TestTheCanonicalAnswerIsUnchangedAndComesFirst:
    def test_the_ordinary_request_returns_the_audit_figure(self, canonical):
        principal = canonical.get("principal") or {}
        assert principal.get("replacementDistanceM") == pytest.approx(
            CANONICAL_M, abs=1.0)

    def test_it_does_not_carry_a_counterfactual(self, canonical):
        """Sensitivity is a separate request. The canonical response must not
        quietly contain an assumed route."""
        body = str(canonical)
        assert "counterfactual" not in body.lower()


class TestSensitivityIsSeparateAndNamesTheCrossing:
    def test_it_is_topology_sensitive(self, sensitivity):
        assert sensitivity["available"] is True
        assert sensitivity["state"] == "TOPOLOGY_SENSITIVE"
        assert sensitivity["message"] == "Topology-sensitive"

    def test_the_canonical_answer_is_repeated_unchanged(self, sensitivity):
        assert sensitivity["canonicalAnswer"]["distanceM"] == pytest.approx(
            CANONICAL_M, abs=1.0)
        assert sensitivity["canonicalAnswer"]["isCanonical"] is True

    def test_the_causal_crossing_is_named_and_material(self, sensitivity):
        changed = [c for c in sensitivity["counterfactuals"]
                   if c["individuallyChangesAnswer"]]
        assert changed, "the Greendale crossing must change the answer"
        labels = [j["label"] for c in changed for j in c["assumedJunctions"]]
        assert CAUSAL in labels, labels

    def test_the_counterfactual_matches_the_audit(self, sensitivity):
        changed = [c for c in sensitivity["counterfactuals"]
                   if c["individuallyChangesAnswer"]]
        assert changed[0]["distanceM"] == pytest.approx(
            COUNTERFACTUAL_M, abs=1.0)

    def test_the_headline_names_the_crossing_and_both_numbers(self,
                                                              sensitivity):
        h = sensitivity["headline"]
        assert h.startswith("Topology-sensitive.")
        assert CAUSAL in h and "7944" in h and "4916" in h


class TestTheDecoyIsNotCreditedWithTheChange:
    def test_greendale_x_clintons_never_appears_as_the_cause(self,
                                                             sensitivity):
        """It is a genuine missing junction, and connecting it alone leaves
        the route unchanged. It must not be named as the reason."""
        changed = [c for c in sensitivity["counterfactuals"]
                   if c["individuallyChangesAnswer"]]
        for c in changed:
            for j in c["assumedJunctions"]:
                label = j["label"] or ""
                assert not (DECOY_A in label and DECOY_B in label), label

    def test_it_is_either_tested_unchanged_or_excluded_by_the_rule(
            self, sensitivity):
        labels = [j["label"] or "" for c in sensitivity["counterfactuals"]
                  for j in c["assumedJunctions"]]
        decoy = [l for l in labels if DECOY_A in l and DECOY_B in l]
        if decoy:
            cf = [c for c in sensitivity["counterfactuals"]
                  if any(DECOY_A in (j["label"] or "")
                         and DECOY_B in (j["label"] or "")
                         for j in c["assumedJunctions"])][0]
            assert cf["individuallyChangesAnswer"] is False
            assert cf["tested"] is True
        else:
            # Excluded by the relevance rule, which is pinned by tests in
            # test_candidates.py: 654 m from the corridor against a 250 m
            # radius, 1,686 m from the closure against 600 m, outside the hull.
            assert sensitivity["candidateSearch"]["candidates"] >= 1


class TestTheContractTheBrowserRelies0n:
    def test_the_candidate_source_is_declared(self, sensitivity):
        assert sensitivity["candidateSearch"]["candidateSource"] in (
            "PRECOMPUTED_CATALOGUE", "ON_DEMAND_NEIGHBOURHOOD", "UNAVAILABLE")
        assert "candidateSourceComplete" in sensitivity["candidateSearch"]

    def test_the_token_is_echoed_so_a_stale_answer_can_be_discarded(
            self, sensitivity):
        assert sensitivity["token"] == "sel-1"

    def test_the_counterfactual_is_never_the_canonical_route(self,
                                                             sensitivity):
        assert sensitivity["isSeparateFromCanonical"] is True
        assert "NEVER returned here" in sensitivity["canonicalRouteSlot"]
        assert all(c["isCanonical"] is False
                   for c in sensitivity["counterfactuals"])

    def test_there_is_no_v1_fallback(self, sensitivity):
        assert sensitivity["comparableToV1"] is False

    def test_at_most_three_candidates_are_tested(self, sensitivity):
        assert sensitivity["testedCandidates"] <= 3
        assert sensitivity["candidateCap"] == 3

    def test_the_timings_are_reported_per_stage(self, sensitivity):
        t = sensitivity["timing"]
        for k in ("canonicalMs", "candidateSearchMs",
                  "neighbourhoodExtractAndValidateMs",
                  "singleCounterfactualMs", "cleanupMs", "totalMs"):
            assert k in t


class TestNoTransientSnapshotSurvivesTheRequest:
    def test_the_database_is_clean_afterwards(self, sensitivity):
        from nzcl import neighbourhood
        assert neighbourhood.transient_snapshots() == []
