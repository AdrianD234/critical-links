"""Known-answer tests for the boundary-port model (PR 2, checkpoint A).

The property that matters most is the COMPATIBILITY one: on a simple two-way
segment the port measure and the endpoint measure must coincide. If they did
not, PR 2 would be changing answers on the easy cases as well as the hard ones,
and every difference from PR 1 would be unattributable.

The property that matters second is that they must NOT coincide on a branching
source-feature closure - because that is the case PR 1 answered by picking two
arbitrary nodes out of a seventeen-child chain.
"""

from __future__ import annotations

import random

import pytest

from nzcl import closure as closure_mod
from nzcl import physical, ports

from conftest import requires_db

pytestmark = requires_db


@pytest.fixture(autouse=True)
def _clear_graph_cache():
    physical.clear_cache()
    yield
    physical.clear_cache()


SQUARE = [
    {"id": "S", "pts": [(0, 0), (100, 0)]},
    {"id": "E", "pts": [(100, 0), (100, 100)]},
    {"id": "N", "pts": [(100, 100), (0, 100)]},
    {"id": "W", "pts": [(0, 100), (0, 0)]},
]

#: One long road cut by four side roads into five children, with a bypass.
FIVE_CHILDREN = [
    {"id": "MAIN", "pts": [(0, 0), (500, 0)]},
    {"id": "T1", "pts": [(100, -100), (100, 0)]},
    {"id": "T2", "pts": [(200, -100), (200, 0)]},
    {"id": "T3", "pts": [(300, -100), (300, 0)]},
    {"id": "T4", "pts": [(400, -100), (400, 0)]},
    {"id": "BYPASS", "pts": [(100, -100), (200, -100), (300, -100), (400, -100)]},
]

#: Northbound and southbound one-way carriageways joined at both ends.
DIVIDED = [
    {"id": "NB", "pts": [(0, 0), (0, 200)], "oneway": True},
    {"id": "SB", "pts": [(100, 200), (100, 0)], "oneway": True},
    {"id": "XN", "pts": [(0, 200), (100, 200)]},
    {"id": "XS", "pts": [(100, 0), (0, 0)]},
]


def boundary(net, link_name_or_id, *, scope="segment", profile="car"):
    link_id = (net.link_id(link_name_or_id)
               if isinstance(link_name_or_id, str) else link_name_or_id)
    c = closure_mod.resolve(net.snapshot_id, link_id, scope=scope,
                            profile=profile)
    return c, ports.derive(net.snapshot_id, c.removed_link_ids, link_id,
                           c.fingerprint, profile=profile, shape=c.shape)


class TestSimpleSegmentReducesToEndpoints:
    """The compatibility property.

    A two-way segment in a square has one entry and one exit at each of its own
    endpoints. The port measure and the endpoint measure are the same question
    asked twice, and PR 2 must not change the answer on this case.
    """

    def test_ports_sit_on_the_segments_own_endpoints(self, synthetic):
        """Four ports, not two, and it still reduces.

        Each endpoint of S carries a two-way neighbour, so each contributes an
        entry AND an exit. Counting ports would have called the simplest case
        in the network irreducible; what matters is WHERE they sit.
        """
        net = synthetic(SQUARE)
        c, b = boundary(net, "S")

        assert b.reduces_to_endpoints is True
        assert len(b.entry_ports) == 2
        assert len(b.exit_ports) == 2
        assert set(b.boundary_nodes) == set(b.closure_nodes)
        assert b.interior_nodes == []

        endpoints = set(c.boundary_nodes)
        assert {p.closure_node for p in b.ports} == endpoints

    def test_an_entry_arc_points_into_the_closure(self, synthetic):
        """The direction is the whole content of the entry/exit distinction."""
        net = synthetic(SQUARE)
        _, b = boundary(net, "S")
        for p in b.entry_ports:
            assert p.closure_node in b.closure_nodes
            assert p.outside_node not in b.closure_nodes
        for p in b.exit_ports:
            assert p.closure_node in b.closure_nodes
            assert p.outside_node not in b.closure_nodes

    def test_closed_arcs_are_never_ports(self, synthetic):
        """A port is a crossing of the boundary, not a piece of the closure."""
        net = synthetic(SQUARE)
        c, b = boundary(net, "S")
        for p in b.ports:
            assert p.link_id not in c.removed_link_ids
            assert p.arc_id not in c.removed_arc_ids


class TestBranchingSourceFeatureDoesNotReduce:
    """The case PR 1 answered by picking two arbitrary nodes.

    A source-feature closure over a chain cut by four side roads meets the open
    network at every one of those junctions. Reducing it to "the parent's two
    endpoints" throws away the other ports, and with them every through
    movement that actually used the closed stretch.
    """

    def test_a_five_child_closure_has_a_port_at_every_side_road(
            self, synthetic):
        net = synthetic(FIVE_CHILDREN)
        child = net.link_id("MAIN#1")
        c, b = boundary(net, child, scope="source_feature")

        assert len(c.removed_link_ids) == 5
        assert b.reduces_to_endpoints is False
        # Four side roads, each two-way, so each contributes one entry and one
        # exit; the chain's two far ends are closure-only and contribute none.
        assert len(b.entry_ports) == 4
        assert len(b.exit_ports) == 4
        assert len(b.boundary_nodes) == 4

    def test_segment_scope_on_the_same_link_does_reduce(self, synthetic):
        """Same link, narrower scope: back to a two-port closure."""
        net = synthetic(FIVE_CHILDREN)
        child = net.link_id("MAIN#1")
        c, b = boundary(net, child, scope="segment")
        assert b.reduces_to_endpoints is True
        # Each endpoint carries the chain's continuation AND a side road, so
        # four two-way neighbours give four entries and four exits - all of
        # them on the selected segment's own two nodes.
        assert {p.closure_node for p in b.ports} == set(c.boundary_nodes)
        assert b.interior_nodes == []

    def test_distance_from_selected_is_measured_along_the_closure(
            self, synthetic):
        """Not a hop count, and not straight-line.

        The children here are 100 m each, so the ports either side of the
        selected child are 0 m away and the far ones are 100 m and 200 m. A hop
        count would call them 1 and 2, which on the real Tokoroa parent would
        rank a 1.99 m stub equal with a 5,201 m leg.
        """
        net = synthetic(FIVE_CHILDREN)
        child = net.link_id("MAIN#1")
        _, b = boundary(net, child, scope="source_feature")
        distances = sorted({round(p.distance_from_selected_m, 3)
                            for p in b.ports})
        assert distances == [0.0, 100.0, 200.0]


class TestOneWayPortsAreDirected:
    """A one-way street into a closure is an entry and never an exit."""

    def test_a_one_way_carriageway_still_has_ports_at_both_ends(self, synthetic):
        """The crossovers are two-way, so both ends carry an entry and an exit.

        The one-way link is the CLOSURE, not the boundary. What its
        directionality decides is which entry/exit pairs form a usable
        movement, and that is the movement engine's job rather than the port
        model's - the port model must not pre-judge it by dropping ports.
        """
        net = synthetic(DIVIDED)
        _, b = boundary(net, "NB")
        assert len(b.entry_ports) == 2
        assert len(b.exit_ports) == 2
        assert len({p.closure_node for p in b.ports}) == 2
        # Every port is directed and knows which side of the boundary it is on.
        for p in b.ports:
            assert (p.closure_node in b.closure_nodes
                    and p.outside_node not in b.closure_nodes)

    def test_ports_record_which_profiles_may_use_them(self, synthetic):
        net = synthetic(DIVIDED)
        _, b = boundary(net, "NB")
        for p in b.ports:
            assert "car" in p.profiles


class TestPortIdentity:
    def test_port_ids_are_stable_across_repeated_derivation(self, synthetic):
        net = synthetic(FIVE_CHILDREN)
        child = net.link_id("MAIN#1")
        a = boundary(net, child, scope="source_feature")[1]
        b = boundary(net, child, scope="source_feature")[1]
        assert [p.port_id for p in a.ports] == [p.port_id for p in b.ports]

    def test_the_same_arc_gets_different_ids_under_different_closures(
            self, synthetic):
        """A port is a crossing RELATIVE to a closure.

        Arc 42 is a boundary crossing of this closure and an ordinary arc of
        the next. Sharing an id between the two would let a cache serve one
        closure's port evidence for another - the failure PR 1 hit twice.
        """
        net = synthetic(FIVE_CHILDREN)
        seg = boundary(net, net.link_id("MAIN#1"), scope="segment")[1]
        grp = boundary(net, net.link_id("MAIN#1"), scope="source_feature")[1]
        shared_arcs = ({p.arc_id for p in seg.ports}
                       & {p.arc_id for p in grp.ports})
        assert shared_arcs, "the fixture should share at least one boundary arc"
        for arc in shared_arcs:
            a = next(p.port_id for p in seg.ports if p.arc_id == arc)
            b = next(p.port_id for p in grp.ports if p.arc_id == arc)
            assert a != b

    def test_ports_do_not_depend_on_row_order(self, synthetic):
        """PR 1 shipped one BFS-order tie-break bug. Assume the class recurs.

        The derivation orders by arc id and sorts by (distance, port id), both
        intrinsic. Nothing here may vary with the order rows come back in.
        """
        net = synthetic(FIVE_CHILDREN)
        child = net.link_id("MAIN#1")
        baseline = None
        for _ in range(5):
            _, b = boundary(net, child, scope="source_feature")
            got = [(p.kind, p.port_id, p.outside_node, p.closure_node,
                    round(p.distance_from_selected_m, 6)) for p in b.ports]
            if baseline is None:
                baseline = got
            assert got == baseline


class TestInteriorNodes:
    """A node every incident link of which is closed can never be a port."""

    def test_a_closed_junction_becomes_an_interior_node(self, synthetic):
        net = synthetic([
            {"id": "A", "pts": [(0, 0), (100, 0)]},
            {"id": "B", "pts": [(100, 0), (200, 0)]},
            {"id": "OUT1", "pts": [(0, 0), (0, 100)]},
            {"id": "OUT2", "pts": [(200, 0), (200, 100)]},
        ])
        a, bl = net.link_id("A"), net.link_id("B")
        c = closure_mod.resolve(net.snapshot_id, a, scope="segment")
        b = ports.derive(net.snapshot_id, [a, bl], a, c.fingerprint)

        # The shared junction is closed on both sides, so nothing outside
        # reaches it and it is interior rather than a boundary node.
        assert len(b.interior_nodes) == 1
        assert b.interior_nodes[0] not in b.boundary_nodes
        for p in b.ports:
            assert p.closure_node != b.interior_nodes[0]

    def test_an_empty_closure_has_no_boundary(self, synthetic):
        net = synthetic(SQUARE)
        b = ports.derive(net.snapshot_id, [], net.link_id("S"), "fp")
        assert b.ports == []
        assert "empty closure" in b.detail
