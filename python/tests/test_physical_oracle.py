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
    """Edge rows in the shape `from_edges` consumes.

    Parallel edges and both orientations are allowed on purpose: AMDS contains
    both (links 49791 and 49792 near Tokoroa are a real parallel pair), and a
    bridge test that mishandles a parallel edge would call a two-way road a
    sole access.
    """
    rows = []
    for link_id in range(1, n_edges + 1):
        u = rng.randrange(n_nodes)
        v = rng.randrange(n_nodes)
        if u == v:
            continue  # Gu excludes self-loops, as `_load_edges` does
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
    if not rows:
        pytest.skip("degenerate graph")

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
    if len(rows) < 2:
        pytest.skip("degenerate graph")

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
        oracle_parts = [set(c) for c in
                        nx.connected_components(oracle.subgraph(origin_nodes))]

        # --- the separation verdict, which is what the interface states -----
        # Reproduce the documented principal rule on the oracle's own
        # partition: most state-highway links, then most nodes.
        def anchors(nodes: set[int]):
            sh = n = 0
            for link_id, u, v, _l, rca in rows:
                if link_id in closure:
                    continue
                if u in nodes and v in nodes:
                    n += 1
                    sh += 1 if rca == 1 else 0
            return (sh, len(nodes), n)

        ranked = sorted(oracle_parts, key=lambda s: (-anchors(s)[0], -len(s)))
        top = anchors(ranked[0])[:2]
        unambiguous = len(ranked) == 1 or anchors(ranked[1])[:2] != top

        if unambiguous:
            expected_sep = sorted(
                link_id for link_id, u, v, _l, _r in rows
                if link_id not in closure
                and u in origin_nodes
                and not (u in ranked[0] and v in ranked[0]))
            assert result.separated_link_ids == expected_sep, (
                f"seed={seed} closure={closure}: separated link set differs")
            assert result.physically_isolates == bool(expected_sep), (
                f"seed={seed} closure={closure}: isolation verdict differs")

        # --- the full partition, where the result enumerates it -------------
        # The not-a-bridge fast path answers "nothing separated" from the
        # precompute and deliberately does not enumerate the untouched side.
        if result.method != "precomputed-not-a-bridge":
            got = sorted(sorted(c.node_ids) for c in result.components)
            assert got == sorted(sorted(p) for p in oracle_parts), (
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
    if len(rows) < 2:
        pytest.skip("degenerate graph")

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
        assert (sorted(sorted(c.node_ids) for c in ra.components)
                == sorted(sorted(c.node_ids) for c in rb.components)), (
            f"seed={seed} closure={closure}: post-closure partition moved")
