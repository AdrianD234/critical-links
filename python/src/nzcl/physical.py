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


@dataclass
class IsolationResult:
    """What a closure does to physical connectivity. Exact, or it says so.

    `physically_isolates` is true only when at least one link that is NOT part
    of the closure ends up in a component with no principal connection. A
    closure that merely detaches itself isolates nothing.
    """

    exact: bool
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


def _edges_of(g: PhysicalGraph, link_ids: Iterable[int]) -> list[int]:
    out = []
    for lid in link_ids:
        e = g.link_index.get(lid)
        if e is not None:
            out.append(e)
    return out


def analyse_closure(g: PhysicalGraph, removed_link_ids: Sequence[int]) -> IsolationResult:
    """Exact connected components of Gu after removing `removed_link_ids`.

    Single bridge closures are answered from the precomputed DFS intervals with
    no traversal at all. Everything else is a BFS restricted to the components
    the closure actually touches - the rest of the network cannot change, so it
    is never visited.

    Every component of every affected pre-closure component is returned. The
    smallest is not selected and called "the affected side": on a multi-link
    closure that is a guess, and reporting all of them is the only honest
    answer.
    """
    removed = set(_edges_of(g, removed_link_ids))
    if not removed:
        return IsolationResult(
            exact=True, physically_isolates=False, method="empty-closure",
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
            exact=True, physically_isolates=False,
            method="precomputed-not-a-bridge",
            components=[ResultingComponent(
                node_count=int(g.comp_nodes[cid]),
                link_count=int(g.comp_links[cid]) - 1,
                road_length_m=float(g.comp_length[cid] - g.edge_len[single]),
                link_ids=[], state_highway_link_count=int(g.comp_sh_links[cid]),
                retains_principal_connection=True, node_ids=[],
            )],
            origin_component_ids=origin, closure_is_bridge=False,
            detail="the closed link is not a bridge of the physical-access "
                   "graph, so its removal leaves every other link connected "
                   "exactly as before",
        )

    if single_bridge:
        comps = _split_by_bridge(g, next(iter(removed)))
        method = "precomputed-bridge-interval"
    else:
        comps = _recompute_components(g, removed, origin)
        method = "restricted-bfs"

    principal = [c for c in comps if c.retains_principal_connection]
    others = [c for c in comps if not c.retains_principal_connection]
    separated: list[int] = []
    for c in others:
        separated.extend(c.link_ids)
    separated.sort()
    length = sum(g.edge_len[g.link_index[l]] for l in separated)

    return IsolationResult(
        exact=True,
        physically_isolates=bool(separated),
        method=method,
        components=principal + others,
        separated_link_ids=separated,
        separated_link_count=len(separated),
        separated_length_m=float(length),
        origin_component_ids=origin,
        closure_is_bridge=single_bridge,
        detail=(
            f"{len(comps)} component(s) result from removing "
            f"{len(removed)} edge(s) of {len(origin)} original component(s)"
        ),
    )


def _split_by_bridge(g: PhysicalGraph, edge: int) -> list[ResultingComponent]:
    """The two sides of a bridge, straight off the DFS intervals.

    No traversal. The child side is exactly {v : tin[child] <= tin[v] <= tout[child]}
    within the bridge's component, and the parent side is the rest of it.
    """
    child = int(g.bridge_child[edge])
    lo, hi = int(g.tin[child]), int(g.tout[child])
    cid = int(g.comp_of_edge[edge])
    removed = {edge}

    inside: set[int] = set()
    outside: set[int] = set()
    for i in range(len(g.node_ids)):
        if g.comp_of_node[i] != cid:
            continue
        (inside if lo <= g.tin[i] <= hi else outside).add(i)

    a = _side(g, inside, removed)
    b = _side(g, outside, removed)
    # The principal side is the one holding the network's principal component's
    # anchors; with a single component split in two, that is the side with the
    # state highways, else the larger side.
    if (a[3], a[0]) >= (b[3], b[0]):
        a_principal, b_principal = True, False
    else:
        a_principal, b_principal = False, True
    return [
        _as_component(a, a_principal),
        _as_component(b, b_principal),
    ]


#: Above this many links, a resulting side is summarised rather than
#: enumerated. The separated sides are what the map draws and they are small;
#: the principal side is the rest of the country and listing it serves nothing.
MAX_ENUMERATED_LINKS = 20_000


def _side(g: PhysicalGraph, node_idxs: set[int], removed: set[int]):
    """Roll up one node set into (nodes, link ids, length, SH count, node ids).

    `seen` deduplicates: an edge with both endpoints in the set is reached
    twice, and counting it twice would inflate the very figure the interface
    puts in a headline.
    """
    links: list[int] = []
    length = 0.0
    sh = 0
    seen: set[int] = set()
    for i in node_idxs:
        for k in range(g.adj_start[i], g.adj_start[i + 1]):
            e = g.adj_edge[k]
            if e in removed or e in seen:
                continue
            seen.add(e)
            links.append(g.link_ids[e])
            length += g.edge_len[e]
            if g.edge_sh[e]:
                sh += 1
    links.sort()
    node_ids = sorted(g.node_ids[i] for i in node_idxs)
    return (len(node_idxs), links, float(length), sh, node_ids)


def _as_component(side, principal: bool) -> ResultingComponent:
    nodes, links, length, sh, node_ids = side
    return ResultingComponent(
        node_count=nodes, link_count=len(links), road_length_m=length,
        link_ids=links, state_highway_link_count=sh,
        retains_principal_connection=principal, node_ids=node_ids,
    )


def _recompute_components(g: PhysicalGraph, removed: set[int],
                          origin: Sequence[int]) -> list[ResultingComponent]:
    """BFS the affected components only, with `removed` deleted.

    Exact by construction: every node of every touched component is labelled,
    and untouched components cannot have changed because no edge was removed
    from them.
    """
    origin_set = set(origin)
    members = [i for i in range(len(g.node_ids))
               if g.comp_of_node[i] in origin_set]
    seen: dict[int, int] = {}
    groups: list[set[int]] = []

    for start in members:
        if start in seen:
            continue
        gid = len(groups)
        bucket: set[int] = {start}
        seen[start] = gid
        stack = [start]
        while stack:
            u = stack.pop()
            for k in range(g.adj_start[u], g.adj_start[u + 1]):
                e = g.adj_edge[k]
                if e in removed:
                    continue
                w = g.edge_v[e] if g.edge_u[e] == u else g.edge_u[e]
                if w not in seen:
                    seen[w] = gid
                    bucket.add(w)
                    stack.append(w)
        groups.append(bucket)

    sides = [_side(g, b, removed) for b in groups]
    # The principal side is the one containing the most state-highway anchors,
    # falling back to node count. Documented in _choose_principal.
    order = sorted(range(len(sides)), key=lambda i: (-sides[i][3], -sides[i][0], i))
    principal_i = order[0] if order else -1
    return [_as_component(s, i == principal_i) for i, s in enumerate(sides)]


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
