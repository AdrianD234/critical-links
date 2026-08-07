"""The synthetic snapshot must be a faithful miniature, not just enough rows.

Every defect these cover was invisible to the routing tests — which read
`geom_2193` and the graph tables — and fatal to the browser suite, which reads
the columns the map draws from. That gap is why they survived: the fixture was
built to satisfy pgRouting, and then quietly reused as the thing an entire
product gate runs against.
"""

from __future__ import annotations

import pytest
from conftest import requires_db

from nzcl import db
from nzcl.fixtures import (
    CI_COVERAGE_NAME,
    CI_SNAPSHOT_ID,
    FixtureContractError,
    assert_fixture_contract,
    build_ci_snapshot,
)

pytestmark = requires_db


@pytest.fixture(scope="module")
def ci_snapshot() -> str:
    return build_ci_snapshot()


def test_the_contract_check_passes_on_a_freshly_built_fixture(ci_snapshot):
    """If this fails, every browser test downstream is about to fail too."""
    assert_fixture_contract(ci_snapshot, require_nz=True)


def test_it_is_labelled_synthetic_and_named(ci_snapshot):
    row = db.query_one(
        "SELECT coverage_kind, coverage_name, status FROM network_snapshots "
        " WHERE snapshot_id = %s", (ci_snapshot,))
    assert row["coverage_kind"] == "synthetic"
    assert row["coverage_name"] == CI_COVERAGE_NAME
    assert row["status"] == "complete"


def test_counts_match_the_rows_that_exist(ci_snapshot):
    """The row used to say zero links while seven sat in the table, so
    /health and the About panel both reported an empty network."""
    row = db.query_one(
        """
        SELECT s.routable_link_count, s.arc_count, s.node_count,
               (SELECT count(*) FROM links WHERE snapshot_id=s.snapshot_id) AS links,
               (SELECT count(*) FROM arcs  WHERE snapshot_id=s.snapshot_id) AS arcs,
               (SELECT count(*) FROM nodes WHERE snapshot_id=s.snapshot_id) AS nodes
          FROM network_snapshots s WHERE s.snapshot_id = %s
        """, (ci_snapshot,))
    assert row["routable_link_count"] == row["links"] > 0
    assert row["arc_count"] == row["arcs"] > 0
    assert row["node_count"] == row["nodes"] > 0


def test_the_node_count_is_derived_not_assumed(ci_snapshot):
    """Junction splitting decides how many nodes there are. Asserting a
    hard-coded number would pin the fixture to today's splitting behaviour and
    call a real topology change a fixture failure."""
    row = db.query_one(
        "SELECT node_count, (SELECT count(DISTINCT n) FROM ("
        "   SELECT source_node AS n FROM links WHERE snapshot_id=%(s)s"
        "   UNION SELECT target_node FROM links WHERE snapshot_id=%(s)s) u"
        " ) AS referenced FROM network_snapshots WHERE snapshot_id=%(s)s",
        {"s": ci_snapshot})
    assert row["node_count"] == row["referenced"]


def test_it_has_both_extents(ci_snapshot):
    """Without a display extent the application has nothing to fit the map to
    and opens at the national fallback, where a 1.6 km fixture is invisible."""
    row = db.query_one(
        "SELECT display_extent_2193 IS NOT NULL AS display, "
        "       analysis_extent_2193 IS NOT NULL AS analysis "
        "  FROM network_snapshots WHERE snapshot_id = %s", (ci_snapshot,))
    assert row["display"] and row["analysis"]


def test_the_display_extent_contains_every_link(ci_snapshot):
    outside = db.query_one(
        "SELECT count(*) AS n FROM links l JOIN network_snapshots s "
        "    ON s.snapshot_id = l.snapshot_id "
        " WHERE l.snapshot_id = %s "
        "   AND NOT ST_Contains(s.display_extent_2193, l.geom_2193)",
        (ci_snapshot,))
    assert outside["n"] == 0


# ------------------------------------------------------------------ geometry

def test_link_geometry_is_transformed_not_relabelled(ci_snapshot):
    """`geom_4326` used to be the NZTM WKT with the SRID overwritten, giving a
    longitude of about 1,749,100 degrees. Nothing that only routes would ever
    notice; everything that draws would."""
    row = db.query_one(
        "SELECT count(*) AS bad FROM links WHERE snapshot_id = %s "
        "   AND NOT ST_Within(geom_4326, ST_MakeEnvelope(-180,-90,180,90,4326))",
        (ci_snapshot,))
    assert row["bad"] == 0


def test_node_geometry_is_transformed_not_a_placeholder(ci_snapshot):
    """Nodes were written as POINT(0 0) in EPSG:4326 — the Gulf of Guinea."""
    row = db.query_one(
        "SELECT count(*) AS at_null_island FROM nodes WHERE snapshot_id = %s "
        "   AND ST_DWithin(geom_4326, ST_SetSRID(ST_MakePoint(0,0),4326), 0.001)",
        (ci_snapshot,))
    assert row["at_null_island"] == 0


def test_every_geometry_lands_in_new_zealand(ci_snapshot):
    for table in ("links", "nodes"):
        row = db.query_one(
            f"SELECT count(*) AS n FROM {table} WHERE snapshot_id = %s "
            "   AND NOT ST_Within(geom_4326, ST_MakeEnvelope(166,-48,179,-34,4326))",
            (ci_snapshot,))
        assert row["n"] == 0, f"{row['n']} {table} rows fall outside New Zealand"


def test_the_two_projections_describe_the_same_place(ci_snapshot):
    """A round trip back to NZTM must land on the original within a metre."""
    row = db.query_one(
        "SELECT max(ST_Distance(geom_2193, ST_Transform(geom_4326, 2193))) AS drift "
        "  FROM links WHERE snapshot_id = %s", (ci_snapshot,))
    assert row["drift"] < 1.0


# -------------------------------------------------------------- the guard itself

def test_the_contract_check_actually_rejects_a_broken_fixture(ci_snapshot):
    """A guard that cannot fail is not a guard. Break the row, confirm the
    check catches it, then put it back."""
    db.execute(
        "UPDATE network_snapshots SET display_extent_2193 = NULL "
        " WHERE snapshot_id = %s", (ci_snapshot,))
    try:
        with pytest.raises(FixtureContractError, match="display_extent_2193"):
            assert_fixture_contract(ci_snapshot)
    finally:
        build_ci_snapshot()
    assert_fixture_contract(CI_SNAPSHOT_ID, require_nz=True)


def test_abstract_fixtures_are_held_to_valid_wgs84_but_not_to_geography(synthetic):
    """The known-answer networks are coordinate grids at the NZTM origin, not
    places. They must still transform to real longitudes and latitudes - that
    is what was broken - but demanding they land in New Zealand would fail
    every routing test for no defect."""
    net = synthetic([
        {"id": "{a}", "pts": [(0.0, 0.0), (100.0, 0.0)], "road_name": "Grid Road"},
    ])
    assert_fixture_contract(net.snapshot_id)          # passes
    with pytest.raises(FixtureContractError, match="outside NZ"):
        assert_fixture_contract(net.snapshot_id, require_nz=True)

    bad = db.query_one(
        "SELECT count(*) AS n FROM links WHERE snapshot_id = %s "
        "   AND NOT ST_Within(geom_4326, ST_MakeEnvelope(-180,-90,180,90,4326))",
        (net.snapshot_id,))
    assert bad["n"] == 0
