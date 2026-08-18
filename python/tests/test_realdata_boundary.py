"""Named real-data regression cases, pinned against the national snapshot.

Marked `realdata`, so the mandatory suite deselects them: CI has no national
ingest, and a test that silently skips protects nothing. Run them with

    pytest -m realdata

against a database holding the national snapshot.

These exist because a synthetic fixture proves the SHAPE and a real link proves
the shape occurs. `test_movements.py::TestTheReassuringHundredMetres` covers the
first; this covers the second.
"""

from __future__ import annotations

import pytest

from nzcl import closure as closure_mod
from nzcl import db, detourv2, impactv2, reviewv2, turns

from conftest import requires_db

pytestmark = [requires_db, pytest.mark.realdata]

NATIONAL = "amds-national-2026-07-28-5b359d84"

#: Lyon Street. The endpoint measure's failure mode, in the field.
LYON_STREET = 375011


def _skip_without_snapshot():
    row = db.query_one(
        "SELECT snapshot_id FROM network_snapshots WHERE snapshot_id=%s",
        (NATIONAL,))
    if row is None:
        pytest.fail(
            f"snapshot {NATIONAL} is not in this database. These tests are "
            "marked `realdata` and are deselected by default; if you selected "
            "them, the snapshot has to be here.")


class TestLyonStreet375011:
    """The reassuring 108 metres, on the road it actually happened on."""

    def test_the_endpoint_measure_reports_a_hundred_metre_alternative(self):
        _skip_without_snapshot()
        ep = detourv2.analyse(NATIONAL, LYON_STREET, scope="source_feature",
                              use_cache=False)
        d = ep.forward or ep.reverse
        assert d.status == "OK"
        assert d.alternative_distance_m == pytest.approx(108.408, abs=0.01)

    def test_the_through_movement_has_no_replacement(self):
        _skip_without_snapshot()
        r = impactv2.analyse(NATIONAL, LYON_STREET, scope="source_feature")
        assert r.principal is not None
        assert r.principal.status == "DISCONNECTED"
        assert r.principal.replacement_distance_m is None
        assert r.principal.intact_distance_m == pytest.approx(1179.25, abs=0.1)

    def test_the_closure_is_thirteen_links_not_one(self):
        _skip_without_snapshot()
        c = closure_mod.resolve(NATIONAL, LYON_STREET, scope="source_feature")
        assert c.removed_link_count == 13
        assert c.total_closure_length_m == pytest.approx(1382.9, abs=0.1)
        assert c.selected_segment_length_m == pytest.approx(92.6, abs=0.1)

    def test_something_really_is_cut_off(self):
        _skip_without_snapshot()
        r = impactv2.analyse(NATIONAL, LYON_STREET, scope="source_feature")
        assert r.isolation is not None
        assert r.isolation.physically_isolates is True
        assert r.isolation.separated_link_count == 26
        assert r.isolation.separated_length_m == pytest.approx(5110.4, abs=0.5)

    def test_an_independent_oracle_confirms_both_figures(self):
        """networkx over the raw arcs, no engine code involved.

        Both halves matter: the 108 m the endpoint measure found IS there, and
        the movement pair really has no route. The oracle has to reproduce the
        reassuring number too, or it is not checking the same graph.
        """
        _skip_without_snapshot()
        parallel = reviewv2.load_oracle_graph(NATIONAL)
        c = closure_mod.resolve(NATIONAL, LYON_STREET, scope="source_feature")
        removed = {int(a) for a in c.removed_arc_ids}

        link = db.query_one(
            "SELECT source_node, target_node FROM links "
            " WHERE snapshot_id=%s AND link_id=%s", (NATIONAL, LYON_STREET))
        endpoint = reviewv2.oracle_distance(
            parallel, int(link["source_node"]), int(link["target_node"]),
            removed)
        assert endpoint == pytest.approx(108.408, abs=0.01)

        r = impactv2.analyse(NATIONAL, LYON_STREET, scope="source_feature")
        pm = r.principal_movement
        assert reviewv2.oracle_distance(
            parallel, pm.from_node, pm.to_node, removed) is None


class TestMilfordSoundHighway:
    """A road that really is sole access, on the country's clearest example.

    Link 6774 is a 10.8 km section of SH94. Closing it separates 24 links and
    21.8 km of road, and the closure is a bridge in the graph-theoretic sense:
    there is no second way in. This is the case where "Road cut off" is the
    correct headline, and it is here so that the five cases in
    TestALostCrossingIsNotARoadCutOff are testing a distinction rather than a
    blanket suppression.
    """

    LINK = 6774

    def test_it_is_a_genuine_isolation(self):
        _skip_without_snapshot()
        r = impactv2.analyse(NATIONAL, self.LINK, scope="segment",
                             with_isolation=True)
        assert r.headline == "Through movement has no represented replacement"
        assert r.principal is not None
        assert r.principal.status == "DISCONNECTED"

        iso = r.isolation
        assert iso is not None
        assert iso.physically_isolates is True
        assert iso.closure_is_bridge is True
        assert iso.separated_link_count == 24
        assert iso.separated_length_m == pytest.approx(21760.2, abs=1.0)

    def test_the_closure_is_the_segment_that_was_selected(self):
        """Segment scope, and it removes exactly one link.

        The point of the default: a sole-access road is found without having to
        remove the whole AMDS source record around it.
        """
        _skip_without_snapshot()
        c = closure_mod.resolve(NATIONAL, self.LINK, scope="segment")
        assert c.removed_link_count == 1
        assert c.total_closure_length_m == pytest.approx(10828.8, abs=0.5)


class TestNationalCohorts:
    """Three cohorts of five, spread the length of the country.

    Chosen deterministically (`ORDER BY md5(link_id::text)`, then thinned so no
    two are within 0.4 degrees of latitude) rather than picked for their
    answers. Ordering by anything meaningful - id, length, road number - biases
    the sample towards whatever was ingested first, which on this data is the
    state-highway network.

    The assertions are PROPERTIES rather than pinned figures. A cohort test
    asserting "Willowflat Road separates 122 links" fails on the next ingest
    for a reason that has nothing to do with the engine; what has to hold is
    that each cohort still behaves like its kind.
    """

    #: One-way carriageways. The retired engine's worst case: it asked for a
    #: path from a link's end back to its own start, which does not exist on a
    #: one-way, called that DISCONNECTED and headlined it as a road cut off.
    ONE_WAY = {
        5329: "State Highway 74",
        291336: "Resolution Drive Roundabout",
        369743: "Riverside Drive (N)",
        147231: "Maunganamu Drive",
        61941: "Kennedy Road",
    }

    #: State highways, north to south. 5329 is deliberately in both cohorts:
    #: it is a one-way state highway, and each cohort asserts a different
    #: property of it.
    STATE_HIGHWAY = {
        241361: "State Highway 10",
        1970: "State Highway 35",
        4612: "State Highway 2",
        370140: "State Highway 67",
        5329: "State Highway 74",
    }

    #: Closures that genuinely separate part of the network.
    TRUE_ISOLATION = {
        270018: "Wilcox Road",
        346129: "Ford Road",
        193527: "Vintage Drive",
        162816: "Harris Lane",
        75365: "Willowflat Road",
    }

    HEADLINES = {
        "Through movement diverts",
        "Through movement has no represented replacement",
        "No through movement identified",
        "Partial analysis",
        "Analysis unresolved",
    }

    @pytest.mark.parametrize("link_id", sorted(ONE_WAY))
    def test_a_one_way_link_is_analysed_rather_than_defeated(self, link_id):
        """It resolves, and nothing is claimed cut off that is not.

        The measure is across the closure boundary, so a one-way carriageway
        has a crossing like any other road and the question the retired engine
        could not answer is not asked.
        """
        _skip_without_snapshot()
        row = db.query_one(
            "SELECT oneway FROM links WHERE snapshot_id=%s AND link_id=%s",
            (NATIONAL, link_id))
        assert row is not None and row["oneway"] == 1, (
            f"{link_id} is not one-way; this cohort tests the wrong thing")

        r = impactv2.analyse(NATIONAL, link_id, scope="segment",
                             with_isolation=True)
        assert r.headline in self.HEADLINES
        assert r.isolation is not None
        if not r.isolation.physically_isolates:
            assert r.isolation.separated_link_count == 0

    @pytest.mark.parametrize("link_id", sorted(STATE_HIGHWAY))
    def test_a_state_highway_resolves_and_is_named(self, link_id):
        _skip_without_snapshot()
        r = impactv2.analyse(NATIONAL, link_id, scope="segment",
                             with_isolation=True)
        assert r.headline in self.HEADLINES

        body = impactv2.as_dict(r, include_all_movements=False)
        pm = (body["principal"] or {}).get("movement")
        if pm is not None:
            # The crossing has to be identifiable. A state highway closure with
            # a figure and no subject is the case a reader most needs to place.
            assert "entryRoadName" in pm and "exitRoadName" in pm

    @pytest.mark.parametrize("link_id", sorted(TRUE_ISOLATION))
    def test_a_true_isolation_separates_something_and_says_so(self, link_id):
        _skip_without_snapshot()
        r = impactv2.analyse(NATIONAL, link_id, scope="segment",
                             with_isolation=True)
        iso = r.isolation
        assert iso is not None
        assert iso.physically_isolates is True, (
            f"{link_id} ({self.TRUE_ISOLATION[link_id]}) no longer isolates; "
            "this cohort is meant to be the case where 'Road cut off' is right")
        assert iso.separated_link_count > 0
        assert iso.separated_length_m > 0
        # And the routing finding agrees it has nowhere to go.
        assert r.principal is not None
        assert r.principal.status == "DISCONNECTED"

    @pytest.mark.parametrize("link_id", sorted(TRUE_ISOLATION))
    def test_the_separated_links_can_be_drawn(self, link_id):
        """The counts are checkable only if the reader can see them.

        `separatedGeoJson` is capped, and null beyond the cap: a truncated
        collection drawn as if whole understates the extent, which is worse
        than drawing nothing. Below the cap it must actually be there.
        """
        _skip_without_snapshot()
        from nzcl import api as api_mod

        r = impactv2.analyse(NATIONAL, link_id, scope="segment",
                             with_isolation=True)
        iso = r.isolation
        assert iso is not None
        n = len(iso.separated_link_ids)
        if 0 < n <= api_mod.MAX_DRAWN_STRANDED_LINKS:
            gj = api_mod._links_geojson_v2(NATIONAL, iso.separated_link_ids)
            assert gj is not None
            assert len(gj["features"]) == n


class TestStBathansLoopRoadStaysPartial:
    """295128 must keep saying it did not look at everything.

    The candidate bound acts here: 400 pairs considered, 84 left unevaluated.
    That withholds the definitive headline and substitutes "Partial analysis",
    which is the correct answer — an unevaluated crossing could hold a longer
    diversion, or the only one with no replacement at all.

    Raising the bound until this case resolves would make the panel read
    better and would be the wrong change to make before production: it would
    remove the visible instance of a class of results that still exists, and
    leave nothing to notice the class by. Merge condition, 2026-08-18.
    """

    LINK = 295128

    def test_it_reports_partial_analysis(self):
        _skip_without_snapshot()
        r = impactv2.analyse(NATIONAL, self.LINK, scope="source_feature")
        assert r.headline == "Partial analysis"
        assert r.headline not in impactv2.DEFINITIVE_HEADLINES

    def test_the_bound_really_acted_and_is_reported(self):
        _skip_without_snapshot()
        r = impactv2.analyse(NATIONAL, self.LINK, scope="source_feature")
        ms = r.movement_set
        assert ms.exhaustive is False
        assert ms.candidate_pairs == 400
        assert ms.omitted_pair_count == 84
        assert "HEADLINE_WITHHELD_NOT_EXHAUSTIVE" in r.quality_flags
        assert "MOVEMENT_CANDIDATES_TRUNCATED" in r.quality_flags

    def test_the_wire_body_says_so_too(self):
        """The panel gates its caveat on `exhaustive`, so the field has to
        survive serialisation as well as exist on the dataclass."""
        _skip_without_snapshot()
        body = impactv2.as_dict(
            impactv2.analyse(NATIONAL, self.LINK, scope="source_feature"))
        assert body["headline"] == "Partial analysis"
        assert body["movements"]["exhaustive"] is False
        assert body["movements"]["omittedPairCount"] == 84


class TestALostCrossingIsNotARoadCutOff:
    """Five closures where one crossing loses its route and nothing is cut off.

    A highway pair, a roundabout arm, a one-way carriageway, a city street and
    a motorway connector. In every one the principal movement is DISCONNECTED
    and the undirected isolation result finds nothing separated at all.

    "Road cut off" here would be false about the place while being true about
    the crossing, and the place is what a reader acts on. The engine must keep
    these two apart in the response; the panel test in tests/e2e asserts the
    words the reader sees.
    """

    CASES = {
        8887: "Bluff Highway East",
        33082: "Titahi Bay roundabout",
        27644: "The Boulevard eastbound",
        29348: "Swanston Street",
        51258: "SH74 connector",
    }

    @pytest.mark.parametrize("link_id", sorted(CASES))
    def test_a_lost_crossing_with_nothing_separated(self, link_id):
        _skip_without_snapshot()
        r = impactv2.analyse(NATIONAL, link_id, scope="source_feature",
                             with_isolation=True)
        where = self.CASES[link_id]

        assert r.headline == "Through movement has no represented replacement", (
            f"{link_id} ({where}) no longer reports a lost crossing")
        assert r.principal is not None
        assert r.principal.status == "DISCONNECTED"

        # The half that matters: nothing is separated, so nothing may be
        # headlined as cut off.
        assert r.isolation is not None, (
            f"{link_id} ({where}) returned no isolation result, so the panel "
            "has nothing to distinguish a lost crossing from a cut-off road")
        assert r.isolation.physically_isolates is False
        assert r.isolation.separated_link_count == 0
        assert r.isolation.separated_length_m == 0

    @pytest.mark.parametrize("link_id", sorted(CASES))
    def test_the_response_carries_no_cut_off_wording(self, link_id):
        """No string anywhere in the body says the road is cut off.

        Headline vocabulary alone is not enough — `detail` fields are free
        prose and are rendered verbatim, so a sentence added to one of them
        would reach the reader without any headline changing.
        """
        _skip_without_snapshot()
        body = impactv2.as_dict(
            impactv2.analyse(NATIONAL, link_id, scope="source_feature",
                             with_isolation=True))
        for key in ("headline",):
            assert "cut off" not in body[key].lower()
        for block in ("movements", "replacements"):
            assert "cut off" not in (body[block]["detail"] or "").lower()
        assert "cut off" not in (body["principal"]["detail"] or "").lower()
        assert "cut off" not in (body["isolation"]["detail"] or "").lower()


class TestLowTopologyConfidenceKeepsItsReason:
    """The seven cases review singled out.

    V2 asks the better question on each of these and the local graph is
    uncertain, so the real-world reading has to stay caveated. A label is not a
    caveat: "topology confidence: low" says there is a scale and this is the
    bad end of it, and nothing about why. The REASON is the part that says the
    connectivity may be an artefact of an ingest tolerance rather than a
    property of the roads.
    """

    LINKS = (375011, 157091, 169247, 17097, 313963, 147489, 114903)

    @pytest.mark.parametrize("link_id", LINKS)
    def test_the_reason_is_present_and_substantive(self, link_id):
        _skip_without_snapshot()
        r = impactv2.analyse(NATIONAL, link_id, scope="source_feature",
                             with_isolation=True)
        assert r.isolation is not None
        assert r.isolation.topology_confidence == "low"

        reason = r.isolation.topology_confidence_reason or ""
        assert reason.strip(), f"{link_id} is low confidence with no reason"
        # It must say what is uncertain, not merely that something is.
        assert "near-miss" in reason
        assert len(reason) > 80, (
            "a reason short enough to be a label is a label")

    @pytest.mark.parametrize("link_id", LINKS)
    def test_the_reason_survives_serialisation(self, link_id):
        _skip_without_snapshot()
        body = impactv2.as_dict(
            impactv2.analyse(NATIONAL, link_id, scope="source_feature",
                             with_isolation=True))
        iso = body["isolation"]
        assert iso["topologyConfidence"] == "low"
        assert (iso["topologyConfidenceReason"] or "").strip()


class TestTheOnlyRestrictionThatRestrictsAnything:
    """Every published restriction that applies to a modelled vehicle class.

    That is one record. The figure is small enough to be worth stating exactly
    rather than gesturing at, and small enough that it invites being assumed to
    be zero and skipped - which is how a validator that never runs against real
    data ends up shipping.

    AMDS publishes 60 restricted turns for the whole country. 16 could not be
    resolved to a connected chain of graph links after junction splitting and 1
    referenced links outside the extract, leaving 43 in this snapshot. Of those
    43, exactly ONE sets any of restricted_vehicle, restricted_heavy or
    restricted_emergency, and it sets all three.

    These assertions are the ones that fail loudly if a re-ingest changes the
    data underneath the validator, which is the case that would otherwise leave
    `applicableRestrictions: 0` on every route and nobody any the wiser.
    """

    def test_the_snapshot_holds_the_records_this_suite_assumes(self):
        _skip_without_snapshot()
        row = db.query_one(
            "SELECT count(*) AS total, "
            "       count(*) FILTER (WHERE restricted_vehicle) AS car, "
            "       count(*) FILTER (WHERE restricted_heavy) AS heavy, "
            "       count(*) FILTER (WHERE restricted_emergency) AS emergency, "
            "       count(*) FILTER (WHERE restricted_vehicle "
            "                          OR restricted_heavy "
            "                          OR restricted_emergency) AS any_class "
            "  FROM turn_restrictions WHERE snapshot_id=%s", (NATIONAL,))

        assert row["total"] == 43
        assert row["any_class"] == 1, (
            "the count of restrictions that restrict anything has changed; "
            "every figure in this class is derived from it")
        # All three flags land on the same single record.
        assert row["car"] == 1
        assert row["heavy"] == 1
        assert row["emergency"] == 1

    def test_it_is_a_two_link_sequence_and_so_exactly_enforceable(self):
        """Sequence length decides how exact the check can be.

        A two-link restriction is a single turn and is matched exactly. Longer
        sequences are approximated against the predecessor chain. The one
        record that restricts anything is two links, so the only restriction
        this engine can actually act on is also the one it can act on exactly -
        which is worth knowing, and worth being told when it stops being true.
        """
        _skip_without_snapshot()
        rows = db.query(
            "SELECT link_seq FROM turn_restrictions WHERE snapshot_id=%s "
            "   AND (restricted_vehicle OR restricted_heavy "
            "        OR restricted_emergency)", (NATIONAL,))
        assert len(rows) == 1
        assert len(rows[0]["link_seq"]) == 2

    def test_every_modelled_profile_sees_exactly_that_one(self):
        """The loader is per-profile, so each profile is asked separately.

        A column mapped to the wrong profile would show up here as a profile
        seeing zero, and nowhere else: with 42 of 43 records restricting
        nothing, a validator loading the wrong column still returns a plausible
        empty list.
        """
        _skip_without_snapshot()
        for profile in ("car", "heavy", "emergency"):
            seqs = turns.restricted_sequences(NATIONAL, profile)
            assert len(seqs) == 1, f"{profile} saw {len(seqs)} restrictions"
            assert len(seqs[0]) == 2

    def test_a_route_over_the_restricted_pair_is_refused_and_not_drawn(self):
        """The whole chain, on the real record, end to end.

        The restricted pair is two adjacent links. Closing the first one forces
        any replacement to reach the second some other way; whether that route
        happens to use the banned turn depends on the local network, so the
        assertion is conditional on it - what must NOT be conditional is that if
        the check fires, the result fails closed in every respect.
        """
        _skip_without_snapshot()
        row = db.query_one(
            "SELECT link_seq FROM turn_restrictions WHERE snapshot_id=%s "
            "   AND (restricted_vehicle OR restricted_heavy "
            "        OR restricted_emergency)", (NATIONAL,))
        first, second = (int(x) for x in row["link_seq"])

        for link_id in (first, second):
            r = impactv2.analyse(NATIONAL, link_id, scope="segment",
                                 with_geometry=True, with_isolation=False)
            if r.principal is None:
                continue
            assert r.principal.turn_check is not None, (
                "a replacement route was produced without being checked "
                "against the restriction table at all")
            assert r.principal.turn_check.checked is True
            assert r.principal.turn_check.applicable_restrictions == 1

            if r.principal.status == "TURN_RESTRICTION_UNSUPPORTED":
                assert r.principal.resolved is False
                assert r.headline == "Analysis unresolved"
                assert r.replacement_geometry is None
                body = impactv2.as_dict(r)
                assert "replacement" not in (body["geometry"] or {})


class TestTheOracleHandlesParallelArcs:
    """A guard on the oracle, not on the engine.

    The first version of `reviewv2.load_oracle_graph` collapsed each node pair
    to its cheapest arc and then deleted edges whose stored arc was closed.
    Where two arcs run between the same nodes and only the cheaper is closed,
    that removed the connection entirely - the oracle reported "no path" on
    Lyon Street's endpoint pair while the engine correctly routed 108 m.

    Had it done that on a MOVEMENT pair, engine and oracle would have agreed on
    None for opposite reasons and the agreement would have been worthless.
    """

    def test_parallel_arcs_exist_in_this_data(self):
        _skip_without_snapshot()
        row = db.query_one(
            "SELECT count(*) AS pairs, sum(n) AS arcs FROM ("
            "  SELECT source, target, count(*) AS n FROM arcs "
            "   WHERE snapshot_id=%s AND mode_vehicle "
            "   GROUP BY source, target HAVING count(*) > 1) q",
            (NATIONAL,))
        assert row["pairs"] > 0, (
            "if no node pair carries parallel arcs, this guard is vacuous")

    def test_a_surviving_parallel_arc_keeps_the_connection(self):
        _skip_without_snapshot()
        pair = db.query_one(
            "SELECT source, target, array_agg(arc_id ORDER BY arc_id) AS arcs "
            "  FROM arcs WHERE snapshot_id=%s AND mode_vehicle "
            " GROUP BY source, target HAVING count(*) > 1 "
            " ORDER BY source, target LIMIT 1", (NATIONAL,))
        parallel = reviewv2.load_oracle_graph(NATIONAL)
        u, v = int(pair["source"]), int(pair["target"])
        arcs = [int(a) for a in pair["arcs"]]

        # Remove all but one: the connection must survive.
        d = reviewv2.oracle_distance(parallel, u, v, set(arcs[:-1]))
        assert d is not None
        # Remove every one: only then is it gone (or routed the long way).
        d_all = reviewv2.oracle_distance(parallel, u, v, set(arcs))
        assert d_all is None or d_all > d
