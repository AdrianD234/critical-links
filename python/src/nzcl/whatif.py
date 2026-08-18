"""Isolated graph copies, for asking "what if this crossing were noded?".

Why this exists
---------------
The question "would the replacement path be different if these two roads
actually met" cannot be answered by opening a transaction and rolling it back.
`db.get_pool()` hands out AUTOCOMMIT connections, so an uncommitted change on
one connection is invisible to every query the engine makes on another. The
engine gives no hook for injecting a connection.

So the counterfactual runs on a COPY of the snapshot under a different
`snapshot_id`. The original is never written to. The copy is dropped afterwards
by deleting its `network_snapshots` row, which cascades to every dependent
table.

What `node_crossings` does, precisely
-------------------------------------
For each supplied crossing it cuts BOTH links at the intersection point and
gives the four resulting ends ONE shared node id. That is the same outcome
`topology.split_at_junctions` produces for a T-junction, reached by a different
route: this operates on an already-split graph in the database, whereas the
ingest operates on source geometry in memory.

The two must agree, and `tests/test_whatif.py` is what holds them to it.

This module is a MEASUREMENT instrument, not part of the ingest. Nothing in the
request path imports it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import datetime as _dt

from . import db

#: Tables copied verbatim, keyed by snapshot_id. `arc_transitions` is REBUILT
#: rather than copied, because arc ids change; `physical_access_*`,
#: `closure_analysis_v2`, `closure_isolation_v2`, `detour_results` and
#: `closure_shadow_comparisons` are derived results and must not be inherited by
#: a graph that differs from the one that produced them.
_COPIED_TABLES = ("nodes", "links", "arcs", "turn_restrictions", "link_names",
                  "near_misses", "qa_issues")


@dataclass
class CrossingEdit:
    """One crossing to node: two links, and the point they cross at."""
    link_a: int
    link_b: int
    #: EPSG:2193.
    x: float
    y: float

    @property
    def key(self) -> tuple[int, int]:
        return (min(self.link_a, self.link_b), max(self.link_a, self.link_b))


@dataclass
class NodingReport:
    snapshot_id: str
    crossings_requested: int
    crossings_applied: int
    crossings_skipped: list[tuple[tuple[int, int], str]] = field(default_factory=list)
    links_split: int = 0
    new_links: int = 0
    new_nodes: int = 0
    arcs_rebuilt: int = 0
    #: crossing key -> the node id the four ends now share.
    node_of_crossing: dict[tuple[int, int], int] = field(default_factory=dict)
    #: original link id -> the link ids it became, in order along the line.
    pieces_of_link: dict[int, list[int]] = field(default_factory=dict)


def copy_snapshot(src: str, dst: str, *, coverage_note: str | None = None) -> None:
    """Duplicate a snapshot under a new id. The source is only read."""
    with db.direct_connection(autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM network_snapshots WHERE snapshot_id=%s",
                        (src,))
            if cur.fetchone() is None:
                raise KeyError(f"no such snapshot: {src}")
            _drop(cur, dst)

            cur.execute("SELECT * FROM network_snapshots WHERE snapshot_id=%s",
                        (src,))
            row = cur.fetchone()
            cols = [c for c in row]
            row = dict(row)
            row["snapshot_id"] = dst
            # EVERY whatif copy is a working copy, so every one is transient.
            # Marked here rather than left to callers: a copy that forgets can
            # win automatic snapshot selection on recency and serve a fragment
            # as the national network.
            row["is_transient"] = True
            row["transient_created_at"] = _dt.datetime.now(_dt.timezone.utc)
            if "coverage_kind" in row:
                row["coverage_kind"] = "counterfactual"
            notes = list(row.get("notes") or [])
            notes.append(
                coverage_note
                or f"ISOLATED COPY of {src} for counterfactual analysis. "
                   f"Not a published snapshot.")
            row["notes"] = notes
            collist = ", ".join(cols)
            placeholders = ", ".join(["%s"] * len(cols))
            cur.execute(
                f"INSERT INTO network_snapshots ({collist}) VALUES ({placeholders})",
                [row[c] for c in cols],
            )

            for table in _COPIED_TABLES:
                cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    " WHERE table_schema='public' AND table_name=%s "
                    " ORDER BY ordinal_position", (table,))
                names = [r["column_name"] for r in cur.fetchall()]
                if not names:
                    continue
                sel = ", ".join(
                    "%s AS snapshot_id" if n == "snapshot_id" else n
                    for n in names)
                cur.execute(
                    f"INSERT INTO {table} ({', '.join(names)}) "
                    f"SELECT {sel} FROM {table} WHERE snapshot_id = %s",
                    (dst, src),
                )
        conn.commit()

    with db.direct_connection() as conn:
        with conn.cursor() as cur:
            for table in _COPIED_TABLES:
                cur.execute(f"ANALYZE {table}")


def drop_snapshot(snapshot_id: str) -> None:
    """Delete a copy. Every dependent table cascades from the snapshot row."""
    with db.direct_connection(autocommit=False) as conn:
        with conn.cursor() as cur:
            _drop(cur, snapshot_id)
        conn.commit()


def _drop(cur, snapshot_id: str) -> None:
    cur.execute("DELETE FROM arc_transitions WHERE snapshot_id=%s", (snapshot_id,))
    cur.execute("DELETE FROM network_snapshots WHERE snapshot_id=%s", (snapshot_id,))


def node_crossings(snapshot_id: str, crossings: list[CrossingEdit], *,
                   rebuild_transitions: bool = True) -> NodingReport:
    """Cut both links of every crossing and join the four ends at one node.

    Everything happens in ONE transaction against `snapshot_id`. Call it on a
    copy, never on a published snapshot.
    """
    report = NodingReport(snapshot_id=snapshot_id,
                          crossings_requested=len(crossings),
                          crossings_applied=0)

    # Deduplicate: the same two links can be handed in twice, and the same link
    # can take part in several crossings.
    by_key: dict[tuple[int, int], CrossingEdit] = {}
    for c in crossings:
        by_key.setdefault(c.key, c)

    with db.direct_connection(autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT coalesce(max(node_id), -1) AS n FROM nodes WHERE snapshot_id=%s",
                (snapshot_id,))
            next_node = int(cur.fetchone()["n"]) + 1
            cur.execute(
                "SELECT coalesce(max(link_id), -1) AS n FROM links WHERE snapshot_id=%s",
                (snapshot_id,))
            next_link = int(cur.fetchone()["n"]) + 1
            cur.execute(
                "SELECT coalesce(max(arc_id), -1) AS n FROM arcs WHERE snapshot_id=%s",
                (snapshot_id,))
            next_arc = int(cur.fetchone()["n"]) + 1

            # --- 1. validate every crossing, and collect cut positions ------
            # link_id -> list of (fraction_along, node_id)
            cuts: dict[int, list[tuple[float, int]]] = {}
            for key, c in sorted(by_key.items()):
                ok, detail, frac_a, frac_b = _locate(cur, snapshot_id, c)
                if not ok:
                    report.crossings_skipped.append((key, detail))
                    continue
                node_id = next_node
                next_node += 1
                cur.execute(
                    "INSERT INTO nodes (snapshot_id, node_id, geom_2193, "
                    "geom_4326, component_id, quality_flags) VALUES "
                    "(%s,%s, ST_SetSRID(ST_MakePoint(%s,%s),2193), "
                    " ST_Transform(ST_SetSRID(ST_MakePoint(%s,%s),2193),4326), "
                    " -1, ARRAY['AT_GRADE_CROSSING_NODE'])",
                    (snapshot_id, node_id, c.x, c.y, c.x, c.y))
                report.new_nodes += 1
                cuts.setdefault(c.link_a, []).append((frac_a, node_id))
                cuts.setdefault(c.link_b, []).append((frac_b, node_id))
                report.node_of_crossing[key] = node_id
                report.crossings_applied += 1

            # --- 2. cut each affected link once, at all of its points -------
            for link_id in sorted(cuts):
                pieces = _split_link(cur, snapshot_id, link_id,
                                     sorted(cuts[link_id]), next_link)
                next_link += len(pieces) - 1
                report.links_split += 1
                report.new_links += len(pieces) - 1
                report.pieces_of_link[link_id] = pieces

            # --- 3. rebuild the arcs of every affected link -----------------
            affected = sorted({lid for lid in cuts} |
                              {p for ps in report.pieces_of_link.values() for p in ps})
            cur.execute("DELETE FROM arcs WHERE snapshot_id=%s AND link_id = ANY(%s)",
                        (snapshot_id, affected))
            report.arcs_rebuilt = _rebuild_arcs(cur, snapshot_id, affected, next_arc)

            cur.execute("ANALYZE links")
            cur.execute("ANALYZE nodes")
            cur.execute("ANALYZE arcs")

            if rebuild_transitions:
                cur.execute("DELETE FROM arc_transitions WHERE snapshot_id=%s",
                            (snapshot_id,))
                cur.execute("SELECT set_config('statement_timeout','1200000',true)")
                cur.execute("SELECT build_arc_transitions(%s) AS n", (snapshot_id,))
                cur.execute("SELECT set_config('statement_timeout','0',true)")

            cur.execute(
                "UPDATE network_snapshots SET "
                "  routable_link_count = (SELECT count(*) FROM links WHERE snapshot_id=%s), "
                "  arc_count = (SELECT count(*) FROM arcs WHERE snapshot_id=%s), "
                "  node_count = (SELECT count(*) FROM nodes WHERE snapshot_id=%s) "
                "WHERE snapshot_id=%s",
                (snapshot_id, snapshot_id, snapshot_id, snapshot_id))
        conn.commit()

    _recompute_components(snapshot_id)
    return report


#: A cut this close to either end of a line would produce a zero-length piece,
#: and `links_length_m_check` forbids one. It is also the signature of a
#: crossing that is really an endpoint junction, which `split_at_junctions`
#: already handles.
_END_GUARD_M = 0.05


def _locate(cur, snapshot_id: str, c: CrossingEdit,
            ) -> tuple[bool, str, float, float]:
    cur.execute(
        "SELECT link_id, ST_Length(geom_2193) AS len, "
        "       ST_LineLocatePoint(geom_2193, ST_SetSRID(ST_MakePoint(%s,%s),2193)) AS frac, "
        "       ST_Distance(geom_2193, ST_SetSRID(ST_MakePoint(%s,%s),2193)) AS dist, "
        "       source_node, target_node "
        "  FROM links WHERE snapshot_id=%s AND link_id = ANY(%s)",
        (c.x, c.y, c.x, c.y, snapshot_id, [c.link_a, c.link_b]))
    rows = {r["link_id"]: r for r in cur.fetchall()}
    if len(rows) != 2:
        return False, "one or both links no longer exist (already split?)", 0.0, 0.0

    a, b = rows[c.link_a], rows[c.link_b]
    if {a["source_node"], a["target_node"]} & {b["source_node"], b["target_node"]}:
        return False, "links already share a node", 0.0, 0.0

    for label, r in (("a", a), ("b", b)):
        if r["dist"] > 0.001:
            return False, (f"point lies {r['dist']:.3f} m off link "
                           f"{label}; not a zero-gap crossing"), 0.0, 0.0
        along = r["frac"] * r["len"]
        if along < _END_GUARD_M or along > r["len"] - _END_GUARD_M:
            return False, (f"crossing sits at an END of link {label}, not its "
                           f"interior"), 0.0, 0.0
    return True, "", float(a["frac"]), float(b["frac"])


def _split_link(cur, snapshot_id: str, link_id: int,
                cuts: list[tuple[float, int]], next_link: int) -> list[int]:
    """Replace `link_id` with len(cuts)+1 pieces. Returns their ids, in order.

    The first piece keeps the original link id, so anything that referred to
    the start of this line still refers to the start of this line.

    The parent geometry is read ONCE, up front, and every piece is cut from
    that captured copy. Reading it back from the table per piece would work for
    the first cut and silently produce zero-length geometry for the rest,
    because the first piece overwrites the row the later ones are cutting.
    """
    cur.execute(
        "SELECT *, ST_AsEWKB(geom_2193) AS parent_geom "
        "  FROM links WHERE snapshot_id=%s AND link_id=%s",
        (snapshot_id, link_id))
    parent = dict(cur.fetchone())
    parent_geom = parent.pop("parent_geom")

    bounds = [0.0] + [f for f, _ in cuts] + [1.0]
    # source node of piece i, target node of piece i
    ends = [parent["source_node"]] + [n for _, n in cuts] + [parent["target_node"]]

    ids: list[int] = []
    for i in range(len(bounds) - 1):
        lid = link_id if i == 0 else next_link + i - 1
        ids.append(lid)
        amds = parent["amds_id"] if i == 0 else f"{parent['amds_id']}~x{i}"
        flags = list(parent["quality_flags"] or [])
        if "SPLIT_AT_GRADE_CROSSING" not in flags:
            flags.append("SPLIT_AT_GRADE_CROSSING")

        # ST_LineSubstring interpolates its endpoints; force the cut ends onto
        # the exact crossing coordinate so the geometry meets where the node is.
        geom = "ST_LineSubstring(%s::geometry, %s, %s)"
        params: list = [parent_geom, bounds[i], bounds[i + 1]]
        if i > 0:
            geom = (f"ST_SetPoint({geom}, 0, "
                    f"(SELECT geom_2193 FROM nodes WHERE snapshot_id=%s AND node_id=%s))")
            params += [snapshot_id, ends[i]]
        if i < len(bounds) - 2:
            geom = (f"ST_SetPoint({geom}, -1, "
                    f"(SELECT geom_2193 FROM nodes WHERE snapshot_id=%s AND node_id=%s))")
            params += [snapshot_id, ends[i + 1]]

        if i == 0:
            cur.execute(
                f"UPDATE links SET geom_2193 = {geom}, target_node = %s, "
                f"  quality_flags = %s "
                f" WHERE snapshot_id=%s AND link_id=%s",
                (*params, ends[1], flags, snapshot_id, link_id))
            cur.execute(
                "UPDATE links SET geom_4326 = ST_Transform(geom_2193, 4326), "
                "  length_m = ST_Length(geom_2193) "
                " WHERE snapshot_id=%s AND link_id=%s",
                (snapshot_id, link_id))
        else:
            cols = [k for k in parent if k not in
                    ("snapshot_id", "link_id", "geom_2193", "geom_4326",
                     "length_m", "source_node", "target_node", "amds_id",
                     "quality_flags")]
            collist = ", ".join(cols)
            ph = ", ".join(["%s"] * len(cols))
            cur.execute(
                f"INSERT INTO links (snapshot_id, link_id, amds_id, "
                f"  quality_flags, source_node, target_node, geom_2193, "
                f"  geom_4326, length_m, {collist}) "
                f"SELECT %s, %s, %s, %s, %s, %s, g, ST_Transform(g, 4326), "
                f"       ST_Length(g), {ph} "
                f"  FROM (SELECT {geom} AS g) s",
                (snapshot_id, lid, amds, flags, ends[i], ends[i + 1],
                 *[parent[c] for c in cols], *params))
    return ids


def _rebuild_arcs(cur, snapshot_id: str, link_ids: list[int], next_arc: int) -> int:
    cur.execute(
        "SELECT link_id, closure_group_id, source_node, target_node, length_m, "
        "       forward_allowed, reverse_allowed, speed_kph, mode_vehicle, "
        "       mode_vehicle_heavy, mode_emergency "
        "  FROM links WHERE snapshot_id=%s AND link_id = ANY(%s) ORDER BY link_id",
        (snapshot_id, link_ids))
    made = 0
    for r in cur.fetchall():
        if r["source_node"] == r["target_node"]:
            continue
        speed = r["speed_kph"] or 0.0
        time_s = r["length_m"] / (speed * 1000 / 3600) if speed > 0 else None
        for direction, u, v, allowed in (
            ("forward", r["source_node"], r["target_node"], r["forward_allowed"]),
            ("reverse", r["target_node"], r["source_node"], r["reverse_allowed"]),
        ):
            if not allowed:
                continue
            cur.execute(
                "INSERT INTO arcs (snapshot_id, arc_id, link_id, "
                "  closure_group_id, source, target, direction, cost_distance_m, "
                "  cost_time_s, time_cost_valid, mode_vehicle, mode_vehicle_heavy, "
                "  mode_emergency) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (snapshot_id, next_arc, r["link_id"], r["closure_group_id"],
                 u, v, direction, r["length_m"], time_s, time_s is not None,
                 r["mode_vehicle"], r["mode_vehicle_heavy"], r["mode_emergency"]))
            next_arc += 1
            made += 1
    return made


def _recompute_components(snapshot_id: str) -> None:
    """Relabel `nodes.component_id`. Noding can only ever MERGE components."""
    rows = db.query(
        "SELECT source_node, target_node FROM links WHERE snapshot_id=%s",
        (snapshot_id,))
    node_rows = db.query(
        "SELECT node_id FROM nodes WHERE snapshot_id=%s ORDER BY node_id",
        (snapshot_id,))
    ids = [r["node_id"] for r in node_rows]
    parent = {n: n for n in ids}

    def find(v: int) -> int:
        while parent[v] != v:
            parent[v] = parent[parent[v]]
            v = parent[v]
        return v

    for r in rows:
        a, b = find(r["source_node"]), find(r["target_node"])
        if a != b:
            parent[a] = b

    labels: dict[int, int] = {}
    assignment: list[tuple[int, int]] = []
    for n in ids:
        root = find(n)
        if root not in labels:
            labels[root] = len(labels)
        assignment.append((labels[root], n))

    with db.direct_connection(autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TEMP TABLE _comp (component_id int, node_id bigint) "
                        "ON COMMIT DROP")
            with cur.copy("COPY _comp (component_id, node_id) FROM STDIN") as cp:
                for cid, nid in assignment:
                    cp.write_row((cid, nid))
            cur.execute(
                "UPDATE nodes n SET component_id = c.component_id "
                "  FROM _comp c WHERE n.snapshot_id=%s AND n.node_id = c.node_id",
                (snapshot_id,))
        conn.commit()
