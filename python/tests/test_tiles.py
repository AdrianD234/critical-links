"""Vector-tile contract: decode real bytes and check what the client reads.

This exists because a style-specification test gave false confidence. It
validated the STYLE definition and never a tile, so it could not see that the
backend emitted `link_id`/`state_highway` while the client read
`linkId`/`stateHighway`. Every map click resolved undefined and state-highway
styling silently fell back to the default line colour.

The core tests build their OWN miniature snapshot at real Wellington
coordinates and compute the tile that must contain it. They do not import-skip
and do not depend on a previously ingested database, because a regression guard
that skips in a clean clone or in CI protects nothing. `mapbox-vector-tile` is
a required dev dependency for exactly that reason.

One optional test at the end additionally checks a real ingested snapshot.
"""

from __future__ import annotations

import math

import mapbox_vector_tile
import pytest
from fastapi.testclient import TestClient

from nzcl import db

from conftest import requires_db

pytestmark = requires_db


#: Exactly what apps/web/src/MapView.tsx reads from a tile feature.
CLIENT_READS = {"linkId", "stateHighway", "core", "roadName", "oneway"}

#: Central Wellington, in NZTM2000 metres. Real coordinates so the fixture
#: lands in a real tile rather than off the coast of Africa.
WGTN_X, WGTN_Y = 1749100.0, 5428100.0


def lonlat_to_tile(lon: float, lat: float, z: int) -> tuple[int, int]:
    """Slippy-map tile containing a coordinate."""
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


@pytest.fixture(scope="module")
def tile_fixture():
    """A deterministic two-link snapshot with known attributes."""
    from nzcl.fixtures import load_synthetic

    net = load_synthetic([
        # A state highway and an ordinary road, meeting end to end, so the
        # stateHighway flag has both values to distinguish.
        {"id": "SH-TEST", "pts": [(WGTN_X, WGTN_Y), (WGTN_X + 400, WGTN_Y)],
         "road_name": "Test State Highway"},
        {"id": "LOCAL-TEST", "pts": [(WGTN_X + 400, WGTN_Y), (WGTN_X + 800, WGTN_Y)],
         "road_name": "Test Local Road", "oneway": True},
    ])
    # Mark one as a state highway and both as in-area, so the tile flags differ.
    db.execute(
        "UPDATE links SET rca_code = 1, in_analysis_area = true "
        "WHERE snapshot_id = %s AND amds_id = 'SH-TEST'", (net.snapshot_id,))
    db.execute(
        "UPDATE links SET rca_code = 69, in_analysis_area = true "
        "WHERE snapshot_id = %s AND amds_id = 'LOCAL-TEST'", (net.snapshot_id,))
    try:
        yield net
    finally:
        db.execute("DELETE FROM network_snapshots WHERE snapshot_id=%s",
                   (net.snapshot_id,))


@pytest.fixture(scope="module")
def client(tile_fixture):
    import os
    os.environ["SNAPSHOT_ID"] = tile_fixture.snapshot_id
    from nzcl.api import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def decoded(client, tile_fixture):
    """The tile containing the fixture, decoded."""
    from nzcl.geo import nztm_to_lonlat
    from nzcl.api import TILE_SCHEMA_VERSION

    lon, lat = nztm_to_lonlat(WGTN_X + 400, WGTN_Y)
    z = 14
    x, y = lonlat_to_tile(lon, lat, z)
    r = client.get(
        f"/tiles/v{TILE_SCHEMA_VERSION}/{tile_fixture.snapshot_id}/{z}/{x}/{y}.pbf"
    )
    assert r.status_code == 200, (
        f"expected a tile at z{z}/{x}/{y} containing the fixture, "
        f"got HTTP {r.status_code}"
    )
    assert r.headers["content-type"] == "application/x-protobuf"
    return mapbox_vector_tile.decode(r.content), r


class TestTileSchema:
    def test_serves_a_network_layer_containing_the_fixture(self, decoded):
        tile, _ = decoded
        assert "network" in tile
        assert len(tile["network"]["features"]) >= 2

    def test_publishes_every_property_the_client_reads(self, decoded):
        tile, _ = decoded
        props = set(tile["network"]["features"][0]["properties"])
        missing = CLIENT_READS - props
        assert not missing, (
            f"tile is missing {sorted(missing)}; the client would read undefined. "
            f"present: {sorted(props)}"
        )

    def test_sets_a_feature_id(self, decoded):
        # ST_AsMVT strips the feature-id column from properties, so the id is
        # the robust handle for selection and feature-state.
        tile, _ = decoded
        for f in tile["network"]["features"]:
            assert f.get("id") is not None

    def test_feature_id_matches_the_link_id_property(self, decoded):
        tile, _ = decoded
        for f in tile["network"]["features"]:
            assert int(f["id"]) == int(f["properties"]["linkId"])

    def test_state_highway_flag_distinguishes_the_two_fixture_roads(self, decoded):
        tile, _ = decoded
        by_name = {f["properties"]["roadName"]: f["properties"]
                   for f in tile["network"]["features"]}
        assert by_name["Test State Highway"]["stateHighway"] == 1
        assert by_name["Test Local Road"]["stateHighway"] == 0

    def test_oneway_flag_reflects_the_source(self, decoded):
        tile, _ = decoded
        by_name = {f["properties"]["roadName"]: f["properties"]
                   for f in tile["network"]["features"]}
        assert by_name["Test State Highway"]["oneway"] == 0
        assert by_name["Test Local Road"]["oneway"] == 1

    def test_flags_are_numeric_so_style_expressions_compare_correctly(self, decoded):
        tile, _ = decoded
        f = tile["network"]["features"][0]
        for key in ("oneway", "stateHighway", "lifeline", "core"):
            assert isinstance(f["properties"][key], int), (
                f"{key} must be numeric: the style compares it with =="
            )

    def test_empty_area_returns_204_not_an_empty_tile(self, client, tile_fixture):
        from nzcl.api import TILE_SCHEMA_VERSION
        r = client.get(
            f"/tiles/v{TILE_SCHEMA_VERSION}/{tile_fixture.snapshot_id}/12/100/100.pbf")
        assert r.status_code == 204


class TestTileCacheGovernance:
    """A cached tile must never be reinterpretable under a new snapshot/schema."""

    def test_bytes_are_deterministic(self, client, tile_fixture, decoded):
        _, first = decoded
        second = client.get(first.request.url.path)
        # ST_AsMVT encodes in row order; without an explicit ORDER BY the same
        # request can return different bytes, which makes an ETag meaningless.
        assert first.content == second.content
        assert first.headers["etag"] == second.headers["etag"]

    def test_conditional_request_returns_304(self, client, decoded):
        _, first = decoded
        r = client.get(first.request.url.path,
                       headers={"If-None-Match": first.headers["etag"]})
        assert r.status_code == 304

    def test_versioned_tile_is_cacheable(self, decoded):
        _, r = decoded
        assert "immutable" in r.headers["cache-control"]

    def test_unversioned_tile_is_not_cacheable(self, client):
        # Without a snapshot in the URL there is no safe way to let a cache keep it.
        r = client.get("/tiles/14/16146/10258.pbf")
        assert r.status_code in (200, 204)
        if r.status_code == 200:
            assert "no-cache" in r.headers["cache-control"]

    def test_unknown_schema_version_is_rejected(self, client, tile_fixture):
        r = client.get(f"/tiles/v1/{tile_fixture.snapshot_id}/14/16146/10258.pbf")
        assert r.status_code == 404

    def test_unknown_snapshot_is_rejected(self, client):
        from nzcl.api import TILE_SCHEMA_VERSION
        r = client.get(f"/tiles/v{TILE_SCHEMA_VERSION}/no-such-snapshot/14/1/1.pbf")
        assert r.status_code == 404

    def test_tilejson_uses_the_request_origin_and_is_versioned(self, client):
        j = client.get("/tiles/tilejson.json").json()
        assert "localhost:8000" not in j["tiles"][0], (
            "TileJSON must be built from the request origin, not a hard-coded host"
        )
        assert f"/tiles/v{j['tileSchemaVersion']}/" in j["tiles"][0]
        assert j["snapshotId"] in j["tiles"][0]
        assert j["attribution"]


class TestTileToDetourJourney:
    """A decoded tile feature must survive the whole path a click takes."""

    def test_a_tile_feature_resolves_to_a_detour(self, client, decoded):
        tile, _ = decoded
        feature = tile["network"]["features"][0]
        link_id = int(feature["id"])

        r = client.get(f"/api/v1/links/{link_id}/detour?geometry=false")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["selectedLink"]["linkId"] == link_id
        assert body["selectedLink"]["roadName"] == feature["properties"]["roadName"]
        assert body["closure"]["removedArcCount"] >= 1


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

    def test_error_bodies_use_the_field_the_client_reads(self, client):
        # The client reads `detail` (FastAPI) falling back to `error`. If the
        # shape changes, a user sees a bare status code instead of a reason.
        r = client.get("/api/v1/links/definitely-not-a-link/detour")
        assert r.status_code == 404
        assert isinstance(r.json().get("detail"), str)


@pytest.mark.realdata
class TestRealSnapshot:
    """Optional: additionally check a real ingested snapshot when present."""

    def test_wellington_tile_matches_the_database(self):
        row = db.query_one(
            "SELECT snapshot_id FROM network_snapshots "
            "WHERE snapshot_id LIKE 'amds-wellington-%' "
            "ORDER BY retrieved_at_utc DESC LIMIT 1")
        if not row:
            pytest.skip("no ingested Wellington snapshot (optional check)")
        snapshot = row["snapshot_id"]

        import os
        os.environ["SNAPSHOT_ID"] = snapshot
        from nzcl.api import app, TILE_SCHEMA_VERSION
        with TestClient(app) as c:
            r = c.get(f"/tiles/v{TILE_SCHEMA_VERSION}/{snapshot}/12/4036/2564.pbf")
            assert r.status_code == 200
            tile = mapbox_vector_tile.decode(r.content)

        feats = tile["network"]["features"]
        assert len(feats) > 100
        for f in feats[:40]:
            db_row = db.query_one(
                "SELECT rca_code, road_name FROM links "
                "WHERE snapshot_id=%s AND link_id=%s", (snapshot, int(f["id"])))
            assert db_row is not None, "tile feature id is not a real link"
            assert f["properties"]["stateHighway"] == (
                1 if db_row["rca_code"] == 1 else 0)
