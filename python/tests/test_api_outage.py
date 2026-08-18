"""Contract tests for the outage-span HTTP surface.

The router is mounted on a bare application with the snapshot dependency
overridden, rather than on `nzcl.api.app`. That keeps these tests off the
application's lifespan, which picks a snapshot from whatever happens to be in
the database - so a contract test would otherwise be asserting against an
arbitrary network.

The fixture is the same arithmetic as `test_outage.py`: 100 m of Main Road
shut, 1100 m round.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nzcl import api_outage, geo

from conftest import requires_db

pytestmark = requires_db


MAIN_BYPASS = [
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


def span_params(net, a=0.25, b=0.5):
    link = net.link_id("MAIN")
    return {"aLink": link, "aFraction": a, "bLink": link, "bFraction": b}


class TestSnapEndpoint:

    def test_a_click_returns_a_linear_reference(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        r = client(net).get("/api/v2/outage/snap",
                            params={"x": 250.0, "y": 30.0})

        assert r.status_code == 200
        body = r.json()
        assert body["found"] is True
        assert body["handle"]["distanceAlongM"] == pytest.approx(250.0, abs=1e-3)
        assert body["handle"]["fraction"] == pytest.approx(0.625, abs=1e-9)
        assert body["handle"]["offsetM"] == pytest.approx(30.0, abs=1e-3)

    def test_lon_lat_and_nztm_agree(self, synthetic):
        """The map speaks WGS84 and everything measures in NZTM metres."""
        net = synthetic(MAIN_BYPASS)
        c = client(net)
        lon, lat = geo.nztm_to_lonlat(250.0, 30.0)

        projected = c.get("/api/v2/outage/snap",
                          params={"x": 250.0, "y": 30.0}).json()
        geographic = c.get("/api/v2/outage/snap",
                           params={"lon": lon, "lat": lat}).json()

        assert geographic["handle"]["linkId"] == projected["handle"]["linkId"]
        assert geographic["handle"]["distanceAlongM"] == pytest.approx(
            projected["handle"]["distanceAlongM"], abs=0.01)

    def test_neither_frame_is_refused(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        r = client(net).get("/api/v2/outage/snap", params={"x": 250.0})

        assert r.status_code == 422
        assert "EPSG:2193" in r.json()["detail"]

    def test_an_absurd_radius_is_refused_by_validation(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        r = client(net).get("/api/v2/outage/snap",
                            params={"x": 0, "y": 0, "radius": 1e9})

        assert r.status_code == 422

    def test_the_two_kinds_of_rival_are_reported_separately(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        r = client(net).get("/api/v2/outage/snap",
                            params={"x": 250.0, "y": 30.0}).json()

        assert "equivalentHosts" in r
        assert "alternatives" in r
        assert "hostLinkIds" in r


class TestCorridorEndpoint:

    def test_it_returns_the_corridor_and_its_evidence(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        r = client(net).get("/api/v2/outage/corridor",
                            params=span_params(net))

        assert r.status_code == 200
        body = r.json()
        assert body["found"] is True
        assert body["corridor"]["roads"] == "Main Road"
        assert body["corridor"]["lengthM"] == pytest.approx(100.0, abs=0.1)
        assert body["ambiguous"] is False
        assert body["snapshotId"] == net.snapshot_id

    def test_an_unknown_link_is_a_404(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        r = client(net).get("/api/v2/outage/corridor",
                            params={"aLink": 999999, "aFraction": 0.2,
                                    "bLink": 999999, "bFraction": 0.8})

        assert r.status_code == 404

    def test_a_fraction_outside_the_link_is_refused(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        params = span_params(net)
        params["aFraction"] = 1.5
        r = client(net).get("/api/v2/outage/corridor", params=params)

        assert r.status_code == 422

    def test_a_malformed_alternate_is_refused(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        params = span_params(net)
        params["aAlt"] = "not-a-pair"
        r = client(net).get("/api/v2/outage/corridor", params=params)

        assert r.status_code == 422
        assert "linkId:fraction" in r.json()["detail"]


class TestAnalysisEndpoint:

    def test_the_known_answer(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        r = client(net).get("/api/v2/outage/analysis",
                            params=span_params(net))

        assert r.status_code == 200
        body = r.json()
        assert body["closedLengthM"] == pytest.approx(100.0, abs=0.1)
        assert body["headline"] == "Replacement route found"
        forward = next(m for m in body["measures"] if m["direction"] == "a_to_b")
        assert forward["replacementDistanceM"] == pytest.approx(1100.0, abs=0.1)
        assert forward["addedDistanceM"] == pytest.approx(1000.0, abs=0.1)
        assert forward["ratio"] == pytest.approx(11.0, abs=0.001)

    def test_it_carries_the_caveat_and_the_isolation_reason(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        body = client(net).get("/api/v2/outage/analysis",
                               params=span_params(net)).json()

        assert "says nothing about how much traffic" in body["measurementCaveat"]
        assert body["isolation"] is None
        assert body["isolationUnavailableReason"]

    def test_a_contraflow_measures_one_direction(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        params = span_params(net) | {"direction": "a_to_b"}
        body = client(net).get("/api/v2/outage/analysis", params=params).json()

        assert len(body["measures"]) == 1
        assert body["measures"][0]["direction"] == "a_to_b"

    def test_geometry_is_off_unless_asked_for(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        body = client(net).get("/api/v2/outage/analysis",
                               params=span_params(net)).json()

        assert "closureGeometry" not in body

    def test_geometry_draws_the_closure_and_the_replacement(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        params = span_params(net) | {"geometry": "true"}
        body = client(net).get("/api/v2/outage/analysis", params=params).json()

        assert body["closureGeometry"]["measuredLengthM"] == pytest.approx(
            body["closedLengthM"], abs=0.001)
        drawn = body["replacementGeometry"]["a_to_b"]["features"]
        assert drawn
        # The partial links at either end are drawn too, or the detour would
        # start in mid air.
        assert any(f["properties"]["virtual"] for f in drawn)

    def test_an_unknown_pinned_corridor_is_a_conflict(self, synthetic):
        """409 rather than a quiet substitution: reopening a shared span onto a
        different road would be worse than failing to open it."""
        net = synthetic(MAIN_BYPASS)
        params = span_params(net) | {"corridorId": "nope"}
        r = client(net).get("/api/v2/outage/analysis", params=params)

        assert r.status_code == 409
        assert "not among the candidates" in r.json()["detail"]

    def test_two_handles_at_one_measure_are_refused(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        r = client(net).get("/api/v2/outage/analysis",
                            params=span_params(net, 0.4, 0.4))

        assert r.status_code == 422


class TestThePermalinkRoundTrips:
    """A shared span must reopen as the same span, over HTTP."""

    def test_the_response_carries_everything_needed_to_restore_it(
            self, synthetic):
        net = synthetic(MAIN_BYPASS)
        c = client(net)
        first = c.get("/api/v2/outage/analysis", params=span_params(net)).json()

        state = first["permalink"]
        restored = c.get("/api/v2/outage/analysis", params={
            "aLink": state["aLinkId"], "aFraction": state["aFraction"],
            "bLink": state["bLinkId"], "bFraction": state["bFraction"],
            "corridorId": state["corridorId"],
            "direction": state["directionMode"],
            "vehicle": state["profile"], "metric": state["metric"],
        }).json()

        assert restored["fingerprint"] == first["fingerprint"]
        assert restored["corridor"]["candidateId"] == \
            first["corridor"]["candidateId"]
        assert restored["closedLengthM"] == first["closedLengthM"]

    def test_the_permalink_stores_a_position_not_a_click(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        body = client(net).get("/api/v2/outage/analysis",
                               params=span_params(net)).json()

        assert body["permalink"]["aFraction"] == pytest.approx(0.25)
        assert "x" not in body["permalink"]
        assert "lon" not in body["permalink"]


class TestTheFeatureFlag:
    """Off by default: a deployment that has not opted in is unchanged."""

    def test_the_router_is_not_mounted_by_default(self):
        from nzcl import api

        outage_routes = [r for r in api.app.routes
                         if getattr(r, "path", "").startswith("/api/v2/outage")]
        assert outage_routes == []

    def test_the_setting_defaults_to_off(self):
        from nzcl.config import Settings

        assert Settings().enable_outage_span_api is False
