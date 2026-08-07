"""Regressions for the second review round.

Four defects, all of the same family: a result that was correct for the object
it was computed on, then applied to something it did not describe.

  * a closure touching two ALREADY-disconnected components had its resulting
    parts ranked globally, so an untouched component was reported as newly cut
    off;
  * topology confidence, which depends on WHERE a closure is, was stored in a
    cache keyed only on WHAT it removes;
  * the bridge walk always took the DFS child subtree, which can be nearly the
    whole component, so "bounded by the separated side" was not true;
  * the non-bridge summary kept the closed link's state-highway indicator in
    the remainder.
"""

from __future__ import annotations

import json
import random

import pytest

from nzcl import closure as closure_mod
from nzcl import db, detourv2, physical

from conftest import requires_db
from test_closure_v2 import SQUARE, analyse

pytestmark = requires_db


@pytest.fixture(autouse=True)
def _clear_graph_cache():
    physical.clear_cache()
    yield
    physical.clear_cache()


# --------------------------------------------------------------------------
# closures spanning components that were ALREADY disconnected
# --------------------------------------------------------------------------
#: Two four-link cycles sharing no node: two pre-closure components.
TWO_CYCLES = [
    {"id": "A1", "pts": [(0, 0), (100, 0)]},
    {"id": "A2", "pts": [(100, 0), (100, 100)]},
    {"id": "A3", "pts": [(100, 100), (0, 100)]},
    {"id": "A4", "pts": [(0, 100), (0, 0)]},
    {"id": "B1", "pts": [(900, 0), (1000, 0)]},
    {"id": "B2", "pts": [(1000, 0), (1000, 100)]},
    {"id": "B3", "pts": [(1000, 100), (900, 100)]},
    {"id": "B4", "pts": [(900, 100), (900, 0)]},
]

#: The same, with a two-link spur on cycle B, so B can split and A cannot.
TWO_COMPONENTS_ONE_SPLITS = TWO_CYCLES + [
    {"id": "SPUR", "pts": [(900, 0), (800, 0)]},
    {"id": "TAIL", "pts": [(800, 0), (700, 0)]},
]


class TestClosureAcrossAlreadyDisconnectedComponents:
    """A closure cannot separate two things that were never joined.

    `_recompute_components` used to flatten every node of every touched
    component into one traversal and then rank ALL the resulting groups
    GLOBALLY. When a closure touched two components that were already
    disconnected, the lower-ranked one was reported as newly separated -
    despite never having been connected to the other and never having split.

    Reachable in production: a `source_feature` closure can have shape
    `disjoint`, and none of the original ten fixtures exercised one.
    """

    def test_two_cycles_each_losing_a_non_bridge_separate_nothing(
            self, synthetic):
        """Case A. Each cycle stays whole, so nothing is newly separated."""
        net = synthetic(TWO_CYCLES)
        g = physical.get(net.snapshot_id, "car")
        closure = [net.link_id("A1"), net.link_id("B1")]
        assert not g.bridge(closure[0]) and not g.bridge(closure[1])
        assert g.component_of_link(closure[0]) != g.component_of_link(closure[1])

        r = physical.analyse_closure(g, closure)
        assert r.physically_isolates is False
        assert r.separated_link_count == 0
        assert r.separated_link_ids == []
        assert r.separated_length_m == 0.0
        assert len(r.origin_component_ids) == 2
        assert all(c.retains_principal_connection for c in r.components)
        assert {c.origin_component_id for c in r.components} == set(
            r.origin_component_ids)

    def test_only_the_component_that_actually_split_is_reported(
            self, synthetic):
        """Case B. A splits nothing; B strands its tail. Only B contributes."""
        net = synthetic(TWO_COMPONENTS_ONE_SPLITS)
        g = physical.get(net.snapshot_id, "car")
        a_link, spur, tail = (net.link_id("A1"), net.link_id("SPUR"),
                              net.link_id("TAIL"))

        r = physical.analyse_closure(g, [a_link, spur])
        assert r.physically_isolates is True
        assert r.separated_link_ids == [tail]
        for name in ("A1", "A2", "A3", "A4"):
            assert net.link_id(name) not in r.separated_link_ids

        separated = [c for c in r.components
                     if not c.retains_principal_connection]
        assert len(separated) == 1
        assert separated[0].origin_component_id == g.component_of_link(spur)
        assert separated[0].origin_component_id != g.component_of_link(a_link)

    def test_both_cases_are_unchanged_by_row_order(self, synthetic):
        """Case C."""
        for spec, names in ((TWO_CYCLES, ("A1", "B1")),
                            (TWO_COMPONENTS_ONE_SPLITS, ("A1", "SPUR"))):
            net = synthetic(spec)
            closure = [net.link_id(n) for n in names]
            rows = physical._load_edges(net.snapshot_id, "car")
            baseline = None
            for seed in range(6):
                shuffled = list(rows)
                random.Random(seed).shuffle(shuffled)
                g = physical.from_edges(net.snapshot_id, "car", shuffled)
                r = physical.analyse_closure(g, closure)
                got = (r.physically_isolates, r.separated_link_ids,
                       round(r.separated_length_m, 6))
                if baseline is None:
                    baseline = got
                assert got == baseline, (
                    f"seed={seed}: result moved with row order")

    def test_end_to_end_disjoint_source_feature_closure(self, synthetic):
        """Case D, through the engine rather than the graph layer.

        One source feature whose graph children end up in different components
        is what a `disjoint` closure is. Nothing is separated, and the headline
        must not say anything was cut off.
        """
        net = synthetic(TWO_CYCLES)
        a, b = net.link_id("A1"), net.link_id("B1")
        group = db.query_one(
            "SELECT closure_group_id FROM links WHERE snapshot_id=%s "
            "  AND link_id=%s", (net.snapshot_id, a))["closure_group_id"]
        for table in ("links", "arcs"):
            db.execute(
                f"UPDATE {table} SET closure_group_id=%s "
                f" WHERE snapshot_id=%s AND link_id=%s",
                (group, net.snapshot_id, b))
        physical.clear_cache()

        r = detourv2.analyse(net.snapshot_id, a, scope="source_feature",
                             use_cache=False)
        assert sorted(r.closure.removed_link_ids) == sorted([a, b])
        assert r.closure.shape == "disjoint"
        assert r.isolation.physically_isolates is False
        assert r.isolation.separated_link_count == 0
        assert r.headline != "Road cut off"
        assert r.isolation_statement == (
            "No isolation in the represented physical-access graph")


# --------------------------------------------------------------------------
# topology confidence must not ride in the closure-invariant cache
# --------------------------------------------------------------------------
class TestTopologyConfidenceIsPerClosure:
    """Confidence describes WHERE a closure is, not what it removes.

    Under `scope='direction'` nothing is removed from Gu, so every direction
    closure in the country shares one isolation fingerprint. Storing the
    confidence in that cache meant the first request to run wrote it for all of
    them: a closure beside unresolved near misses could serve `medium`, and a
    clean one could serve `low` with a reason describing near misses hundreds
    of kilometres away.
    """

    def test_every_direction_closure_shares_one_isolation_fingerprint(self):
        """The precondition that makes the bug possible."""
        a = closure_mod.isolation_fingerprint(
            "snap", "car", physical.DERIVATION_VERSION, [])
        b = closure_mod.isolation_fingerprint(
            "snap", "car", physical.DERIVATION_VERSION, [])
        assert a == b

    def test_the_cached_payload_holds_no_confidence(self, synthetic):
        """Not persisted at all, rather than persisted and then overwritten."""
        net = synthetic(SQUARE)
        detourv2.invalidate_cache(net.snapshot_id)
        detourv2.analyse(net.snapshot_id, net.link_id("S"), scope="segment",
                         use_cache=True)
        row = db.query_one(
            "SELECT result FROM closure_isolation_v2 WHERE snapshot_id=%s "
            " LIMIT 1", (net.snapshot_id,))
        assert row is not None
        stored = row["result"]
        if isinstance(stored, str):
            stored = json.loads(stored)
        assert "topology_confidence" not in stored
        assert "topology_confidence_reason" not in stored
        # ...while the partition the cache exists to share IS there.
        assert "components" in stored
        assert "separated_link_ids" in stored

    @pytest.mark.parametrize("order", [("S", "N"), ("N", "S")])
    def test_confidence_is_computed_on_every_call_hit_or_miss(
            self, synthetic, order, monkeypatch):
        """A cache hit must still compute confidence from THIS closure's nodes.

        The synthetic snapshot has no near misses, so both links would report
        `medium` either way. The check that bites is therefore on the ARGUMENT:
        `topology_confidence` must be called once per request, each time with
        the nodes of the closure being asked about - not once for the first
        request and then inherited.
        """
        net = synthetic(SQUARE)
        detourv2.invalidate_cache(net.snapshot_id)

        calls: list[tuple[int, ...]] = []
        real = physical.topology_confidence

        def spy(snapshot_id, node_ids):
            calls.append(tuple(sorted(node_ids)))
            return real(snapshot_id, node_ids)

        monkeypatch.setattr(physical, "topology_confidence", spy)

        wanted = []
        for name in order:
            link = net.link_id(name)
            row = db.query_one(
                "SELECT source_node, target_node FROM links "
                " WHERE snapshot_id=%s AND link_id=%s", (net.snapshot_id, link))
            wanted.append(tuple(sorted(
                (int(row["source_node"]), int(row["target_node"])))))
            r = detourv2.analyse(net.snapshot_id, link, scope="direction",
                                 direction="forward", use_cache=True)
            assert r.isolation.topology_confidence in ("medium", "low")
            assert r.isolation.topology_confidence_reason

        # One call per request - the second was a cache hit and still computed.
        assert len(calls) == 2, (
            "confidence was not recomputed on the cache hit")
        assert calls == wanted, (
            "confidence was computed from the wrong closure's nodes")


# --------------------------------------------------------------------------
# bridge traversal walks the smaller side
# --------------------------------------------------------------------------
#: A three-node loop, a bridge, then a fifteen-link chain. The loop is listed
#: first so the DFS roots there, which makes the CHILD subtree the long chain -
#: the LARGE side. Walking the child unconditionally would examine ~19 nodes to
#: separate a 3-node pocket.
INVERTED_BRIDGE = (
    [{"id": "L1", "pts": [(0, 0), (100, 0)]},
     {"id": "L2", "pts": [(100, 0), (50, 80)]},
     {"id": "L3", "pts": [(50, 80), (0, 0)]},
     {"id": "BR", "pts": [(100, 0), (300, 0)]}]
    + [{"id": f"C{i}", "pts": [(300 + 100 * i, 0), (400 + 100 * i, 0)]}
       for i in range(15)]
)


class TestBridgeWalksTheSmallerSide:
    """"Bounded by the separated side" was not true of the previous code.

    It always walked the DFS CHILD subtree, and DFS orientation is an artefact
    of where the traversal started - the child side can be nearly the whole
    component. Both side sizes are now known in O(1) from the contiguous DFS
    preorder interval, so the walk starts on the smaller side.

    Measured with a counter rather than a clock: wall-clock time measures the
    machine, not the algorithm.
    """

    def test_the_small_side_is_separated_and_only_it_is_walked(
            self, synthetic):
        net = synthetic(INVERTED_BRIDGE)
        g = physical.get(net.snapshot_id, "car")
        br = net.link_id("BR")
        assert g.bridge(br) is True

        r = physical.analyse_closure(g, [br])
        assert r.method == "bridge-smaller-side-and-subtraction"
        assert r.physically_isolates is True

        separated = [c for c in r.components
                     if not c.retains_principal_connection]
        assert len(separated) == 1
        assert separated[0].node_count == 3
        assert separated[0].link_count == 3

        cid = g.component_of_link(br)
        total_nodes = int(g.comp_nodes[cid])
        assert total_nodes >= 18
        assert r.nodes_examined <= 8, (
            f"walked {r.nodes_examined} nodes of {total_nodes} to separate a "
            f"3-node pocket; the walk is not bounded by the smaller side")

    def test_conservation_holds_when_a_side_is_derived_not_walked(
            self, synthetic):
        net = synthetic(INVERTED_BRIDGE)
        g = physical.get(net.snapshot_id, "car")
        br = net.link_id("BR")
        r = physical.analyse_closure(g, [br])
        cid = g.component_of_link(br)

        assert sum(c.node_count for c in r.components) == int(g.comp_nodes[cid])
        assert (sum(c.link_count for c in r.components) + 1
                == int(g.comp_links[cid]))
        assert (sum(c.road_length_m for c in r.components)
                + float(g.edge_len[g.link_index[br]])
                == pytest.approx(float(g.comp_length[cid]), abs=1e-6))


# --------------------------------------------------------------------------
# the non-bridge summary must drop the closed link's state-highway indicator
# --------------------------------------------------------------------------
class TestNonBridgeComponentSummary:
    def test_closing_a_state_highway_decrements_the_sh_count(self, synthetic):
        """The remainder cannot still contain the link that was closed.

        The fast path decremented link count and subtracted length but left the
        state-highway count alone, over-reporting the state highways remaining
        by one on exactly the closures a reader is most likely to look at.
        """
        net = synthetic(SQUARE)
        g = physical.get(net.snapshot_id, "car")
        s = net.link_id("S")
        e = g.link_index[s]
        # The synthetic loader carries no RCA, so the anchor is set directly:
        # this is a test of the arithmetic, not of the ingest.
        g.edge_sh[e] = 1
        cid = int(g.comp_of_edge[e])
        g.comp_sh_links[cid] += 1
        before = int(g.comp_sh_links[cid])

        r = physical.analyse_closure(g, [s])
        assert r.method == "precomputed-not-a-bridge"
        assert len(r.components) == 1
        assert r.components[0].state_highway_link_count == before - 1
        assert r.components[0].link_count == int(g.comp_links[cid]) - 1

    def test_closing_a_local_road_leaves_the_sh_count_alone(self, synthetic):
        net = synthetic(SQUARE)
        g = physical.get(net.snapshot_id, "car")
        s = net.link_id("S")
        cid = int(g.comp_of_edge[g.link_index[s]])
        before = int(g.comp_sh_links[cid])

        r = physical.analyse_closure(g, [s])
        assert r.components[0].state_highway_link_count == before
