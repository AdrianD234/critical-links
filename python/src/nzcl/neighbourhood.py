"""A bounded copy of the network around one analysis, for counterfactuals.

WHY NOT `whatif.copy_snapshot`
-----------------------------
It copies whole tables. For a synthetic fixture that is instant and exactly
right. For the national snapshot it is 375,696 links, 7 tables and an ANALYZE,
per counterfactual - and a sensitivity analysis runs one per candidate. That
is not a slow request, it is a request that never returns, so the sensitivity
engine could not reach a user.

So a counterfactual copies only the neighbourhood the movement can reach, and
the bound is explicit and recorded.

THE HAZARD, AND THE GUARD
-------------------------
A bounded copy CUTS the network at its boundary. A replacement route that
would have left the neighbourhood and come back finds the edge missing, and
reports a longer route - or DISCONNECTED - that is an artefact of the copy
rather than a fact about the road network. A counterfactual that reports a
number produced that way is worse than one that declines, because it is wrong
in a direction nobody can see.

The guard is to make the copy prove itself before it is believed:

    1. extract the neighbourhood at radius R;
    2. run the analysis on the copy with NOTHING assumed;
    3. compare that against the canonical answer from the FULL snapshot;
    4. only if they agree is the copy trusted, and only then is any
       counterfactual run against it.

If they disagree the boundary is biting. The radius grows, up to a declared
number of attempts and a declared link ceiling, and if it still disagrees the
result is UNRESOLVED - stated, not hidden. `NeighbourhoodTooSmall` carries
what was tried, so "we could not test this" is a finding a reader can act on
rather than an absence.

This is the same discipline as the rest of this work: the mechanism has to be
able to say "I do not know", and saying it has to be cheaper than guessing.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from . import db, whatif

#: Radii tried in order, in metres. The first is generous for an urban
#: closure; the last is far larger than any replacement path this tool has
#: produced. Growing rather than starting large keeps the common case fast.
DEFAULT_RADII_M = (5_000.0, 12_000.0, 30_000.0)

#: Hard ceiling on links in a neighbourhood copy. Past this the copy costs
#: more than it saves and the honest answer is that this analysis is not
#: cheaply testable. Roughly 8% of the national network.
MAX_LINKS = 30_000

#: Tables copied for the subset, in dependency order. `nodes` comes first
#: because links reference them.
_SUBSET_TABLES = ("nodes", "links", "arcs", "turn_restrictions", "link_names")


class NeighbourhoodTooSmall(Exception):
    """No admissible neighbourhood reproduced the canonical answer.

    Raised instead of returning a counterfactual computed against a copy whose
    boundary is affecting the result. The attributes say what was tried, so
    the caller can report UNRESOLVED with a reason rather than a shrug.
    """

    def __init__(self, radii_tried, link_counts, detail: str) -> None:
        self.radii_tried = tuple(radii_tried)
        self.link_counts = tuple(link_counts)
        self.detail = detail
        super().__init__(
            f"no bounded neighbourhood reproduced the canonical answer "
            f"(radii {list(self.radii_tried)} m, "
            f"{list(self.link_counts)} links): {detail}. "
            f"Reporting UNRESOLVED rather than a number the boundary shaped.")


@dataclass
class Extraction:
    """One bounded copy, and what it cost."""

    snapshot_id: str
    source_snapshot_id: str
    radius_m: float
    link_count: int
    node_count: int
    seconds: float
    #: True once the copy has reproduced the canonical answer with nothing
    #: assumed. Nothing may be believed from it before that.
    validated: bool = False
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "radiusM": self.radius_m,
            "linkCount": self.link_count,
            "nodeCount": self.node_count,
            "extractionSeconds": round(self.seconds, 3),
            "validatedAgainstCanonical": self.validated,
            "maxLinks": MAX_LINKS,
            "why": ("A counterfactual is routed on a bounded copy of the "
                    "network around the closure, not on the national tables. "
                    "The copy is only believed once it reproduces the "
                    "canonical answer with nothing assumed."),
        }


def count_links_within(snapshot_id: str, link_id: int,
                       radius_m: float) -> int:
    """How many links a neighbourhood at this radius would hold.

    Cheap enough to ask before committing to a copy, which is the point: the
    ceiling should be hit by a count, not by a copy that has already run.
    """
    rows = db.query(
        "SELECT count(*) AS n FROM links l "
        " WHERE l.snapshot_id = %s "
        "   AND ST_DWithin(l.geom_2193, "
        "                  (SELECT geom_2193 FROM links "
        "                    WHERE snapshot_id = %s AND link_id = %s), %s)",
        (snapshot_id, snapshot_id, link_id, radius_m))
    return int(rows[0]["n"])


def extract(src: str, link_id: int, *, radius_m: float,
            dst: str | None = None) -> Extraction:
    """Copy the network within `radius_m` of one link into a new snapshot.

    Links are selected by distance from the closed link's geometry, and nodes
    by membership of the selected links - both endpoints, so no arc is left
    dangling. `arcs`, `turn_restrictions` and `link_names` follow the links
    they belong to.

    The source snapshot is only ever read.
    """
    t0 = time.perf_counter()
    dst = dst or f"cf-{uuid.uuid4().hex[:12]}"
    n = count_links_within(src, link_id, radius_m)
    if n > MAX_LINKS:
        raise NeighbourhoodTooSmall(
            (radius_m,), (n,),
            f"a {radius_m:.0f} m neighbourhood holds {n} links, over the "
            f"{MAX_LINKS} ceiling")

    with db.direct_connection(autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM network_snapshots WHERE snapshot_id=%s",
                        (src,))
            if cur.fetchone() is None:
                raise KeyError(f"no such snapshot: {src}")
            whatif._drop(cur, dst)

            cur.execute("SELECT * FROM network_snapshots WHERE snapshot_id=%s",
                        (src,))
            row = dict(cur.fetchone())
            cols = list(row)
            row["snapshot_id"] = dst
            row["notes"] = list(row.get("notes") or []) + [
                f"BOUNDED COUNTERFACTUAL COPY of {src}: links within "
                f"{radius_m:.0f} m of link {link_id}. NOT a published "
                f"snapshot, and not a complete network - it is valid only "
                f"for the movement it was extracted for, and only once it "
                f"has reproduced that movement's canonical answer."]
            cur.execute(
                f"INSERT INTO network_snapshots ({', '.join(cols)}) "
                f"VALUES ({', '.join(['%s'] * len(cols))})",
                [row[c] for c in cols])

            # The selected links, materialised once so every later copy joins
            # against the same set rather than re-running the distance test.
            cur.execute(
                "CREATE TEMP TABLE _cf_links ON COMMIT DROP AS "
                " SELECT link_id, source_node, target_node FROM links "
                "  WHERE snapshot_id = %s "
                "    AND ST_DWithin(geom_2193, (SELECT geom_2193 FROM links "
                "        WHERE snapshot_id = %s AND link_id = %s), %s)",
                (src, src, link_id, radius_m))
            cur.execute("CREATE INDEX ON _cf_links (link_id)")
            cur.execute("SELECT count(*) AS n FROM _cf_links")
            link_count = int(cur.fetchone()["n"])

            cur.execute(
                "CREATE TEMP TABLE _cf_nodes ON COMMIT DROP AS "
                " SELECT DISTINCT source_node AS node_id FROM _cf_links "
                "  UNION SELECT DISTINCT target_node FROM _cf_links")
            cur.execute("CREATE INDEX ON _cf_nodes (node_id)")
            cur.execute("SELECT count(*) AS n FROM _cf_nodes")
            node_count = int(cur.fetchone()["n"])

            for table in _SUBSET_TABLES:
                cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    " WHERE table_schema='public' AND table_name=%s "
                    " ORDER BY ordinal_position", (table,))
                names = [r["column_name"] for r in cur.fetchall()]
                if not names:
                    continue
                sel = ", ".join("%s AS snapshot_id" if c == "snapshot_id"
                                else f"t.{c}" for c in names)
                if table == "nodes":
                    where = ("JOIN _cf_nodes n ON n.node_id = t.node_id "
                             "WHERE t.snapshot_id = %s")
                elif table in ("links", "arcs"):
                    where = ("JOIN _cf_links c ON c.link_id = t.link_id "
                             "WHERE t.snapshot_id = %s")
                elif table == "turn_restrictions":
                    # Keyed by an ARRAY of links, not one. A restriction is
                    # copied only when EVERY link it names is inside the
                    # neighbourhood: one that reaches outside cannot be
                    # violated within the copy, and importing it half-formed
                    # would forbid a turn on evidence the copy does not hold.
                    where = ("WHERE t.snapshot_id = %s AND t.link_seq <@ "
                             "ARRAY(SELECT link_id FROM _cf_links)")
                else:  # link_names is keyed by closure_group_id
                    where = ("WHERE t.snapshot_id = %s AND t.closure_group_id "
                             "IN (SELECT l.closure_group_id FROM links l "
                             "JOIN _cf_links c ON c.link_id = l.link_id "
                             "WHERE l.snapshot_id = %s)")
                params = ([dst, src, src] if table == "link_names"
                          else [dst, src])
                cur.execute(
                    f"INSERT INTO {table} ({', '.join(names)}) "
                    f"SELECT {sel} FROM {table} t {where}", params)
        conn.commit()

    return Extraction(snapshot_id=dst, source_snapshot_id=src,
                      radius_m=radius_m, link_count=link_count,
                      node_count=node_count,
                      seconds=time.perf_counter() - t0)


def extract_validated(src: str, link_id: int, *, canonical, answer_of,
                      radii=DEFAULT_RADII_M) -> Extraction:
    """A neighbourhood that has PROVED it reproduces the canonical answer.

    `answer_of(snapshot_id)` runs the movement under test on a snapshot and
    returns something comparable to `canonical`. Nothing is assumed while this
    runs: the whole question is whether the boundary changed the answer on its
    own.

    Grows the radius rather than starting at the largest, because the common
    case is a short closure whose replacement stays local, and paying 30 km of
    copy for every one of those would defeat the purpose.

    Raises `NeighbourhoodTooSmall` if no admissible radius agrees. The caller
    must then report UNRESOLVED. It must NOT fall back to the largest copy it
    managed to build - that is the silent truncation this exists to prevent.
    """
    tried, counts, last = [], [], ""
    for radius in radii:
        tried.append(radius)
        try:
            ex = extract(src, link_id, radius_m=radius)
        except NeighbourhoodTooSmall as e:
            counts.append(e.link_counts[0] if e.link_counts else -1)
            last = e.detail
            break          # a larger radius can only hold more links
        counts.append(ex.link_count)
        try:
            got = answer_of(ex.snapshot_id)
            if got == canonical or (
                    hasattr(got, "differs_from")
                    and not got.differs_from(canonical)):
                ex.validated = True
                ex.notes.append(
                    f"reproduced the canonical answer at {radius:.0f} m")
                return ex
            last = f"at {radius:.0f} m the copy answered {got!r}, not {canonical!r}"
            ex.notes.append(last)
        finally:
            if not ex.validated:
                whatif.drop_snapshot(ex.snapshot_id)
    raise NeighbourhoodTooSmall(tried, counts, last or "no radius was admissible")
