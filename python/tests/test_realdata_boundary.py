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
