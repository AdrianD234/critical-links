"""The undirected physical-access graph, Gu.

Gu exists to answer one question: **what is still attached to what.** It has
one edge per graph link the profile may traverse in at least one direction, and
no notion of direction, cost or legality beyond that.

It is deliberately NOT the routing graph. `arcs` is directed and is the only
correct object for "can a vehicle drive from u to v". Using it for connectivity
is how V1 came to report a road as cut off when the only thing that happened
was that a one-way carriageway's downstream endpoint stopped being reachable
from its upstream one - an artefact of the endpoint pair, not a road losing
access. So:

    Gu  -> isolation, components, bridges.        Never a driving route.
    Gd  -> routes, replacement paths, direction.  Never an isolation headline.

Nothing in this module returns a path.

What is precomputed
-------------------
One iterative Tarjan pass over Gu yields, in linear time:

  * connected component id per node and per link
  * bridges - edges whose single removal splits their component
  * articulation points - nodes whose removal splits their component
  * biconnected component ids per edge
  * the DFS interval (tin, tout) per node

The interval is what makes a bridge closure exact without a search. In the DFS
spanning tree, removing a bridge separates precisely the subtree hanging below
it, and a node is in that subtree if and only if its `tin` lies in
[tin_child, tout_child]. Both resulting sides are therefore recovered by a
range test in O(1) per node.

Recursion is not used anywhere. The national graph has 338,182 nodes and a DFS
on it would exceed any reasonable stack limit; every traversal here is an
explicit stack.
"""

from __future__ import annotations

import time
from array import array
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from . import db
from .routing import Profile

#: Bump when the SHAPE of the derivation changes - a different edge-inclusion
#: rule, a different component definition. Persisted rows are keyed by it, so
#: an old precompute is never silently reused under new semantics.
DERIVATION_VERSION = "1.0.0"

_MODE_COLUMN = {"car": "mode_vehicle", "heavy": "mode_vehicle_heavy",
                "emergency": "mode_emergency"}


# ---------------------------------------------------------------- structure
@dataclass
class PhysicalGraph:
    """Gu for one snapshot and profile, plus everything Tarjan found.

    Node and link ids are the database's. Everything internal is a dense index
    into the arrays below; `node_index` and `link_index` map in, `node_ids` and
    `link_ids` map out.
    """

    snapshot_id: str
    profile: Profile
    derivation_version: str

    node_ids: list[int]
    node_index: dict[int, int]
    link_ids: list[int]
    link_index: dict[int, int]

    #: Endpoints of edge e, as dense node indices.
    edge_u: array
    edge_v: array
    edge_len: array
    #: 1 where the link's RCA is NZTA (rca_code 1). The state-highway anchor
    #: that decides which side of a cut retains the principal connection.
    edge_sh: bytearray

    #: CSR adjacency: neighbours of node i are adj_edge[adj_start[i]:adj_start[i+1]]
    adj_start: array
    adj_edge: array

    comp_of_node: array
    comp_of_edge: array
    tin: array
    tout: array
    is_articulation: bytearray
    is_bridge: bytearray
    bcc_of_edge: array
    #: For a bridge, the dense index of the DFS-child endpoint; -1 otherwise.
    bridge_child: array

    component_count: int = 0
    bcc_count: int = 0
    build_ms: int = 0
    #: Per component: node count, link count, road length, SH link count.
    comp_nodes: array = field(default_factory=lambda: array("i"))
    comp_links: array = field(default_factory=lambda: array("i"))
    comp_length: array = field(default_factory=lambda: array("d"))
    comp_sh_links: array = field(default_factory=lambda: array("i"))
    principal_component_id: int = -1
    principal_rule: str = ""

    # -- convenience ------------------------------------------------------
    def has_link(self, link_id: int) -> bool:
        return link_id in self.link_index

    def bridge(self, link_id: int) -> bool:
        e = self.link_index.get(link_id)
        return False if e is None else bool(self.is_bridge[e])

    def component_of_link(self, link_id: int) -> int:
        return int(self.comp_of_edge[self.link_index[link_id]])

    def degree(self, node_id: int) -> int:
        i = self.node_index[node_id]
        return int(self.adj_start[i + 1] - self.adj_start[i])


# ------------------------------------------------------------------- build
def _load_edges(snapshot_id: str, profile: Profile) -> list[tuple]:
    """One undirected edge per link traversable by `profile` either way.

    Read from `links`, not from `arcs`. A link is an edge of Gu when the
    profile may traverse it in at least one direction; collapsing the arc table
    would give the same answer but would make the definition depend on arc-row
    generation rather than on the link's own permissions.

    ORDER BY link_id is not cosmetic. Tarjan's output - which endpoint of a
    bridge is the child, which biconnected component gets which id - depends on
    the order edges are visited. Ordering the load makes the whole precompute a
    pure function of the snapshot, so two runs on the same data agree exactly.
    """
    mode = _MODE_COLUMN[profile]
    return [
        (r["link_id"], r["source_node"], r["target_node"], r["length_m"],
         r["rca_code"])
        for r in db.query(
            f"""
            SELECT link_id, source_node, target_node, length_m, rca_code
              FROM links
             WHERE snapshot_id = %s
               AND {mode}
               AND (forward_allowed OR reverse_allowed)
               AND source_node <> target_node
             ORDER BY link_id
            """,
            (snapshot_id,),
        )
    ]


def build(snapshot_id: str, profile: Profile = "car") -> PhysicalGraph:
    """Construct Gu for a snapshot from the database and analyse it."""
    return from_edges(snapshot_id, profile, _load_edges(snapshot_id, profile))


def from_edges(snapshot_id: str, profile: Profile,
               rows: Sequence[tuple]) -> PhysicalGraph:
    """Construct Gu from `(link_id, u, v, length_m, rca_code)` rows.

    Separated from the database read so the analysis can be exercised on a
    hand-built graph. The tests and the independent oracle both enter here;
    nothing about the algorithm depends on where the rows came from.
    """
    t0 = time.perf_counter()

    node_index: dict[int, int] = {}
    node_ids: list[int] = []

    def idx(node: int) -> int:
        i = node_index.get(node)
        if i is None:
            i = len(node_ids)
            node_index[node] = i
            node_ids.append(node)
        return i

    link_ids: list[int] = []
    link_index: dict[int, int] = {}
    edge_u = array("i")
    edge_v = array("i")
    edge_len = array("d")
    edge_sh = bytearray()

    for link_id, s, t, length, rca in rows:
        link_index[link_id] = len(link_ids)
        link_ids.append(link_id)
        edge_u.append(idx(s))
        edge_v.append(idx(t))
        edge_len.append(float(length))
        edge_sh.append(1 if rca == 1 else 0)

    n = len(node_ids)
    m = len(link_ids)

    # CSR adjacency, built with a counting pass so no per-node list is ever
    # allocated. At national scale the list-of-lists version costs ~10x here.
    deg = array("i", bytes(4 * (n + 1)))
    for e in range(m):
        deg[edge_u[e]] += 1
        deg[edge_v[e]] += 1
    adj_start = array("i", bytes(4 * (n + 1)))
    total = 0
    for i in range(n):
        adj_start[i] = total
        total += deg[i]
    adj_start[n] = total
    cursor = array("i", adj_start[:n])
    adj_edge = array("i", bytes(4 * total))
    for e in range(m):
        u, v = edge_u[e], edge_v[e]
        adj_edge[cursor[u]] = e
        cursor[u] += 1
        adj_edge[cursor[v]] = e
        cursor[v] += 1

    g = PhysicalGraph(
        snapshot_id=snapshot_id, profile=profile,
        derivation_version=DERIVATION_VERSION,
        node_ids=node_ids, node_index=node_index,
        link_ids=link_ids, link_index=link_index,
        edge_u=edge_u, edge_v=edge_v, edge_len=edge_len, edge_sh=edge_sh,
        adj_start=adj_start, adj_edge=adj_edge,
        comp_of_node=array("i", bytes(4 * n)),
        comp_of_edge=array("i", bytes(4 * m)),
        tin=array("i", bytes(4 * n)),
        tout=array("i", bytes(4 * n)),
        is_articulation=bytearray(n),
        is_bridge=bytearray(m),
        bcc_of_edge=array("i", bytes(4 * m)),
        bridge_child=array("i", bytes(4 * m)),
    )
    for e in range(m):
        g.bridge_child[e] = -1

    _tarjan(g)
    _summarise_components(g)
    _choose_principal(g)
    g.build_ms = int((time.perf_counter() - t0) * 1000)
    return g


def _tarjan(g: PhysicalGraph) -> None:
    """Iterative Tarjan: components, bridges, articulation points, BCCs.

    The classic formulation in one pass. `low[v]` is the smallest discovery
    time reachable from v's subtree using at most one back edge; a tree edge
    (u,v) is a bridge when `low[v] > tin[u]`, and u is an articulation point
    when some child has `low[v] >= tin[u]` (with the usual root exception of
    needing two children).

    Biconnected components come off an explicit edge stack: when a child
    closes with `low[v] >= tin[u]`, every edge pushed since that tree edge is
    one biconnected component and is popped together.

    The stack frame is (node, parent_edge, pointer into its CSR slice), so the
    walk is a plain loop. Nothing here recurses.
    """
    n = len(g.node_ids)
    tin, tout, low = g.tin, g.tout, array("i", bytes(4 * n))
    visited = bytearray(n)
    adj_start, adj_edge = g.adj_start, g.adj_edge
    eu, ev = g.edge_u, g.edge_v

    timer = 0
    comp_id = 0
    bcc_id = 0
    edge_stack: list[int] = []

    for root in range(n):
        if visited[root]:
            continue
        comp_id += 1
        # (node, parent_edge, cursor, root_child_count)
        stack: list[list[int]] = [[root, -1, adj_start[root], 0]]
        visited[root] = 1
        timer += 1
        tin[root] = low[root] = timer
        g.comp_of_node[root] = comp_id
        root_children = 0

        while stack:
            frame = stack[-1]
            u, pe, cur = frame[0], frame[1], frame[2]

            if cur < adj_start[u + 1]:
                frame[2] = cur + 1
                e = adj_edge[cur]
                if e == pe:
                    continue
                w = ev[e] if eu[e] == u else eu[e]
                if not visited[w]:
                    edge_stack.append(e)
                    visited[w] = 1
                    timer += 1
                    tin[w] = low[w] = timer
                    g.comp_of_node[w] = comp_id
                    g.comp_of_edge[e] = comp_id
                    if u == root:
                        root_children += 1
                    stack.append([w, e, adj_start[w], 0])
                elif tin[w] < tin[u]:
                    # Back edge, counted once - from the deeper endpoint only.
                    edge_stack.append(e)
                    g.comp_of_edge[e] = comp_id
                    if tin[w] < low[u]:
                        low[u] = tin[w]
                continue

            # u is finished. Fold it into its parent.
            tout[u] = timer
            stack.pop()
            if not stack:
                break
            parent = stack[-1][0]
            if low[u] < low[parent]:
                low[parent] = low[u]

            if low[u] > tin[parent]:
                g.is_bridge[pe] = 1
                g.bridge_child[pe] = u
            if low[u] >= tin[parent]:
                # Close one biconnected component at the tree edge (parent,u).
                bcc_id += 1
                while edge_stack:
                    top = edge_stack.pop()
                    g.bcc_of_edge[top] = bcc_id
                    if top == pe:
                        break
                if parent != root:
                    g.is_articulation[parent] = 1

        tout[root] = timer
        if root_children >= 2:
            g.is_articulation[root] = 1
        # Anything left belongs to this root's last component.
        if edge_stack:
            bcc_id += 1
            while edge_stack:
                g.bcc_of_edge[edge_stack.pop()] = bcc_id

    g.component_count = comp_id
    g.bcc_count = bcc_id


def _summarise_components(g: PhysicalGraph) -> None:
    c = g.component_count + 1
    g.comp_nodes = array("i", bytes(4 * c))
    g.comp_links = array("i", bytes(4 * c))
    g.comp_length = array("d", bytes(8 * c))
    g.comp_sh_links = array("i", bytes(4 * c))
    for i in range(len(g.node_ids)):
        g.comp_nodes[g.comp_of_node[i]] += 1
    for e in range(len(g.link_ids)):
        cid = g.comp_of_edge[e]
        g.comp_links[cid] += 1
        g.comp_length[cid] += g.edge_len[e]
        if g.edge_sh[e]:
            g.comp_sh_links[cid] += 1


def _choose_principal(g: PhysicalGraph) -> None:
    """Which component IS the network, for the purpose of "which side is cut off".

    Explicit and documented, because the alternative - taking whichever side is
    smaller - is exactly the V1 behaviour that turned an arbitrary tie into a
    headline. The rule, in order:

      1. the component carrying the most state-highway links;
      2. failing any state highway at all, the component with the most nodes.

    State highways come first because a large component of forestry and paper
    roads is not the principal connection however many nodes it has, and the
    RCA field is the only authority in the data on which roads carry the
    national network.
    """
    best, rule = -1, "no components"
    if g.component_count <= 0:
        g.principal_component_id, g.principal_rule = -1, rule
        return
    sh = [(g.comp_sh_links[c], g.comp_nodes[c], -c)
          for c in range(1, g.component_count + 1)]
    top_sh = max(sh)
    if top_sh[0] > 0:
        best, rule = -top_sh[2], "most state-highway links"
    else:
        nodes = [(g.comp_nodes[c], -c) for c in range(1, g.component_count + 1)]
        top = max(nodes)
        best, rule = -top[1], "most nodes (no state highway present)"
    g.principal_component_id, g.principal_rule = int(best), rule


# ------------------------------------------------------- closure analysis
@dataclass
class ResultingComponent:
    """One connected component of Gu after a closure."""

    node_count: int
    link_count: int
    road_length_m: float
    link_ids: list[int]
    state_highway_link_count: int
    #: True for the side that keeps the network's principal connection.
    retains_principal_connection: bool
    #: Carried because a component can hold nodes and NO links - a node whose
    #: every edge was closed is still a resulting component, and a partition
    #: described only by its links would silently drop it.
    node_ids: list[int] = field(default_factory=list)
    #: Which PRE-CLOSURE component this part came out of.
    #:
    #: Without it, parts of two components that were already disconnected from
    #: each other get compared as though they were alternatives. They are not:
    #: a closure cannot separate two things that were never joined.
    origin_component_id: int = -1


#: The most this PR will claim about how faithfully Gu models the real road
#: network. Never "high": Gu is built from INFERRED topology - AMDS publishes no
#: node identifiers, junctions are inferred where one link ends on the interior
#: of another, interior-to-interior crossings are deliberately left unconnected
#: to preserve grade separation, there is no z-level field, and the national
#: snapshot carries 50,000 recorded near-miss endpoints that are close together
#: and deliberately NOT joined. Any of those can turn a road that is connected
#: in reality into a bridge in Gu.
#:
#: The rule set that would justify "high" is not in this PR and is not pretended
#: to be. It needs, at least: z-level or grade-separation evidence per crossing,
#: a resolved disposition for every near miss, ferry-link handling, and a
#: published-node-identifier source to check inferred junctions against. Queued
#: explicitly rather than dropped.
TOPOLOGY_CONFIDENCE_CEILING = "medium"

#: A near miss within this distance of a node involved in the closure makes the
#: isolation claim doubtful: the two endpoints may be the same junction in
#: reality, in which case the "bridge" is not one.
NEAR_MISS_DOUBT_M = 25.0


@dataclass
class IsolationResult:
    """What a closure does to connectivity IN THE REPRESENTED GRAPH.

    Two different claims live here and they are deliberately not merged:

      `calculation_exact`  the partition was computed exactly, with no bound,
                           no sampling and no early termination. This is a
                           statement about the algorithm.

      `graph_exact`        whether Gu is an exact model of the physical road
                           network. It is not, and this is always False. Gu is
                           inferred topology; see TOPOLOGY_CONFIDENCE_CEILING.

    V1 published a single `exact: true` that readers could only take as the
    second. It was not even the first.

    `physically_isolates` is true only when at least one link that is NOT part
    of the closure ends up in a component with no principal connection. A
    closure that merely detaches itself isolates nothing.
    """

    #: The partition of the affected components is exact.
    calculation_exact: bool
    physically_isolates: bool
    method: str
    #: Every component the original component broke into, principal first.
    components: list[ResultingComponent] = field(default_factory=list)
    #: Union of the non-principal sides, excluding the closure's own links.
    separated_link_ids: list[int] = field(default_factory=list)
    separated_link_count: int = 0
    separated_length_m: float = 0.0
    origin_component_ids: list[int] = field(default_factory=list)
    closure_is_bridge: bool = False
    detail: str = ""

    #: Gu is inferred. This is never True and is carried so the distinction
    #: cannot be lost by a caller reading one boolean.
    graph_exact: bool = False
    #: high | medium | low. Capped at TOPOLOGY_CONFIDENCE_CEILING.
    topology_confidence: str = TOPOLOGY_CONFIDENCE_CEILING
    topology_confidence_reason: str = ""

    # --- which side is "cut off" is a POLICY, not a theorem -------------
    #: The partition itself. Exact, and independent of the choice below.
    partition_exact: bool = True
    #: How the principal side was chosen, in words the interface can show.
    principal_side_rule: str = ""
    #: high | low. Low when the anchor does not clearly favour one side.
    principal_side_confidence: str = "high"
    #: True when no side carries a decisive anchor, so naming one "cut off"
    #: would be asserting something the data does not support.
    principal_side_ambiguous: bool = False

    #: True when the separated link list was capped rather than enumerated in
    #: full. The counts and lengths stay exact either way.
    separated_truncated: bool = False

    #: How much of the graph was actually touched. A diagnostic, so a
    #: complexity claim can be checked against a counter rather than inferred
    #: from wall-clock time - which measures the machine, not the algorithm.
    nodes_examined: int = 0
    edges_examined: int = 0


def _edges_of(g: PhysicalGraph, link_ids: Iterable[int]) -> list[int]:
    out = []
    for lid in link_ids:
        e = g.link_index.get(lid)
        if e is not None:
            out.append(e)
    return out


def analyse_closure(g: PhysicalGraph, removed_link_ids: Sequence[int]) -> IsolationResult:
    """Exact connected components of Gu after removing `removed_link_ids`.

    Three paths, and only the first is genuinely a lookup:

      single link, not a bridge   O(1). It cannot disconnect anything; that is
                                  what "bridge" means.
      single link, is a bridge    walks the CHILD SUBTREE only, then derives
                                  the parent side by subtraction from the
                                  precomputed component aggregates. Bounded by
                                  the separated side, not by the network.
      anything else               BFS restricted to the components the closure
                                  touches. The rest of the network cannot have
                                  changed, so it is never visited.

    Every component of every affected pre-closure component is returned. The
    smallest is not selected and called "the affected side": on a multi-link
    closure that is a guess, and reporting all of them is the only honest
    answer.

    `calculation_exact` is about the algorithm. It says nothing about whether
    Gu models the real road network, which is what `graph_exact` and
    `topology_confidence` are for.
    """
    removed = set(_edges_of(g, removed_link_ids))
    if not removed:
        return IsolationResult(
            calculation_exact=True, physically_isolates=False,
            method="empty-closure", principal_side_rule="not applicable",
            detail="no link in the closure is an edge of the physical-access graph",
        )

    origin = sorted({int(g.comp_of_edge[e]) for e in removed})
    single = next(iter(removed))
    single_bridge = (len(removed) == 1 and bool(g.is_bridge[single]))

    # A single edge that is not a bridge cannot disconnect anything. That is
    # the definition of a bridge, not an approximation of it, so the answer is
    # a lookup - and this is by far the commonest case, one road selected on a
    # map. Enumerating the 239,000 links of the untouched principal component
    # to say "nothing changed" would cost more than the entire analysis.
    if len(removed) == 1 and not single_bridge:
        cid = int(g.comp_of_edge[single])
        return IsolationResult(
            calculation_exact=True, physically_isolates=False,
            method="precomputed-not-a-bridge",
            components=[ResultingComponent(
                node_count=int(g.comp_nodes[cid]),
                link_count=int(g.comp_links[cid]) - 1,
                road_length_m=float(g.comp_length[cid] - g.edge_len[single]),
                link_ids=[],
                # The closed link leaves the component, so its state-highway
                # indicator has to leave with it. Reporting the pre-closure
                # count here over-stated the remaining state highways by one
                # on every state-highway closure - the exact case a reader is
                # most likely to be looking at.
                state_highway_link_count=int(g.comp_sh_links[cid])
                - (1 if g.edge_sh[single] else 0),
                retains_principal_connection=True, node_ids=[],
                origin_component_id=cid,
            )],
            origin_component_ids=origin, closure_is_bridge=False,
            principal_side_rule="nothing was separated, so no side was chosen",
            detail="the closed link is not a bridge of the physical-access "
                   "graph, so its removal leaves every other link connected "
                   "exactly as before",
        )

    if single_bridge:
        comps, sides, n_ex, e_ex = _split_by_bridge(g, next(iter(removed)))
        method = "bridge-smaller-side-and-subtraction"
    else:
        comps, sides, n_ex, e_ex = _recompute_components(g, removed, origin)
        method = "per-component-bfs"

    principal = [c for c in comps if c.retains_principal_connection]
    others = [c for c in comps if not c.retains_principal_connection]

    # Counts and length come from the components, which are exact whether or
    # not the id list was capped. Only the DRAWABLE list is bounded.
    sep_count = sum(c.link_count for c in others)
    sep_length = float(sum(c.road_length_m for c in others))
    separated: list[int] = []
    truncated = False
    for c in others:
        for lid in c.link_ids:
            if len(separated) >= MAX_ENUMERATED_LINKS:
                truncated = True
                break
            separated.append(lid)
        if truncated:
            break
    separated.sort()

    # Ambiguity is judged only among the sides of components that ACTUALLY
    # SPLIT. A component that stayed whole made no choice, and folding it into
    # the comparison would let an untouched component make a real split look
    # like a coin toss.
    ambiguous = _anchor_ambiguous(sides) and bool(sep_count)
    rule = ("most state-highway links, then most nodes, within each "
            "pre-closure component"
            if sep_count else "nothing was separated, so no side was chosen")

    split_count = len({c.origin_component_id for c in others})
    return IsolationResult(
        calculation_exact=True,
        physically_isolates=bool(sep_count),
        method=method,
        components=principal + others,
        separated_link_ids=separated,
        separated_link_count=sep_count,
        separated_length_m=sep_length,
        separated_truncated=truncated,
        origin_component_ids=origin,
        closure_is_bridge=single_bridge,
        partition_exact=True,
        principal_side_rule=rule,
        principal_side_confidence="low" if ambiguous else "high",
        principal_side_ambiguous=ambiguous,
        nodes_examined=n_ex,
        edges_examined=e_ex,
        detail=(
            f"{len(comps)} part(s) result from removing {len(removed)} edge(s) "
            f"across {len(origin)} pre-closure component(s); "
            f"{split_count} of those component(s) actually split"
        ),
    )


def _split_by_bridge(g: PhysicalGraph, edge: int):
    """The two sides of a bridge, walking only the SMALLER of them.

    Both side sizes are known before any traversal, in O(1). DFS preorder
    assigns a contiguous block of discovery times to a subtree, so the child
    side's node count is exactly `tout[child] - tin[child] + 1` and the parent
    side's is the component's total minus that. No walk is needed to find out
    which side is smaller - only to enumerate it.

    That matters because the previous version always walked the CHILD subtree,
    and a bridge can sit near the far end of a component with the child side
    holding 95% of it. "Bounded by the separated side" was therefore not true;
    it was bounded by the DFS orientation, which is an artefact of where the
    traversal happened to start.

    Whichever side is walked, the other is derived by subtraction from the
    precomputed component aggregates, so the untouched remainder of the network
    is never visited. The only case that costs more is when the side that must
    be ENUMERATED is the larger one - the map draws the separated side and the
    response lists it, so if the smaller side turns out to be the principal one
    the larger has to be walked after all. That is rare: the smaller side would
    have to carry more state highways than the rest of its component.

    Returns (components, sides, nodes_examined, edges_examined).
    """
    child = int(g.bridge_child[edge])
    lo, hi = int(g.tin[child]), int(g.tout[child])
    cid = int(g.comp_of_edge[edge])
    removed = {edge}

    # O(1): contiguous DFS preorder interval.
    child_nodes = hi - lo + 1
    parent_nodes = int(g.comp_nodes[cid]) - child_nodes
    walk_child = child_nodes <= parent_nodes

    if walk_child:
        start, inside_test = child, lambda i: lo <= g.tin[i] <= hi
    else:
        # The parent endpoint of the bridge, and everything NOT in the subtree.
        u, v = int(g.edge_u[edge]), int(g.edge_v[edge])
        start = v if u == child else u
        inside_test = lambda i: not (lo <= g.tin[i] <= hi)  # noqa: E731

    walked, n_ex, e_ex = _walk_side(g, start, inside_test, removed)

    walked_side = _side(g, walked, removed, collect=True)
    other = _derive_other_side(g, cid, edge, walked_side)

    if walk_child:
        a, b = walked_side, other          # a = child side
    else:
        a, b = other, walked_side          # a = child side (derived)

    a_min, b_min = _side_minima(g, cid, walked, walk_child)
    a_principal = _rank_key(a[3], a[0], a_min) <= _rank_key(b[3], b[0], b_min)

    # Enumerate the SEPARATED side. Usually that is the side already walked; if
    # not, the other one has to be walked now.
    separated_is_walked = (a_principal != walk_child)
    if not separated_is_walked:
        rest = {i for i in _component_nodes(g, cid) if i not in walked}
        n_ex += len(_component_nodes(g, cid))
        enumerated = _side(g, rest, removed, collect=True)
        e_ex += enumerated[5]
        if walk_child:
            a, b = _strip_ids(walked_side), enumerated
        else:
            a, b = enumerated, _strip_ids(walked_side)

    return (
        [_as_component(a, a_principal, cid),
         _as_component(b, not a_principal, cid)],
        [a, b], n_ex, e_ex,
    )


def _walk_side(g: PhysicalGraph, start: int, inside_test, removed: set[int]):
    """Flood from `start`, staying inside `inside_test`. Counts what it touches."""
    inside = {start}
    stack = [start]
    n_ex = 1
    e_ex = 0
    while stack:
        u = stack.pop()
        for k in range(g.adj_start[u], g.adj_start[u + 1]):
            e = g.adj_edge[k]
            e_ex += 1
            if e in removed:
                continue
            w = g.edge_v[e] if g.edge_u[e] == u else g.edge_u[e]
            if w in inside or not inside_test(w):
                continue
            inside.add(w)
            n_ex += 1
            stack.append(w)
    return inside, n_ex, e_ex


def _derive_other_side(g: PhysicalGraph, cid: int, edge: int, walked):
    """The side that was not walked, by subtraction from component aggregates."""
    nodes = int(g.comp_nodes[cid]) - walked[0]
    links = int(g.comp_links[cid]) - walked[5] - 1
    length = float(g.comp_length[cid]) - walked[2] - float(g.edge_len[edge])
    sh = int(g.comp_sh_links[cid]) - walked[3] - (1 if g.edge_sh[edge] else 0)
    return (nodes, [], length, sh, [], links)


def _strip_ids(side):
    """Same aggregates, without the id lists. Used once the ids are not wanted."""
    return (side[0], [], side[2], side[3], [], side[5])


def _side_minima(g: PhysicalGraph, cid: int, walked: set[int], walk_child: bool):
    """Tie-break minima for (child side, parent side).

    Only the ORDER of the two matters, and only one side's ids are in hand, so
    the other's minimum is derived: the component's minimum unless that lies in
    the walked side, in which case the other's is strictly greater.
    """
    walked_min = _min_node_id(g, walked)
    comp_min = _component_min_node(g, cid)
    other_min = comp_min if comp_min != walked_min else walked_min + 1
    return (walked_min, other_min) if walk_child else (other_min, walked_min)


def _component_nodes(g: PhysicalGraph, cid: int) -> list[int]:
    """Node indices of one component, memoised on the graph.

    Only built when a bridge closure needs the parent side enumerated, which is
    the uncommon direction. Memoised because a caller in that position is
    likely to ask again.
    """
    cache = g.__dict__.setdefault("_comp_nodes", {})
    if cid not in cache:
        cache[cid] = [i for i in range(len(g.node_ids))
                      if g.comp_of_node[i] == cid]
    return cache[cid]


def _component_min_node(g: PhysicalGraph, cid: int) -> int:
    """Smallest node id in a component.

    Computed once per (graph, component) and memoised on the graph, because a
    bridge closure otherwise pays a full node scan for a tie-break that almost
    never decides anything.
    """
    cache = g.__dict__.setdefault("_comp_min", {})
    if cid not in cache:
        cache[cid] = min(
            (g.node_ids[i] for i in range(len(g.node_ids))
             if g.comp_of_node[i] == cid), default=-1)
    return cache[cid]


def _rank_key(sh: int, n_nodes: int, min_node_id: int) -> tuple:
    """Sort key for choosing the principal side. Lower sorts first.

    Most state-highway links, then most nodes, then the smallest node id.

    That last term is not decoration. Ties on the first two are common - a
    symmetric split of a rural network is the obvious case - and the previous
    tie-break was the group's index in the BFS, which follows node ordering,
    which follows edge ordering. On an exact tie the "cut off" side could
    therefore swap when the rows came back in a different order. A node id is
    intrinsic to the data, so the choice is now a function of the graph alone.

    Whether the tie was decisive at all is reported separately as
    `principal_side_ambiguous`; this only guarantees the answer is stable.
    """
    return (-sh, -n_nodes, min_node_id)


def _min_node_id(g: PhysicalGraph, node_idxs: set[int]) -> int:
    return min((g.node_ids[i] for i in node_idxs), default=-1)


def _anchor_ambiguous(sides: list[tuple]) -> bool:
    """Is the principal choice actually supported by the data?

    Ambiguous when the top two sides carry the same number of state-highway
    links AND neither is materially larger. Splitting a rural network in two
    equal halves with no state highway on either does not make one of them
    "cut off"; it makes the network split. Milford Sound is obvious, most of
    the network is not, and the interface must be able to tell the difference.
    """
    if len(sides) < 2:
        return False
    ranked = sorted(sides, key=lambda s: (-s[3], -s[0]))
    top, second = ranked[0], ranked[1]
    if top[3] != second[3]:
        return False
    bigger = max(top[0], second[0])
    smaller = min(top[0], second[0])
    return bigger < 2 * max(smaller, 1)


#: Above this many links, a resulting side is summarised rather than
#: enumerated. The separated sides are what the map draws and they are small;
#: the principal side is the rest of the country and listing it serves nothing.
MAX_ENUMERATED_LINKS = 20_000


def _side(g: PhysicalGraph, node_idxs: set[int], removed: set[int],
          collect: bool = True):
    """Roll up one node set into (nodes, link ids, length, SH count, node ids).

    `seen` deduplicates: an edge with both endpoints in the set is reached
    twice, and counting it twice would inflate the very figure the interface
    puts in a headline.

    `collect=False` returns counts and length without the id lists. The
    principal side is the rest of the country; enumerating it costs time and
    cache space to produce ids that nothing draws and the API strips.
    """
    links: list[int] = []
    n_links = 0
    length = 0.0
    sh = 0
    seen: set[int] = set()
    for i in node_idxs:
        for k in range(g.adj_start[i], g.adj_start[i + 1]):
            e = g.adj_edge[k]
            if e in removed or e in seen:
                continue
            seen.add(e)
            n_links += 1
            if collect:
                links.append(g.link_ids[e])
            length += g.edge_len[e]
            if g.edge_sh[e]:
                sh += 1
    links.sort()
    node_ids = sorted(g.node_ids[i] for i in node_idxs) if collect else []
    # The count is carried explicitly rather than left to len(link_ids), so an
    # un-enumerated side reports a true count instead of zero.
    return (len(node_idxs), links, float(length), sh, node_ids, n_links)


def _as_component(side, principal: bool, origin_component_id: int = -1
                  ) -> ResultingComponent:
    nodes, links, length, sh, node_ids, n_links = side
    return ResultingComponent(
        node_count=nodes, link_count=n_links, road_length_m=length,
        link_ids=links, state_highway_link_count=sh,
        retains_principal_connection=principal, node_ids=node_ids,
        origin_component_id=origin_component_id,
    )


def _recompute_components(g: PhysicalGraph, removed: set[int],
                          origin: Sequence[int]):
    """Re-partition each affected component INDEPENDENTLY.

    Each pre-closure component is its own universe. A closure cannot separate
    two things that were never joined, so a part of component 1 and a part of
    component 2 are not alternatives and must never be ranked against each
    other.

    The previous version flattened every node of every touched component into
    one traversal and then ranked ALL the resulting groups globally. When a
    closure touched two components that were already disconnected - reachable
    in production, because a `source_feature` closure can be `disjoint` - the
    lower-ranked component was reported as newly separated despite never having
    been connected to the other and never having split at all.

    The exactness argument was sound and is unchanged: every node of every
    touched component is labelled, and untouched components cannot have changed
    because no edge was removed from them. That justifies the PARTITION. It
    never justified the global ranking layered on top of it, and the two got
    conflated.

    Returns (components, sides, nodes_examined, edges_examined). `sides` holds
    only the sides of components that ACTUALLY SPLIT, because those are the
    only ones where a principal choice was made and so the only ones whose
    anchor can be ambiguous.
    """
    components: list[ResultingComponent] = []
    split_sides: list[tuple] = []
    n_ex = e_ex = 0

    for cid in origin:
        cid_removed = {e for e in removed if int(g.comp_of_edge[e]) == cid}
        members = _component_nodes(g, cid)
        n_ex += len(members)

        seen: set[int] = set()
        groups: list[set[int]] = []
        for start in members:
            if start in seen:
                continue
            bucket, walked_n, walked_e = _walk_side(
                g, start, lambda i: True, cid_removed)
            e_ex += walked_e
            seen |= bucket
            groups.append(bucket)

        # Rank WITHIN this component only.
        ranked = sorted(range(len(groups)),
                        key=lambda i: _rank_key(
                            _anchor_of(g, groups[i], cid_removed)[0],
                            len(groups[i]), _min_node_id(g, groups[i])))
        retained_i = ranked[0] if ranked else -1

        # A component that did not split has one part, and that part is
        # retained. Nothing came off it, so nothing is separated from it.
        did_split = len(groups) > 1
        sides = [_side(g, b, cid_removed, collect=(i != retained_i))
                 for i, b in enumerate(groups)]
        components.extend(
            _as_component(s, i == retained_i, cid) for i, s in enumerate(sides))
        if did_split:
            split_sides.extend(sides)

    return components, split_sides, n_ex, e_ex


def _anchor_of(g: PhysicalGraph, node_idxs: set[int], removed: set[int]):
    """State-highway link count and node count for a node set, nothing else.

    Cheap enough to run on every candidate side before deciding which one to
    enumerate, which is what lets the principal side stay un-enumerated.
    """
    sh = 0
    seen: set[int] = set()
    for i in node_idxs:
        for k in range(g.adj_start[i], g.adj_start[i + 1]):
            e = g.adj_edge[k]
            if e in removed or e in seen:
                continue
            seen.add(e)
            if g.edge_sh[e]:
                sh += 1
    return (sh, len(node_idxs))


# ------------------------------------------------------------- persistence
def persist(g: PhysicalGraph) -> None:
    """Write the precompute, replacing any previous run for the same key."""
    key = (g.snapshot_id, g.profile, g.derivation_version)
    with db.direct_connection(autocommit=False) as conn:
        with conn.cursor() as cur:
            for tbl in ("physical_access_nodes", "physical_access_links",
                        "physical_access_components", "physical_access_runs"):
                cur.execute(
                    f"DELETE FROM {tbl} WHERE snapshot_id=%s AND profile=%s "
                    f"AND derivation_version=%s", key)

            with cur.copy(
                "COPY physical_access_nodes (snapshot_id, profile, "
                "derivation_version, node_id, component_id, is_articulation, "
                "tin, tout) FROM STDIN"
            ) as cp:
                for i, node_id in enumerate(g.node_ids):
                    cp.write_row((g.snapshot_id, g.profile, g.derivation_version,
                                  node_id, int(g.comp_of_node[i]),
                                  bool(g.is_articulation[i]),
                                  int(g.tin[i]), int(g.tout[i])))

            with cur.copy(
                "COPY physical_access_links (snapshot_id, profile, "
                "derivation_version, link_id, u_node, v_node, length_m, "
                "component_id, is_bridge, bcc_id, bridge_child_node) FROM STDIN"
            ) as cp:
                for e, link_id in enumerate(g.link_ids):
                    child = int(g.bridge_child[e])
                    cp.write_row((
                        g.snapshot_id, g.profile, g.derivation_version, link_id,
                        g.node_ids[g.edge_u[e]], g.node_ids[g.edge_v[e]],
                        float(g.edge_len[e]), int(g.comp_of_edge[e]),
                        bool(g.is_bridge[e]), int(g.bcc_of_edge[e]),
                        None if child < 0 else g.node_ids[child]))

            with cur.copy(
                "COPY physical_access_components (snapshot_id, profile, "
                "derivation_version, component_id, node_count, link_count, "
                "road_length_m, state_highway_link_count) FROM STDIN"
            ) as cp:
                for c in range(1, g.component_count + 1):
                    cp.write_row((g.snapshot_id, g.profile, g.derivation_version,
                                  c, int(g.comp_nodes[c]), int(g.comp_links[c]),
                                  float(g.comp_length[c]),
                                  int(g.comp_sh_links[c])))

            cur.execute(
                "INSERT INTO physical_access_runs (snapshot_id, profile, "
                " derivation_version, build_ms, node_count, link_count, "
                " component_count, bridge_count, articulation_count, bcc_count, "
                " principal_component_id, principal_rule) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (g.snapshot_id, g.profile, g.derivation_version, g.build_ms,
                 len(g.node_ids), len(g.link_ids), g.component_count,
                 sum(g.is_bridge), sum(g.is_articulation), g.bcc_count,
                 g.principal_component_id, g.principal_rule),
            )
        conn.commit()


# --------------------------------------------------- topology confidence
def topology_confidence(snapshot_id: str, node_ids: Sequence[int],
                        ) -> tuple[str, str]:
    """How far the represented graph can be trusted AROUND THIS CLOSURE.

    Returns (confidence, reason). Capped at `TOPOLOGY_CONFIDENCE_CEILING`,
    which is "medium" and not "high", because Gu is inferred throughout and
    nothing in this PR establishes otherwise.

    One rule is implemented, and it is the one that bears directly on an
    isolation claim: a NEAR MISS is a pair of endpoints the ingest found close
    together and deliberately did not join. If there is one at a node involved
    in the closure, the two endpoints may be a single junction in reality - in
    which case the road the engine calls a bridge is not one, and the "cut off"
    claim is an artefact of the tolerance rather than a fact about the network.
    The national snapshot records 50,000 of these.

    Still to come, and named rather than quietly omitted:
      * grade separation - AMDS has no z-level field, so interior-to-interior
        crossings are never noded and a genuine overbridge is indistinguishable
        from a missed junction;
      * ferry links, which connect places no road does;
      * a published node-identifier source to check inferred junctions against;
      * a resolved disposition per near miss, so an investigated one stops
        counting against every closure near it.

    Until those exist this returns "medium" for everything else, which is a
    conservative default and is documented as one.
    """
    if not node_ids:
        return TOPOLOGY_CONFIDENCE_CEILING, (
            "no closure nodes to assess; Gu is inferred topology throughout")
    row = db.query_one(
        """
        SELECT count(*) AS n
          FROM near_misses nm
         WHERE nm.snapshot_id = %s
           AND EXISTS (
                 SELECT 1 FROM nodes n
                  WHERE n.snapshot_id = nm.snapshot_id
                    AND n.node_id = ANY(%s)
                    AND ST_DWithin(n.geom_2193, nm.geom_2193, %s))
        """,
        (snapshot_id, list(node_ids), NEAR_MISS_DOUBT_M),
    )
    n = int((row or {}).get("n") or 0)
    if n:
        return "low", (
            f"{n} unresolved near-miss endpoint(s) lie within "
            f"{NEAR_MISS_DOUBT_M:.0f} m of this closure. A near miss is a pair "
            f"of endpoints the ingest found close together and deliberately did "
            f"not join; if they are one junction in reality, this closure's "
            f"connectivity result is an artefact of that tolerance.")
    return TOPOLOGY_CONFIDENCE_CEILING, (
        "no unresolved near miss lies near this closure. Capped at medium "
        "because the represented graph is inferred throughout: AMDS publishes "
        "no node identifiers, junctions are inferred, and there is no z-level "
        "field to distinguish an overbridge from a missed junction.")


#: In-process cache. The graph is immutable for a (snapshot, profile), and
#: rebuilding it per request at national scale would dominate the analysis.
_CACHE: dict[tuple[str, str], PhysicalGraph] = {}


def get(snapshot_id: str, profile: Profile = "car") -> PhysicalGraph:
    key = (snapshot_id, profile)
    g = _CACHE.get(key)
    if g is None:
        g = build(snapshot_id, profile)
        _CACHE[key] = g
    return g


def clear_cache() -> None:
    _CACHE.clear()
