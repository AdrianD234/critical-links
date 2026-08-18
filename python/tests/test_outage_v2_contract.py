"""The span's canonical result, on the final V2 product's terms.

V1 is retired from the product. These tests assert the span engine says so
explicitly rather than merely not calling it, and that everything a reader
would need to place the figures - engine, algorithm, versions, stability, the
processing version the SNAPSHOT was built with - travels with them.

The two that matter most are negative:

  - a route the engine has WITHHELD is never drawn. A withheld route still has
    arc ids, and drawing them would put a replacement path on the map that the
    engine has just refused to offer.

  - a corridor cannot be invented by the client. The only corridors reachable
    are ones this engine generated for these handles; anything else is refused
    rather than substituted.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nzcl import api_outage, config, outage, routing, vsplit
from nzcl.outage import HandleRef
from nzcl.vsplit import LinkInterval

from conftest import requires_db

pytestmark = requires_db


MAIN_BYPASS = [
    {"id": "MAIN", "pts": [(0, 0), (400, 0)], "road_name": "Main Road"},
    {"id": "BYPASS", "pts": [(0, 0), (0, -200), (400, -200), (400, 0)],
     "road_name": "Bypass Road"},
]

#: The detour A -> B runs MAIN then BYPASS, and that manoeuvre is banned, so
#: the route is withheld across the split link.
RESTRICTED = [
    {"id": "MAIN", "pts": [(0, 0), (400, 0)], "road_name": "Main Road"},
    {"id": "BYPASS", "pts": [(0, 0), (0, -200), (400, -200), (400, 0)],
     "road_name": "Bypass Road"},
]


def client(net) -> TestClient:
    app = FastAPI()
    app.include_router(api_outage.router)
    app.dependency_overrides[api_outage.active_snapshot] = \
        lambda: net.snapshot_id
    return TestClient(app)


def analyse(net, a=0.25, b=0.5, **kw):
    link = net.link_id("MAIN")
    return outage.analyse(net.snapshot_id, HandleRef(link, a),
                          HandleRef(link, b), **kw)


class TestTheCanonicalResultCarriesItsProvenance:

    def test_engine_algorithm_and_versions_are_explicit(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        body = outage.as_dict(analyse(net))

        assert body["engine"] == "v2-outage-span"
        assert body["algorithm"] == outage.ALGORITHM
        assert body["algorithmVersion"] == outage.ANALYSIS_VERSION
        assert body["snapshotId"] == net.snapshot_id

    def test_the_processing_version_is_the_snapshots_own(self, synthetic):
        """Not the checkout's. They differ whenever a graph-shaping change has
        landed but the snapshot predates it, and that is a fact about the
        answer rather than about the code."""
        net = synthetic(MAIN_BYPASS)
        body = outage.as_dict(analyse(net))

        from nzcl import db
        recorded = db.query_one(
            "SELECT processing_version FROM network_snapshots WHERE "
            "snapshot_id=%s", (net.snapshot_id,))["processing_version"]

        assert body["processingVersion"] == recorded
        assert body["codeProcessingVersion"] == config.PROCESSING_VERSION

    def test_stability_does_not_borrow_the_production_sentence(self, synthetic):
        """`config.ENGINE_STABILITY` now says "production", which is true of
        the closure engine and false of this one. Reusing it would tell a
        reader these figures are the product's answer."""
        net = synthetic(MAIN_BYPASS)
        body = outage.as_dict(analyse(net))

        assert body["stability"] == outage.STABILITY
        assert body["stability"] != config.ENGINE_STABILITY
        assert "foundation" in body["stability"]
        # It still carries the closure engine's outstanding caveat.
        assert "post-validated" in body["stability"]


class TestV1IsNeitherCalledNorAvailable:

    def test_the_payload_states_it_plainly(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        body = outage.as_dict(analyse(net))

        assert body["comparableToV1"] is False
        assert "no fallback" in body["comparableToV1Detail"].lower()

    def test_nothing_in_the_span_engine_imports_the_v1_engine(self):
        """A fallback that cannot be reached is still a fallback someone will
        reach for later. There is no import to reach through."""
        import inspect

        from nzcl import api_outage, outage, span_corridor, vsplit

        for module in (outage, span_corridor, vsplit, api_outage):
            source = inspect.getsource(module)
            assert "import detour" not in source, module.__name__
            assert "from .detour import" not in source, module.__name__


class TestUnresolvedStaysUnresolved:

    def test_a_withheld_route_reports_unresolved_not_disconnected(
            self, synthetic):
        net = synthetic(RESTRICTED, [{"seq": ["MAIN", "BYPASS"]}])
        result = analyse(net)

        statuses = {m.status for m in result.measures}
        assert statuses == {"TURN_RESTRICTION_UNSUPPORTED"}
        assert all(not m.resolved for m in result.measures)
        assert result.headline == "Analysis unresolved"

    def test_the_status_is_in_the_shared_unresolved_vocabulary(self):
        """Same set the production engine uses, so a client that classifies
        statuses does not need a span-specific rule."""
        from nzcl import detourv2, replacement

        assert "TURN_RESTRICTION_UNSUPPORTED" in outage.UNRESOLVED_STATUSES
        assert "TURN_RESTRICTION_UNSUPPORTED" in detourv2.UNRESOLVED_STATUSES
        assert "TURN_RESTRICTION_UNSUPPORTED" in replacement.UNRESOLVED_STATUSES

    def test_no_distance_is_offered_for_a_withheld_route(self, synthetic):
        net = synthetic(RESTRICTED, [{"seq": ["MAIN", "BYPASS"]}])
        body = outage.as_dict(analyse(net))

        for m in body["measures"]:
            assert m["replacementDistanceM"] is None
            assert m["addedDistanceM"] is None
            assert m["ratio"] is None


class TestAWithheldRouteIsNeverDrawn:
    """The strongest negative in this file."""

    def test_the_arcs_exist_but_no_geometry_is_returned(self, synthetic):
        net = synthetic(RESTRICTED, [{"seq": ["MAIN", "BYPASS"]}])
        link = net.link_id("MAIN")
        params = {"aLink": link, "aFraction": 0.25, "bLink": link,
                  "bFraction": 0.5, "geometry": "true"}

        body = client(net).get("/api/v2/outage/analysis", params=params).json()

        assert body["headline"] == "Analysis unresolved"
        # Nothing to draw, because nothing resolved.
        assert body["replacementGeometry"] == {}

    def test_a_resolved_route_still_is_drawn(self, synthetic):
        """Guards the test above: if geometry were broken outright it would
        also pass."""
        net = synthetic(MAIN_BYPASS)
        link = net.link_id("MAIN")
        params = {"aLink": link, "aFraction": 0.25, "bLink": link,
                  "bFraction": 0.5, "geometry": "true"}

        body = client(net).get("/api/v2/outage/analysis", params=params).json()

        assert body["replacementGeometry"]["a_to_b"]["features"]


class TestTheClientCannotInventACorridor:

    def test_an_unknown_corridor_id_is_refused(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        link = net.link_id("MAIN")
        r = client(net).get("/api/v2/outage/analysis", params={
            "aLink": link, "aFraction": 0.25, "bLink": link,
            "bFraction": 0.5, "corridorId": "deadbeef" * 4})

        assert r.status_code == 409

    def test_a_corridor_from_a_different_span_is_refused(self, synthetic):
        """The tamper that would otherwise work: a real, engine-generated id,
        replayed against handles it does not belong to."""
        net = synthetic(MAIN_BYPASS)
        link = net.link_id("MAIN")
        elsewhere = analyse(net, 0.6, 0.9).corridor.candidate_id

        r = client(net).get("/api/v2/outage/analysis", params={
            "aLink": link, "aFraction": 0.25, "bLink": link,
            "bFraction": 0.5, "corridorId": elsewhere})

        assert r.status_code == 409

    def test_the_closure_is_never_taken_from_the_request(self, synthetic):
        """A client sends two positions. Everything closed is derived here
        from the graph - there is no parameter that names a link to close."""
        import inspect

        source = inspect.getsource(api_outage.analysis)
        for invented in ("closeLinks", "linkIds", "intervals", "arcIds"):
            assert invented not in source


class TestTheSensitivitySeam:
    """Unavailable, explicit, and never softened into robustness."""

    def test_sensitivity_is_null_with_a_reason(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        body = outage.as_dict(analyse(net))

        assert body["sensitivity"] is None
        assert body["sensitivityUnavailableReason"]

    def test_it_disclaims_the_robustness_reading_in_words(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        reason = outage.as_dict(analyse(net))["sensitivityUnavailableReason"]

        assert "not implemented" in reason
        # The disclaimer is asserted verbatim. "No sensitivity reported" and
        # "not sensitive" are opposite claims, and the difference has to be on
        # the screen rather than inferred from an absent field.
        assert "NOT a finding that the span is topology-robust" in reason
        assert "the question has not been asked" in reason

    def test_no_field_can_be_misread_as_a_robustness_verdict(self, synthetic):
        """The failure mode a reason string cannot prevent on its own: a
        client looking for a boolean, finding a falsy one, and rendering
        "not sensitive". There must be no such field to find."""
        net = synthetic(MAIN_BYPASS)
        body = outage.as_dict(analyse(net))

        assert body["sensitivity"] is None
        for key, value in body.items():
            if "sensitiv" in key.lower() and key != "sensitivity":
                # Only the reason string, and it must not be falsy either.
                assert isinstance(value, str) and value, key
        assert not any(k.lower().startswith("topologyrobust") for k in body)

    def test_the_canonical_answer_is_marked_separate(self, synthetic):
        """Same structural promise the production sensitivity endpoint makes:
        no counterfactual is folded into the result a client draws."""
        net = synthetic(MAIN_BYPASS)
        body = outage.as_dict(analyse(net))

        assert body["isSeparateFromCanonical"] is True
        assert "No counterfactual route" in body["canonicalRouteSlot"]

    def test_this_branch_does_not_reimplement_the_sensitivity_engine(self):
        import inspect

        from nzcl import outage as outage_mod
        from nzcl import span_corridor, vsplit

        for module in (outage_mod, span_corridor, vsplit):
            source = inspect.getsource(module)
            assert "sensitivityrun" not in source, module.__name__
            assert "import sensitivity" not in source, module.__name__


class TestProvenanceTravelsWithTheFigures:

    def test_the_analysis_endpoint_carries_attribution_and_limitations(
            self, synthetic):
        net = synthetic(MAIN_BYPASS)
        link = net.link_id("MAIN")
        body = client(net).get("/api/v2/outage/analysis", params={
            "aLink": link, "aFraction": 0.25, "bLink": link,
            "bFraction": 0.5}).json()

        assert body["attribution"]
        assert isinstance(body["limitations"], list) and body["limitations"]

    def test_the_corridor_endpoint_carries_them_too(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        link = net.link_id("MAIN")
        body = client(net).get("/api/v2/outage/corridor", params={
            "aLink": link, "aFraction": 0.25, "bLink": link,
            "bFraction": 0.5}).json()

        assert body["attribution"]
        assert body["limitations"]
