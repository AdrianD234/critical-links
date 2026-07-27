"""Vector-tile contract: decode real bytes and check what the client reads.

This exists because a style-specification test gave false confidence. It
validated the STYLE definition and never a tile, so it could not see that the
backend emitted `link_id`/`state_highway` while the client read
`linkId`/`stateHighway`. Every map click resolved undefined and state-highway
styling silently fell back to the default line colour.

These tests decode the actual protobuf and assert the property names the
MapLibre client depends on, then follow a decoded feature through to a detour
result - the full path a user's click takes.
"""

from __future__ import annotations

import pytest

from fastapi.testclient import TestClient

from nzcl import db

from conftest import requires_db

pytestmark = requires_db

mapbox_vector_tile = pytest.importorskip("mapbox_vector_tile")


#: Exactly what apps/web/src/MapView.tsx reads from a tile feature.
CLIENT_READS = {"linkId", "stateHighway", "core", "roadName", "oneway"}


def _pilot_snapshot() -> str | None:
    row = db.query_one(
        "SELECT snapshot_id FROM network_snapshots "
        "WHERE snapshot_id NOT LIKE 'test-%' AND routable_link_count > 1000 "
        "ORDER BY retrieved_at_utc DESC LIMIT 1"
    )
    return row["snapshot_id"] if row else None


@pytest.fixture(scope="module")
def client():
    snapshot = _pilot_snapshot()
    if not snapshot:
        pytest.skip("no ingested snapshot to serve tiles from")
    import os
    os.environ["SNAPSHOT_ID"] = snapshot
    from nzcl.api import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def wellington_tile(client):
    """A tile over central Wellington, decoded."""
    r = client.get("/tiles/12/4036/2564.pbf")
    assert r.status_code == 200, f"expected a tile, got HTTP {r.status_code}"
    assert r.headers["content-type"] == "application/x-protobuf"
    return mapbox_vector_tile.decode(r.content)


class TestTileSchema:
    def test_serves_a_network_layer(self, wellington_tile):
        assert "network" in wellington_tile
        assert len(wellington_tile["network"]["features"]) > 0

    def test_publishes_every_property_the_client_reads(self, wellington_tile):
        props = set(wellington_tile["network"]["features"][0]["properties"])
        missing = CLIENT_READS - props
        assert not missing, (
            f"tile is missing {sorted(missing)}; the client would read undefined. "
            f"present: {sorted(props)}"
        )

    def test_sets_a_feature_id(self, wellington_tile):
        # ST_AsMVT strips the feature-id column from properties, so the id is
        # the robust handle for selection and feature-state.
        for f in wellington_tile["network"]["features"][:20]:
            assert f.get("id") is not None

    def test_feature_id_matches_the_link_id_property(self, wellington_tile):
        # Both must be present AND agree, so either path resolves the same link.
        for f in wellington_tile["network"]["features"][:20]:
            assert int(f["id"]) == int(f["properties"]["linkId"])

    def test_flags_are_numeric_so_style_expressions_compare_correctly(
        self, wellington_tile
    ):
        f = wellington_tile["network"]["features"][0]
        for key in ("oneway", "stateHighway", "lifeline", "core"):
            assert isinstance(f["properties"][key], int), (
                f"{key} must be numeric: the style compares it with =="
            )

    def test_empty_area_returns_204_not_an_empty_tile(self, client):
        # Mid-Pacific: no roads. MapLibre expects 204 for an empty tile.
        r = client.get("/tiles/12/100/100.pbf")
        assert r.status_code == 204


class TestTileToDetourJourney:
    """A decoded tile feature must survive the whole path a click takes."""

    def test_a_tile_feature_resolves_to_a_detour(self, client, wellington_tile):
        feature = next(
            f for f in wellington_tile["network"]["features"]
            if f["properties"].get("roadName")
        )
        link_id = int(feature["id"])

        # The client passes the id straight to the detour endpoint.
        r = client.get(f"/api/v1/links/{link_id}/detour?geometry=false")
        assert r.status_code == 200, r.text
        body = r.json()

        assert body["selectedLink"]["linkId"] == link_id
        assert body["selectedLink"]["roadName"] == feature["properties"]["roadName"]
        assert body["closure"]["removedArcCount"] >= 1
        assert any(body.get(d) for d in ("forward", "reverse"))

    def test_state_highway_flag_agrees_with_the_database(
        self, client, wellington_tile
    ):
        snapshot = _pilot_snapshot()
        for f in wellington_tile["network"]["features"][:40]:
            row = db.query_one(
                "SELECT rca_code FROM links WHERE snapshot_id=%s AND link_id=%s",
                (snapshot, int(f["id"])),
            )
            if row is None:
                continue
            expected = 1 if row["rca_code"] == 1 else 0
            assert f["properties"]["stateHighway"] == expected


class TestBackendIdentity:
    """Two API implementations exist. A response must say which one answered."""

    def test_health_declares_the_implementation(self, client):
        h = client.get("/health").json()
        assert h["implementation"] == "python-fastapi-postgis"
        assert h["database"]["postgis"]
        assert h["database"]["pgrouting"]

    def test_health_reports_versions_needed_to_reproduce_a_result(self, client):
        h = client.get("/health").json()
        for key in ("algorithm", "algorithmVersion", "processingVersion",
                    "tileSchemaVersion", "activeSnapshotId"):
            assert h.get(key) is not None, f"/health is missing {key}"


class TestTileBounds:
    def test_rejects_out_of_range_coordinates(self, client):
        assert client.get("/tiles/2/99/0.pbf").status_code == 400
        assert client.get("/tiles/30/0/0.pbf").status_code == 400
