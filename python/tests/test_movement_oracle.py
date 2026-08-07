"""The boundary-movement engine against an independent implementation.

The oracle is networkx plus the raw `arcs` and `links` tables. It shares NO
algorithm code with the engine: not the port derivation, not the movement test,
not the routing, not the replacement. It does not import `nzcl.ports`,
`nzcl.movements`, `nzcl.replacement`, `nzcl.corridor` or `nzcl.routing`, and
`nzcl.db` is used only to read rows. The moment the two share code, agreement
stops being evidence.

Agreement is required to be EXACT. There is one tolerance in this file, on
floating-point distance sums, and it is 1e-6 metres - a millionth of a
millimetre, which is round-off, not slack.

WHAT IS CHECKED
---------------
  path cost              intact and replacement distances, to the metre
  closure removal        what the closure takes out, derived independently
  port-pair validity     which arcs cross the boundary, and in which direction
  movement connectivity  which crossings the closure is NECESSARY for
  determinism            the same answers after every id is reassigned

WHERE THE ORACLE DELIBERATELY SAYS NOTHING
------------------------------------------
Where a crossing has two routes of EXACTLY equal cost, one through the closure
and one round it, which one a shortest-path search returns is a choice among
equals. The oracle asserts the strict cases in both directions and leaves the
tie alone; see the header of `nzcl.movements`. It does NOT weaken to "roughly
agrees" - it declines to make a claim that is not determined by the data.
"""

from __future__ import annotations

import math
import random

import networkx as nx
import pytest

from nzcl import closure as closure_mod
from nzcl import db, impactv2, movements, ports
from nzcl import replacement as repl_mod

from conftest import requires_db

pytestmark = requires_db

#: Round-off only. Both sides sum the same doubles in different orders.
EPS = 1e-6


# ====================================================================== oracle
class Oracle:
    """A second implementation, built from the tables and nothing else."""

    def __init__(self, snapshot_id: str, profile_column: str = "mode_vehicle"):
        self.snapshot_id = snapshot_id
        self.arcs = db.query(
            f"SELECT arc_id, link_id, source, target, cost_distance_m "
            f"  FROM arcs WHERE snapshot_id=%s AND {profile_column}",
            (snapshot_id,))
        self.links = db.query(
            "SELECT link_id, amds_id, closure_group_id, source_node, "
            "       target_node, length_m FROM links WHERE snapshot_id=%s",
            (snapshot_id,))
        self.coord = {
            int(r["node_id"]): (round(float(r["x"]), 3), round(float(r["y"]), 3))
            for r in db.query(
                "SELECT node_id, ST_X(geom_2193) AS x, ST_Y(geom_2193) AS y "
                "  FROM nodes WHERE snapshot_id=%s", (snapshot_id,))}

    # -- what a closure removes ------------------------------------------
    def removed_arcs(self, removed_link_ids) -> set[int]:
        want = set(int(x) for x in removed_link_ids)
        return {int(a["arc_id"]) for a in self.arcs if int(a["link_id"]) in want}

    def closure_nodes(self, removed_link_ids) -> set[int]:
        want = set(int(x) for x in removed_link_ids)
        out = set()
        for l in self.links:
            if int(l["link_id"]) in want:
                out.add(int(l["source_node"]))
                out.add(int(l["target_node"]))
        return out

    # -- where the closure meets the open network -------------------------
    def boundary_arcs(self, removed_link_ids):
        """(entry, exit) as sets of (arc_id, outside_node, closure_node).

        An ENTRY runs from outside into the closure; an EXIT runs out of it.
        Derived from the definition, with no reference to how `nzcl.ports`
        does it.
        """
        closed_links = set(int(x) for x in removed_link_ids)
        cn = self.closure_nodes(removed_link_ids)
        entry, exit_ = set(), set()
        for a in self.arcs:
            if int(a["link_id"]) in closed_links:
                continue
            s, t = int(a["source"]), int(a["target"])
            if t in cn and s not in cn:
                entry.add((int(a["arc_id"]), s, t))
            elif s in cn and t not in cn:
                exit_.add((int(a["arc_id"]), s, t))
        return entry, exit_

    # -- shortest paths ---------------------------------------------------
    def graph(self, excluded_arcs=()) -> nx.DiGraph:
        """Cheapest parallel arc per ordered node pair. Directed."""
        excl = set(int(x) for x in excluded_arcs)
        g = nx.DiGraph()
        g.add_nodes_from(self.coord)
        for a in self.arcs:
            if int(a["arc_id"]) in excl:
                continue
            u, v, w = int(a["source"]), int(a["target"]), float(a["cost_distance_m"])
            if not g.has_edge(u, v) or g[u][v]["weight"] > w:
                g.add_edge(u, v, weight=w)
        return g

    def distance(self, u: int, v: int, excluded_arcs=()) -> float | None:
        g = self.graph(excluded_arcs)
        if u not in g or v not in g:
            return None
        try:
            return nx.shortest_path_length(g, u, v, weight="weight")
        except nx.NetworkXNoPath:
            return None


# ================================================================== networks
def grid_spec(rng: random.Random, size: int = 4, spacing: float = 100.0,
              drop: float = 0.25, oneway: float = 0.2) -> list[dict]:
    """A random sub-grid. Endpoints coincide exactly, so nothing is split.

    Grid coordinates are exact multiples of `spacing`, which keeps every
    junction a genuine shared node rather than something the 10 mm assignment
    tolerance had to decide about. Interior crossings cannot occur, so the
    fixture never silently exercises grade separation.
    """
    spec = []
    for i in range(size):
        for j in range(size):
            for di, dj, tag in ((1, 0, "h"), (0, 1, "v")):
                a, b = i + di, j + dj
                if a >= size or b >= size:
                    continue
                if rng.random() < drop:
                    continue
                spec.append({
                    "id": f"{tag}-{i}-{j}",
                    "pts": [(i * spacing, j * spacing), (a * spacing, b * spacing)],
                    "oneway": rng.random() < oneway,
                    "road_name": f"Road {tag.upper()}{j if tag == 'h' else i}",
                })
    return spec


def closable_links(net) -> list[int]:
    rows = db.query(
        "SELECT link_id FROM links WHERE snapshot_id=%s "
        "  AND source_node <> target_node ORDER BY link_id",
        (net.snapshot_id,))
    return [int(r["link_id"]) for r in rows]


def run_engine(net, link_id):
    c = closure_mod.resolve(net.snapshot_id, link_id)
    b = ports.derive(net.snapshot_id, c.removed_link_ids, link_id,
                     c.fingerprint, shape=c.shape)
    ms = movements.identify(b, c.removed_arc_ids)
    rs = repl_mod.compute(ms, c.removed_arc_ids, c.removed_arc_ids,
                          c.selected_segment_length_m)
    return c, b, ms, rs


# ==================================================================== tests
SEEDS = list(range(8))


class TestClosureRemoval:
    """What a closure takes out, derived twice."""

    @pytest.mark.parametrize("seed", SEEDS)
    def test_removed_arcs_match_exactly(self, synthetic, seed):
        net = synthetic(grid_spec(random.Random(seed)))
        oracle = Oracle(net.snapshot_id)
        for link_id in closable_links(net)[:6]:
            c = closure_mod.resolve(net.snapshot_id, link_id)
            assert set(c.removed_arc_ids) == oracle.removed_arcs(
                c.removed_link_ids)
            assert set(c.closure_nodes) == oracle.closure_nodes(
                c.removed_link_ids)


class TestPortPairValidity:
    """Which arcs cross the boundary, and which way they point."""

    @pytest.mark.parametrize("seed", SEEDS)
    def test_the_two_derivations_agree_on_every_port(self, synthetic, seed):
        net = synthetic(grid_spec(random.Random(seed)))
        oracle = Oracle(net.snapshot_id)
        for link_id in closable_links(net)[:6]:
            c, b, _, _ = run_engine(net, link_id)
            o_entry, o_exit = oracle.boundary_arcs(c.removed_link_ids)
            e_entry = {(p.arc_id, p.outside_node, p.closure_node)
                       for p in b.entry_ports}
            e_exit = {(p.arc_id, p.closure_node, p.outside_node)
                      for p in b.exit_ports}
            assert e_entry == o_entry, f"entry ports differ on link {link_id}"
            assert e_exit == o_exit, f"exit ports differ on link {link_id}"


class TestPathCost:
    """Every distance the engine reports, recomputed independently."""

    @pytest.mark.parametrize("seed", SEEDS)
    def test_intact_and_replacement_distances_match(self, synthetic, seed):
        net = synthetic(grid_spec(random.Random(seed)))
        oracle = Oracle(net.snapshot_id)
        checked = 0
        for link_id in closable_links(net)[:6]:
            c, _, ms, rs = run_engine(net, link_id)
            by_id = {m.movement_id: m for m in ms.movements}
            for p in rs.paths:
                m = by_id[p.movement_id]
                intact = oracle.distance(m.from_node, m.to_node)
                assert intact is not None
                assert m.intact_distance_m == pytest.approx(intact, abs=EPS)

                after = oracle.distance(m.from_node, m.to_node,
                                        c.removed_arc_ids)
                if after is None:
                    assert p.status == "DISCONNECTED"
                    assert p.replacement_distance_m is None
                else:
                    assert p.status == "OK"
                    assert p.replacement_distance_m == pytest.approx(
                        after, abs=EPS)
                    assert p.network_penalty_m == pytest.approx(
                        after - intact, abs=EPS)
                checked += 1
        assert checked, "the seed produced nothing to check"

    @pytest.mark.parametrize("seed", SEEDS)
    def test_no_replacement_path_uses_the_closure(self, synthetic, seed):
        net = synthetic(grid_spec(random.Random(seed)))
        oracle = Oracle(net.snapshot_id)
        for link_id in closable_links(net)[:6]:
            c, _, _, rs = run_engine(net, link_id)
            removed = oracle.removed_arcs(c.removed_link_ids)
            for p in rs.paths:
                assert not (set(p.arc_ids) & removed)
                assert p.traverses_own_closure is False


class TestMovementConnectivity:
    """Which crossings the closure is NECESSARY for."""

    @pytest.mark.parametrize("seed", SEEDS)
    def test_a_strictly_necessary_crossing_is_always_a_movement(
            self, synthetic, seed):
        """If removing the closure makes the crossing worse, it IS a movement.

        No tie can hide here: a strict increase means EVERY cheapest intact
        route used the closure, so the engine had no equal alternative to
        choose instead.
        """
        net = synthetic(grid_spec(random.Random(seed)))
        oracle = Oracle(net.snapshot_id)
        proven = 0
        for link_id in closable_links(net)[:6]:
            c, b, ms, _ = run_engine(net, link_id)
            included = {(m.entry_port_id, m.exit_port_id) for m in ms.included}
            for m in ms.movements:
                if m.reason_code == "U_TURN_AT_BOUNDARY":
                    continue
                before = oracle.distance(m.from_node, m.to_node)
                if before is None:
                    continue
                after = oracle.distance(m.from_node, m.to_node,
                                        c.removed_arc_ids)
                strictly_worse = after is None or after > before + EPS
                if strictly_worse and m.reason_code != "U_TURN_IN_ROUTE":
                    assert (m.entry_port_id, m.exit_port_id) in included, (
                        f"link {link_id}: the closure is necessary for "
                        f"{m.from_node}->{m.to_node} but it was excluded as "
                        f"{m.reason_code}")
                    proven += 1
        assert proven, "the seed proved nothing; the test would be vacuous"

    @pytest.mark.parametrize("seed", SEEDS)
    def test_an_unnecessary_crossing_is_never_a_movement(self, synthetic, seed):
        """And the converse, in its strict form.

        A pair the engine EXCLUDED for not traversing the closure must have an
        alternative that is no worse. If it were strictly worse without the
        closure, excluding it would have lost a real detour.
        """
        net = synthetic(grid_spec(random.Random(seed)))
        oracle = Oracle(net.snapshot_id)
        for link_id in closable_links(net)[:6]:
            c, _, ms, _ = run_engine(net, link_id)
            for m in ms.movements:
                if m.reason_code != "DOES_NOT_TRAVERSE_CLOSURE":
                    continue
                before = oracle.distance(m.from_node, m.to_node)
                after = oracle.distance(m.from_node, m.to_node,
                                        c.removed_arc_ids)
                assert after is not None, (
                    f"link {link_id}: {m.from_node}->{m.to_node} was excluded "
                    "as not using the closure, yet removing the closure "
                    "disconnects it")
                assert after <= before + EPS

    @pytest.mark.parametrize("seed", SEEDS)
    def test_an_unreachable_pair_really_is_unreachable(self, synthetic, seed):
        net = synthetic(grid_spec(random.Random(seed)))
        oracle = Oracle(net.snapshot_id)
        for link_id in closable_links(net)[:6]:
            _, _, ms, _ = run_engine(net, link_id)
            for m in ms.movements:
                if m.reason_code != "NO_INTACT_ROUTE":
                    continue
                assert oracle.distance(m.from_node, m.to_node) is None


class TestDeterminismUnderRowShuffling:
    """Every id is reassigned. Nothing the oracle or the engine says may move."""

    @staticmethod
    def _by_coordinate(net, oracle, link_key):
        """Answers keyed on POSITION, so two loads can be compared at all.

        Link and node ids differ between loads by construction; coordinates do
        not. Anything compared by id here would be comparing the numbering, not
        the answer.
        """
        row = db.query_one(
            "SELECT link_id FROM links WHERE snapshot_id=%s AND amds_id=%s",
            (net.snapshot_id, link_key))
        link_id = int(row["link_id"])
        c, b, ms, rs = run_engine(net, link_id)
        out = {
            "closure_len": round(c.total_closure_length_m, 6),
            "entry": sorted(oracle.coord[p.closure_node] + oracle.coord[p.outside_node]
                            for p in b.entry_ports),
            "exit": sorted(oracle.coord[p.closure_node] + oracle.coord[p.outside_node]
                           for p in b.exit_ports),
            "movements": sorted(
                (oracle.coord[m.from_node], oracle.coord[m.to_node],
                 m.reason_code, m.included)
                for m in ms.movements),
            "paths": sorted(
                (oracle.coord[p.from_node], oracle.coord[p.to_node], p.status,
                 None if p.replacement_distance_m is None
                 else round(p.replacement_distance_m, 6))
                for p in rs.paths),
        }
        return out

    @pytest.mark.parametrize("seed", SEEDS[:5])
    def test_engine_and_oracle_both_survive_reordering(self, synthetic, seed):
        spec = grid_spec(random.Random(seed))
        # Pick a link that exists in every load, by its AMDS id.
        link_key = spec[len(spec) // 2]["id"]

        baseline_engine = baseline_oracle = None
        for shuffle_seed in range(4):
            reordered = list(spec)
            random.Random(1000 + shuffle_seed).shuffle(reordered)
            net = synthetic(reordered)
            oracle = Oracle(net.snapshot_id)

            got = self._by_coordinate(net, oracle, link_key)

            row = db.query_one(
                "SELECT link_id, source_node, target_node FROM links "
                " WHERE snapshot_id=%s AND amds_id=%s",
                (net.snapshot_id, link_key))
            c = closure_mod.resolve(net.snapshot_id, int(row["link_id"]))
            o = {
                "removed_len": round(sum(
                    float(l["length_m"]) for l in oracle.links
                    if int(l["link_id"]) in set(c.removed_link_ids)), 6),
                "dist": _rounded(oracle.distance(int(row["source_node"]),
                                                 int(row["target_node"]))),
                "after": _rounded(oracle.distance(int(row["source_node"]),
                                                  int(row["target_node"]),
                                                  c.removed_arc_ids)),
            }

            if baseline_engine is None:
                baseline_engine, baseline_oracle = got, o
            assert got == baseline_engine, (
                f"seed {seed}, shuffle {shuffle_seed}: the engine's answer "
                "changed when the input order changed")
            assert o == baseline_oracle, (
                f"seed {seed}, shuffle {shuffle_seed}: the ORACLE's answer "
                "changed, so the fixture is not the same network")


class TestFullRequestAgainstOracle:
    """The whole orchestrated request, not just its parts."""

    @pytest.mark.parametrize("seed", SEEDS[:5])
    def test_the_principal_movements_numbers_are_independently_confirmed(
            self, synthetic, seed):
        net = synthetic(grid_spec(random.Random(seed)))
        oracle = Oracle(net.snapshot_id)
        confirmed = 0
        for link_id in closable_links(net)[:6]:
            r = impactv2.analyse(net.snapshot_id, link_id, with_isolation=False)
            p, m = r.principal, r.principal_movement
            if p is None or m is None:
                assert r.headline in ("No through movement identified",
                                      "Analysis unresolved")
                continue
            before = oracle.distance(m.from_node, m.to_node)
            after = oracle.distance(m.from_node, m.to_node, r.closure.removed_arc_ids)
            assert p.intact_distance_m == pytest.approx(before, abs=EPS)
            if after is None:
                assert p.status == "DISCONNECTED"
                assert r.headline == \
                    "Through movement has no represented replacement"
            else:
                assert p.replacement_distance_m == pytest.approx(after, abs=EPS)
                assert r.headline == "Through movement diverts"
            confirmed += 1
        assert confirmed, "the seed produced no principal movement to confirm"

    @pytest.mark.parametrize("seed", SEEDS[:5])
    def test_a_corridor_pair_always_has_the_route_it_claims(
            self, synthetic, seed):
        net = synthetic(grid_spec(random.Random(seed)))
        oracle = Oracle(net.snapshot_id)
        for link_id in closable_links(net)[:6]:
            r = impactv2.analyse(net.snapshot_id, link_id, with_isolation=False)
            if r.corridor is None:
                continue
            excl = r.closure.removed_arc_ids
            for pair in r.corridor.pairs:
                if pair.reason_code == "NOT_EVALUATED_TRUNCATED":
                    continue
                d = oracle.distance(pair.upstream_node, pair.downstream_node,
                                    excl)
                if pair.valid:
                    assert d is not None
                    assert pair.replacement_cost_m == pytest.approx(d, abs=EPS)
                elif pair.reason_code == "NO_REPRESENTED_REPLACEMENT":
                    assert d is None


def _rounded(v):
    return None if v is None else round(v, 6)


class TestTheOracleIsIndependent:
    """A guard on the guard.

    An oracle that quietly imported the engine would agree with it perfectly
    and prove nothing. This asserts the separation the file claims in its
    header, so the claim cannot rot.
    """

    #: Modules the oracle may not draw on. `nzcl.db` and `nzcl.closure` are
    #: absent from this list deliberately: reading rows is not an algorithm,
    #: and the closure scope is the QUESTION being asked rather than an answer
    #: to it - the oracle re-derives what that scope removes and asserts the
    #: two agree.
    FORBIDDEN = {"nzcl.routing", "nzcl.ports", "nzcl.movements",
                 "nzcl.replacement", "nzcl.corridor", "nzcl.stableid",
                 "nzcl.routegeom", "nzcl.physical"}

    def test_the_oracle_touches_none_of_the_engines_algorithms(self):
        """Scoped to the ORACLE, not to the harness around it.

        The test module must import the engine - it has to run it to compare
        against it. What must stay clean is the class that computes the second
        answer. The first version of this guard scanned the whole file, matched
        its own list of forbidden names, and failed against itself.

        Names are read from the parsed syntax tree rather than by substring, so
        a mention inside a string or a comment cannot trip it and a real
        reference cannot hide from it.
        """
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(Oracle).strip())
        referenced: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                referenced.add(node.id)
            elif isinstance(node, ast.Attribute):
                referenced.add(node.attr)

        banned_symbols = {m.split(".")[-1] for m in self.FORBIDDEN}
        offending = referenced & banned_symbols
        assert not offending, (
            f"the oracle reaches into {sorted(offending)}; agreement with code "
            "it shares is not evidence")

    def test_the_oracle_computes_with_networkx_and_not_pgrouting(self):
        import inspect

        source = inspect.getsource(Oracle)
        assert "nx." in source, "the oracle must do its own graph work"
        assert "pgr_" not in source.lower(), (
            "the oracle must not reach for the same router the engine uses")
