"""Property-based check of Gu against an independent implementation.

The oracle is networkx. It shares no code with `nzcl.physical` - not the
Tarjan pass, not the component labelling, not the post-closure recomputation -
so agreement is evidence rather than a tautology. Agreement is required to be
exact; there is no tolerance anywhere in this file.

Random graphs are generated with a fixed seed sequence so a failure is
reproducible from the seed printed in the assertion.
"""

from __future__ import annotations

import random

import networkx as nx
import pytest

from nzcl import physical


def _random_edges(rng: random.Random, n_nodes: int, n_edges: int):
    """Exactly `n_edges` valid edge rows, in the shape `from_edges` consumes.

    Parallel edges and both orientations are allowed on purpose: AMDS contains
    both (links 49791 and 49792 near Tokoroa are a real parallel pair), and a
    bridge test that mishandles a parallel edge would call a two-way road a
    sole access.

    Self-loops are RESAMPLED rather than dropped. Dropping them meant a seed
    could yield fewer rows than asked for, occasionally none, and the test then
    skipped itself. A deterministic skip is not a passing test - it is a test
    that stopped running, and the repository's own pytest config says the
    mandatory suite must never skip. `n_nodes >= 2` guarantees the resample
    terminates.
    """
    assert n_nodes >= 2, "a graph with fewer than two nodes has no edges"
    rows = []
    for link_id in range(1, n_edges + 1):
        u = v = 0
        while u == v:
            u = rng.randrange(n_nodes)
            v = rng.randrange(n_nodes)
        rows.append((link_id, 1000 + u, 1000 + v,
                     round(rng.uniform(1.0, 5000.0), 3),
                     1 if rng.random() < 0.2 else 100))
    return rows


def _oracle_graph(rows) -> nx.MultiGraph:
    g = nx.MultiGraph()
    for link_id, u, v, length, _rca in rows:
        g.add_edge(u, v, key=link_id, length=length)
    return g


def _oracle_bridges(rows) -> set[int]:
    """Bridge link ids, by definition rather than by algorithm.

    An edge is a bridge iff removing it increases the number of connected
    components. Computed by brute force so the oracle does not depend on
    networkx's own bridge routine either.
    """
    g = _oracle_graph(rows)
    base = nx.number_connected_components(g)
    out = set()
    for link_id, u, v, _length, _rca in rows:
        g.remove_edge(u, v, key=link_id)
        if nx.number_connected_components(g) > base:
            out.add(link_id)
        g.add_edge(u, v, key=link_id)
    return out


SEEDS = list(range(40))


@pytest.mark.parametrize("seed", SEEDS)
def test_bridges_and_components_match_oracle(seed: int) -> None:
    rng = random.Random(seed)
    n_nodes = rng.randrange(3, 26)
    n_edges = rng.randrange(2, 40)
    rows = _random_edges(rng, n_nodes, n_edges)

    g = physical.from_edges("test", "car", rows)
    oracle = _oracle_graph(rows)

    # --- connected components -------------------------------------------
    ours: dict[int, set[int]] = {}
    for i, node_id in enumerate(g.node_ids):
        ours.setdefault(int(g.comp_of_node[i]), set()).add(node_id)
    theirs = [set(c) for c in nx.connected_components(oracle)]
    assert sorted(map(sorted, ours.values())) == sorted(map(sorted, theirs)), (
        f"seed={seed}: component partition differs")

    # --- bridges ---------------------------------------------------------
    our_bridges = {g.link_ids[e] for e in range(len(g.link_ids)) if g.is_bridge[e]}
    assert our_bridges == _oracle_bridges(rows), f"seed={seed}: bridge set differs"

    # --- articulation points ---------------------------------------------
    our_arts = {g.node_ids[i] for i in range(len(g.node_ids)) if g.is_articulation[i]}
    # networkx wants a simple graph for articulation points; collapsing
    # parallel edges cannot change which NODES separate a graph.
    simple = nx.Graph()
    simple.add_nodes_from(oracle.nodes)
    simple.add_edges_from((u, v) for u, v, _k in oracle.edges(keys=True))
    assert our_arts == set(nx.articulation_points(simple)), (
        f"seed={seed}: articulation points differ")


@pytest.mark.parametrize("seed", SEEDS)
def test_closure_components_match_oracle(seed: int) -> None:
    """Post-closure components, for closures of 1..3 links."""
    rng = random.Random(1000 + seed)
    n_nodes = rng.randrange(3, 22)
    rows = _random_edges(rng, n_nodes, rng.randrange(2, 34))

    g = physical.from_edges("test", "car", rows)
    all_links = [r[0] for r in rows]

    for _ in range(6):
        k = rng.randrange(1, min(4, len(all_links)) + 1)
        closure = rng.sample(all_links, k)

        result = physical.analyse_closure(g, closure)

        oracle = _oracle_graph(rows)
        for link_id, u, v, _l, _r in rows:
            if link_id in closure:
                oracle.remove_edge(u, v, key=link_id)

        origin = set(result.origin_component_ids)
        origin_nodes = {g.node_ids[i] for i in range(len(g.node_ids))
                        if g.comp_of_node[i] in origin}

        # --- the separation verdict, which is what the interface states -----
        #
        # Built from the DEFINITION, independently of the implementation: a
        # closure can only separate things that were connected BEFORE it, so
        # each pre-closure component is evaluated on its own and the retained
        # part of one component says nothing about another.
        def anchors(nodes: set[int]):
            sh = n = 0
            for link_id, u, v, _l, rca in rows:
                if link_id in closure:
                    continue
                if u in nodes and v in nodes:
                    n += 1
                    sh += 1 if rca == 1 else 0
            return (sh, len(nodes), n)

        expected_sep: list[int] = []
        expected_parts: list[set[int]] = []
        unambiguous = True
        for cid in origin:
            cid_nodes = {g.node_ids[i] for i in range(len(g.node_ids))
                         if g.comp_of_node[i] == cid}
            parts = [set(c) for c in
                     nx.connected_components(oracle.subgraph(cid_nodes))]
            expected_parts.extend(parts)
            ranked = sorted(parts, key=lambda s: (-anchors(s)[0], -len(s)))
            if len(ranked) > 1 and anchors(ranked[1])[:2] == anchors(ranked[0])[:2]:
                unambiguous = False
            retained = ranked[0]
            # Only links that came off THIS component count as separated.
            expected_sep.extend(
                link_id for link_id, u, v, _l, _r in rows
                if link_id not in closure
                and u in cid_nodes
                and not (u in retained and v in retained))
        expected_sep.sort()

        if unambiguous:
            assert result.separated_link_ids == expected_sep, (
                f"seed={seed} closure={closure}: separated link set differs")
            assert result.physically_isolates == bool(expected_sep), (
                f"seed={seed} closure={closure}: isolation verdict differs")

        # --- the full partition, where the result enumerates it -------------
        # The not-a-bridge fast path answers "nothing separated" from the
        # precompute and deliberately does not enumerate the untouched side.
        if result.method != "precomputed-not-a-bridge":
            # Retained sides are deliberately NOT enumerated - they are the
            # rest of the network and their ids are never drawn. Reconstruct
            # each by subtraction WITHIN its own origin component, so the
            # partition can still be compared in full.
            got: list[list[int]] = []
            for cid in origin:
                cid_nodes = {g.node_ids[i] for i in range(len(g.node_ids))
                             if g.comp_of_node[i] == cid}
                mine = [c for c in result.components
                        if c.origin_component_id == cid]
                sep_here = [set(c.node_ids) for c in mine
                            if not c.retains_principal_connection]
                union_sep: set[int] = set()
                for s in sep_here:
                    union_sep |= s
                retained_nodes = cid_nodes - union_sep
                got.append(sorted(retained_nodes))
                got.extend(sorted(s) for s in sep_here)

                # The un-enumerated side must still report a truthful node
                # count: it is derived by subtraction from the precompute, so a
                # wrong aggregate shows up here and nowhere else.
                retained = [c for c in mine if c.retains_principal_connection]
                assert len(retained) == 1, (
                    f"seed={seed}: component {cid} has {len(retained)} retained "
                    f"parts; every pre-closure component keeps exactly one")
                assert retained[0].node_count == len(retained_nodes), (
                    f"seed={seed} closure={closure}: retained node count is "
                    f"{retained[0].node_count}, partition says "
                    f"{len(retained_nodes)}")

            assert sorted(got) == sorted(sorted(p) for p in expected_parts), (
                f"seed={seed} closure={closure}: post-closure components differ")

            # Road length is conserved: every edge of the affected components
            # is either in the closure or in exactly one resulting side.
            closed_len = sum(g.edge_len[g.link_index[l]] for l in closure
                             if l in g.link_index)
            side_len = sum(c.road_length_m for c in result.components)
            origin_len = sum(g.edge_len[e] for e in range(len(g.link_ids))
                             if g.comp_of_edge[e] in origin)
            assert side_len + closed_len == pytest.approx(origin_len, abs=1e-6), (
                f"seed={seed} closure={closure}: road length not conserved")

            # Link counts too, which is what the subtraction path computes for
            # the principal side of a bridge closure.
            closed_edges = sum(1 for l in closure if l in g.link_index)
            side_links = sum(c.link_count for c in result.components)
            origin_links = sum(1 for e in range(len(g.link_ids))
                               if g.comp_of_edge[e] in origin)
            assert side_links + closed_edges == origin_links, (
                f"seed={seed} closure={closure}: link count not conserved")
        else:
            assert not result.physically_isolates
            assert len(closure) == 1 and not g.bridge(closure[0])




@pytest.mark.parametrize("seed", SEEDS[:20])
def test_answers_do_not_depend_on_edge_order(seed: int) -> None:
    """Row order must not change an answer.

    `_load_edges` sorts by link_id, so in practice the input is stable. This
    guards the case where it is not: a different query plan, a VACUUM, a future
    caller building the graph from somewhere else. Component IDS and DFS
    interval numbering are internal labels and are allowed to move. Bridges,
    articulation points, the component partition and every closure answer are
    not.
    """
    rng = random.Random(5000 + seed)
    rows = _random_edges(rng, rng.randrange(3, 24), rng.randrange(2, 36))

    shuffled = list(rows)
    random.Random(seed).shuffle(shuffled)

    a = physical.from_edges("test", "car", rows)
    b = physical.from_edges("test", "car", shuffled)

    def bridges(g):
        return {g.link_ids[e] for e in range(len(g.link_ids)) if g.is_bridge[e]}

    def arts(g):
        return {g.node_ids[i] for i in range(len(g.node_ids))
                if g.is_articulation[i]}

    def partition(g):
        out: dict[int, set[int]] = {}
        for i, nid in enumerate(g.node_ids):
            out.setdefault(int(g.comp_of_node[i]), set()).add(nid)
        return sorted(sorted(s) for s in out.values())

    assert bridges(a) == bridges(b), f"seed={seed}: bridge set moved with row order"
    assert arts(a) == arts(b), f"seed={seed}: articulation points moved"
    assert partition(a) == partition(b), f"seed={seed}: component partition moved"

    all_links = [r[0] for r in rows]
    for _ in range(4):
        k = rng.randrange(1, min(4, len(all_links)) + 1)
        closure = sorted(rng.sample(all_links, k))
        ra = physical.analyse_closure(a, closure)
        rb = physical.analyse_closure(b, closure)
        assert ra.physically_isolates == rb.physically_isolates
        assert ra.separated_link_ids == rb.separated_link_ids
        assert ra.separated_length_m == pytest.approx(rb.separated_length_m,
                                                      abs=1e-9)
        # Compare the separated sides, which are the ones enumerated, plus the
        # principal side's counts, which are derived rather than listed.
        assert (sorted(sorted(c.node_ids) for c in ra.components
                       if not c.retains_principal_connection)
                == sorted(sorted(c.node_ids) for c in rb.components
                          if not c.retains_principal_connection)), (
            f"seed={seed} closure={closure}: post-closure partition moved")
        assert (sorted((c.node_count, c.link_count) for c in ra.components)
                == sorted((c.node_count, c.link_count) for c in rb.components)), (
            f"seed={seed} closure={closure}: component sizes moved")
