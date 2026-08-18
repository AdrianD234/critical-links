"""Which unresolved crossings could change THIS answer.

WHY A BUFFER ROUND THE CLICKED LINK IS THE WRONG SEARCH
-------------------------------------------------------
The crossing that actually causes the Greendale result is Clintons Road x
McLaughlins Road, roughly **2.8 km** from the closed link. The crossing a
person looking at the map would point at - Clintons x Greendale, right beside
the closure - is a genuine missing junction and connecting it alone leaves the
route completely unchanged at 7,944.4 m.

So a tight buffer around the closure finds the wrong crossing and misses the
right one, and a buffer wide enough to reach 2.8 km in every direction pulls in
hundreds of irrelevant crossings. Distance from the closure is simply not what
makes a crossing matter.

WHAT DOES MAKE ONE MATTER
-------------------------
A crossing can change a replacement route only if connecting it offers a way
through that the canonical answer had to go round. That gives four places to
look, and they are derived from the analysis rather than guessed:

  1. THE CLOSURE AND ITS PORTS. Where the movement enters and leaves. A
     crossing here can reconnect a severed end directly.
  2. THE CANONICAL DETOUR CORRIDOR. The route the answer actually took. This
     is the one that finds Clintons x McLaughlins: the 7,944 m perimeter runs
     Greendale -> Wards -> Clintons -> Bangor -> McLaughlins -> Greendale, so
     both roads of the causal crossing are ON the canonical route, 2.8 km from
     the closure and directly on the path being measured.
  3. INSIDE THE CIRCUIT. A long detour encloses an area. A crossing inside
     that area is a potential chord across it - exactly the shortcut a
     perimeter route exists because the graph did not have.
  4. NEAR THE SEVERED SIDE. When the canonical answer is DISCONNECTED or
     isolating, the crossings that could reconnect the cut-off part.

Only crossings the canonical graph did NOT node are candidates, and only ones
that could be represented as a node if they were: a mixed place is refused
under every policy, so proposing it is proposing something unrepresentable.

BOUNDED, AND IT SAYS SO WHEN IT TRUNCATES
-----------------------------------------
A search that quietly returns the first N is a search that can miss the causal
crossing and report a confident "not topology-sensitive". When the bound bites
the result carries `truncated=True` and the counts, and the caller must not
present "no sensitivity found" as "this answer is robust".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from . import db
from .sensitivity import Candidate

#: Around the closed link itself, and around the movement's ports.
CLOSURE_RADIUS_M = 600.0
PORT_RADIUS_M = 600.0

#: Around the canonical replacement route. Tighter than the closure radius
#: because the route is a long line and the question is "on this corridor",
#: not "somewhere near it".
CORRIDOR_RADIUS_M = 250.0

#: Hard bound on candidates per analysis. Each one costs a counterfactual
#: route, and `sensitivity.MAX_SINGLE` bounds how many are actually run - this
#: bounds how many are even considered, so the query cannot return thousands.
MAX_CANDIDATES = 60

#: Sources, in the order they are searched. Earlier sources win a tie, because
#: a crossing on the canonical corridor is a better bet than one that merely
#: falls inside the circuit.
SOURCES = ("closure", "corridor", "ports", "inside_circuit")

#: Where the candidates came from. Declared on every response, because "zero
#: candidates" from an empty catalogue and "zero candidates" from a search that
#: ran and found none are opposite facts wearing the same number.
PRECOMPUTED_CATALOGUE = "PRECOMPUTED_CATALOGUE"
ON_DEMAND_NEIGHBOURHOOD = "ON_DEMAND_NEIGHBOURHOOD"
UNAVAILABLE = "UNAVAILABLE"

#: How far around the closure and the canonical corridor on-demand detection
#: reads source features. Bounded: this is not a national pass.
ON_DEMAND_BUFFER_M = 1200.0

#: What a candidate outcome may be. Exactly these, and no sixth.
TESTED_CHANGED = "TESTED_CHANGED"
TESTED_UNCHANGED = "TESTED_UNCHANGED"
EXCLUDED_BY_RELEVANCE_RULE = "EXCLUDED_BY_RELEVANCE_RULE"
UNTESTED_MATERIALISATION_FAILED = "UNTESTED_MATERIALISATION_FAILED"
UNTESTED_CANCELLED = "UNTESTED_CANCELLED"
OUTCOMES = (TESTED_CHANGED, TESTED_UNCHANGED, EXCLUDED_BY_RELEVANCE_RULE,
            UNTESTED_MATERIALISATION_FAILED, UNTESTED_CANCELLED)


@dataclass
class CandidateSearch:
    """What was found, from where, and whether the search was complete."""

    candidates: list[Candidate] = field(default_factory=list)
    by_source: dict[str, int] = field(default_factory=dict)
    considered: int = 0
    truncated: bool = False
    sources_used: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    source_kind: str = UNAVAILABLE
    source_complete: bool = False
    catalogue_rows: int = 0

    def as_dict(self) -> dict:
        return {
            "candidateSource": self.source_kind,
            "candidateSourceComplete": self.source_complete,
            "catalogueRows": self.catalogue_rows,
            "candidates": len(self.candidates),
            "consideredBeforeBound": self.considered,
            "bound": MAX_CANDIDATES,
            "truncated": self.truncated,
            "bySource": dict(self.by_source),
            "sourcesUsed": list(self.sources_used),
            "radii": {"closureM": CLOSURE_RADIUS_M,
                      "corridorM": CORRIDOR_RADIUS_M,
                      "portM": PORT_RADIUS_M},
            "notes": list(self.notes),
            "why": ("Candidates are derived from the closure, its ports, the "
                    "canonical detour corridor and the interior of the "
                    "circuit that detour encloses - not from a buffer around "
                    "the clicked link. The crossing that causes the Greendale "
                    "result is 2.8 km away and ON the canonical route, while "
                    "the one beside the closure changes nothing."),
            "ifTruncated": ("When truncated is true the search was bounded "
                            "before it finished, so 'no sensitivity found' "
                            "does NOT mean the answer is robust."),
            "aboutTheSource": (
                "PRECOMPUTED_CATALOGUE: the snapshot carries a crossings "
                "catalogue and it was queried. ON_DEMAND_NEIGHBOURHOOD: it "
                "does not, so crossings were detected around the closure "
                "and the canonical corridor at request time - bounded, not "
                "national. UNAVAILABLE: neither was possible, and zero "
                "candidates here is NOT a finding that the answer is "
                "robust."),
        }


def _label(name, withheld_source):
    """What to call a road, honestly.

    Three states, not two. A road can be named, genuinely unnamed, or NAMED
    BUT WITHHELD - the naming layer holds a HIGH-confidence external match
    whose licence has not been cleared for display. Calling the third case
    \"unnamed road\" is wrong: it tells a user the road has no name when the
    system knows one and is not allowed to show it.
    """
    if name:
        return name
    if withheld_source:
        return f"(name withheld: {withheld_source} not cleared for display)"
    return None


def _rows_to_candidates(rows) -> list[Candidate]:
    out = []
    for r in rows:
        out.append(Candidate(
            crossing_id=int(r["crossing_id"]),
            source_a=r["source_a"], source_b=r["source_b"],
            x=float(r["x"]), y=float(r["y"]),
            classifier_disposition=r.get("disposition"),
            classifier_reason=r.get("reason"),
            classifier_confidence=r.get("confidence"),
            name_a=_label(r.get("name_a"), r.get("withheld_a")),
            name_b=_label(r.get("name_b"), r.get("withheld_b"))))
    return out


_SELECT = """
    SELECT c.crossing_id, c.source_a, c.source_b,
           ST_X(c.geom_2193) AS x, ST_Y(c.geom_2193) AS y,
           c.disposition, c.reason, c.confidence,
           na.display_name AS name_a, nb.display_name AS name_b,
           na.withheld_name_source AS withheld_a,
           nb.withheld_name_source AS withheld_b
      FROM crossings c
      -- THE GOVERNED NAMING LAYER, not link_names directly. The view applies
      -- the licence gate: an externally matched name is withheld from display
      -- until its source is cleared, and `withheld_name_source` says so. The
      -- Darfield crossing is exactly this case - the naming layer holds
      -- \"Clintons Road\" and \"McLaughlins Road\" from linz_road_sections at HIGH
      -- confidence, and the view withholds both pending clearance. Reading
      -- link_names directly would bypass a deliberate governance decision to
      -- make a sentence read better.
      LEFT JOIN link_display_names na ON na.snapshot_id = c.snapshot_id
                             AND na.closure_group_id = c.source_a
      LEFT JOIN link_display_names nb ON nb.snapshot_id = c.snapshot_id
                             AND nb.closure_group_id = c.source_b
     WHERE c.snapshot_id = %(snap)s
       -- Only crossings the canonical graph did NOT node: a crossing that is
       -- already a junction cannot be assumed into one.
       AND NOT c.noded
       -- And only ones that COULD be a node. A mixed place is refused under
       -- every policy, so proposing it proposes something unrepresentable.
       AND c.safe_to_node
"""


def _query(sql_tail: str, params: dict) -> list[Candidate]:
    return _rows_to_candidates(db.query(_SELECT + sql_tail, params))



def catalogue_rows(snapshot_id: str) -> int:
    """How many crossings the snapshot's catalogue holds."""
    r = db.query_one("SELECT count(*) AS n FROM crossings WHERE snapshot_id=%s",
                     (snapshot_id,))
    return int(r["n"]) if r else 0


def detect_on_demand(snapshot_id: str, *, closure_link_ids, route_link_ids,
                     buffer_m: float = ON_DEMAND_BUFFER_M):
    """Detect crossings around the closure and corridor, at request time.

    For snapshots with no crossings catalogue - which includes the current
    national one, ingested before crossing detection existed. Without this, an
    empty catalogue yields zero candidates and the analysis reports "no
    sensitivity found", which is a false negative dressed as a finding.

    Bounded by construction: it reads source features within `buffer_m` of the
    closure and the canonical route, not the country. It is the same detector
    the ingest uses, over a smaller set of lines.

    Returns (candidates, complete). `complete` is False when the region was
    clipped, so the caller can say the search was partial rather than empty.
    """
    from shapely import wkb
    from shapely.geometry import LineString
    from shapely.ops import linemerge

    from . import crossings as crossings_mod

    region_links = list(closure_link_ids) + list(route_link_ids)
    rows = db.query(
        "SELECT l.closure_group_id, l.rca_code, l.model_asset_type, l.oneway,"
        "       l.quality_flags, n.display_name,"
        "       COALESCE(n.is_ramp, false) AS is_ramp,"
        "       ST_AsBinary(ST_Collect(l.geom_2193)) AS g"
        "  FROM links l"
        "  LEFT JOIN link_display_names n ON n.snapshot_id = l.snapshot_id"
        "                        AND n.closure_group_id = l.closure_group_id"
        " WHERE l.snapshot_id = %s AND l.mode_vehicle"
        "   AND ST_DWithin(l.geom_2193, (SELECT ST_Collect(geom_2193)"
        "        FROM links WHERE snapshot_id = %s AND link_id = ANY(%s)), %s)"
        " GROUP BY 1,2,3,4,5,6,7",
        (snapshot_id, snapshot_id, region_links, buffer_m))

    geoms, ids, attrs, names = [], [], [], {}
    for r in rows:
        g = wkb.loads(bytes(r["g"]))
        merged = linemerge(g) if g.geom_type != "LineString" else g
        parts = ([merged] if merged.geom_type == "LineString"
                 else list(getattr(merged, "geoms", [])))
        for part in parts:
            if len(part.coords) < 2:
                continue
            geoms.append(LineString(part.coords))
            ids.append(r["closure_group_id"])
            attrs.append({"road_name": r["display_name"],
                          "rca_code": r["rca_code"],
                          "model_asset_type": r["model_asset_type"],
                          "oneway": r["oneway"],
                          "is_ramp": bool(r["is_ramp"]),
                          "quality_flags": list(r["quality_flags"] or [])})
        names[r["closure_group_id"]] = r["display_name"]

    if not geoms:
        return [], False

    found = crossings_mod.detect(geoms, ids, end_guard_m=0.05)

    # Ordered by distance to the CANONICAL ROUTE, nearest first. On-demand
    # crossings have no catalogue id to rank by, and id order would be
    # detection order, which is arbitrary. Proximity to the corridor is the
    # signal the Greendale evidence supports: the causal crossing is 0.0 m
    # from the route and the decoy is 654 m away.
    route_geom = None
    if route_link_ids:
        rr = db.query_one(
            "SELECT ST_AsBinary(ST_Collect(geom_2193)) AS g FROM links "
            " WHERE snapshot_id=%s AND link_id = ANY(%s)",
            (snapshot_id, list(route_link_ids)))
        if rr and rr["g"]:
            route_geom = wkb.loads(bytes(rr["g"]))

    def _rank_key(x):
        if route_geom is None:
            return 0.0
        from shapely.geometry import Point as _P
        return route_geom.distance(_P(x.x, x.y))

    found = sorted(found, key=_rank_key)

    out = []
    for i, x in enumerate(found):
        # Classified for RANKING only - it never decides anything.
        out.append(Candidate(
            crossing_id=-(i + 1),          # negative: not a catalogue row
            source_a=x.amds_a, source_b=x.amds_b,
            x=x.x, y=x.y,
            classifier_disposition=None, classifier_reason=None,
            classifier_confidence=None,
            name_a=names.get(x.amds_a), name_b=names.get(x.amds_b)))
    return out, True


def find(snapshot_id: str, *, closure_link_ids: Sequence[int],
         route_link_ids: Sequence[int] = (),
         port_node_ids: Sequence[int] = (),
         force_near: Sequence[tuple] = (),
         force_radius_m: float = 30.0,
         max_candidates: int = MAX_CANDIDATES) -> CandidateSearch:
    """Candidates for one analysis, from deterministic evidence.

    `route_link_ids` is the canonical replacement route. Passing it is what
    makes this a corridor search rather than a buffer, and omitting it is
    supported only because a DISCONNECTED answer has no route - in which case
    the closure, the ports and the isolated side are all there is to go on,
    and the search says so in its notes.
    """
    search = CandidateSearch()
    seen: dict[int, Candidate] = {}

    # WHICH SOURCE. An empty catalogue must trigger on-demand detection, never
    # a false "zero candidates": the two are opposite facts wearing the same
    # number, and the wrong one reads as "this answer is robust".
    search.catalogue_rows = catalogue_rows(snapshot_id)
    if search.catalogue_rows == 0:
        found, complete = detect_on_demand(
            snapshot_id, closure_link_ids=closure_link_ids,
            route_link_ids=route_link_ids)
        search.source_kind = (ON_DEMAND_NEIGHBOURHOOD if found or complete
                              else UNAVAILABLE)
        search.source_complete = bool(complete)
        search.sources_used.append("on_demand")
        search.by_source["on_demand"] = len(found)
        search.considered = len(found)
        for c in found[:max_candidates]:
            seen[c.crossing_id] = c
        search.truncated = len(found) > max_candidates
        search.notes.append(
            "the snapshot carries no crossings catalogue, so crossings were "
            "detected around the closure and the canonical corridor at "
            "request time - bounded, not national")
        search.candidates = list(seen.values())
        return search

    search.source_kind = PRECOMPUTED_CATALOGUE
    search.source_complete = True
    closure = list(closure_link_ids)
    route = [l for l in route_link_ids if l not in set(closure)]
    ports = list(port_node_ids)

    def add(source: str, found: list[Candidate]) -> None:
        search.by_source[source] = 0
        for c in found:
            search.considered += 1
            if c.crossing_id in seen:
                continue
            if len(seen) >= max_candidates:
                search.truncated = True
                continue
            seen[c.crossing_id] = c
            search.by_source[source] += 1
        if search.by_source[source] or source not in search.sources_used:
            search.sources_used.append(source)

    # 1. The closure itself.
    add("closure", _query(
        "  AND ST_DWithin(c.geom_2193, (SELECT ST_Collect(geom_2193) FROM links"
        "      WHERE snapshot_id=%(snap)s AND link_id = ANY(%(closure)s)),"
        "      %(r)s)"
        " ORDER BY c.crossing_id",
        {"snap": snapshot_id, "closure": closure, "r": CLOSURE_RADIUS_M}))

    # 2. THE CANONICAL DETOUR CORRIDOR. The source that finds the crossing
    #    2.8 km away that actually matters.
    if route:
        add("corridor", _query(
            "  AND ST_DWithin(c.geom_2193, (SELECT ST_Collect(geom_2193) FROM"
            "      links WHERE snapshot_id=%(snap)s AND link_id = ANY(%(route)s)),"
            "      %(r)s)"
            " ORDER BY c.crossing_id",
            {"snap": snapshot_id, "route": route, "r": CORRIDOR_RADIUS_M}))
    else:
        search.notes.append(
            "no canonical route to search along - the answer was not a routed "
            "one, so the corridor and circuit sources are unavailable and the "
            "search is narrower than usual")

    # 3. The movement's ports.
    if ports:
        add("ports", _query(
            "  AND ST_DWithin(c.geom_2193, (SELECT ST_Collect(geom_2193) FROM"
            "      nodes WHERE snapshot_id=%(snap)s AND node_id = ANY(%(ports)s)),"
            "      %(r)s)"
            " ORDER BY c.crossing_id",
            {"snap": snapshot_id, "ports": ports, "r": PORT_RADIUS_M}))

    # 4. INSIDE the circuit the detour encloses. A perimeter route exists
    #    because the graph had no chord across the middle; a crossing in there
    #    is a candidate chord.
    if route:
        add("inside_circuit", _query(
            "  AND ST_Contains((SELECT ST_ConvexHull(ST_Collect(geom_2193))"
            "        FROM links WHERE snapshot_id=%(snap)s"
            "         AND link_id = ANY(%(all)s)), c.geom_2193)"
            " ORDER BY c.crossing_id",
            {"snap": snapshot_id, "all": route + closure}))

    # 5. FORCED. Crossings named by the caller, admitted regardless of
    #    relevance and regardless of the bound. Not a production source:
    #    it exists so an audit can push a crossing the relevance rule
    #    EXCLUDES through the counterfactual machinery and measure that it
    #    changes nothing. Without it, an exclusion and a non-material
    #    crossing look identical in the output, and the acceptance claim
    #    that the decoy is non-material is vacuous rather than measured.
    for fx, fy in force_near:
        forced = _query(
            "  AND ST_DWithin(c.geom_2193,"
            "      ST_SetSRID(ST_MakePoint(%(fx)s,%(fy)s),2193), %(fr)s)"
            " ORDER BY c.crossing_id",
            {"snap": snapshot_id, "fx": fx, "fy": fy,
             "fr": force_radius_m})
        search.by_source.setdefault("forced", 0)
        for c in forced:
            if c.crossing_id not in seen:
                seen[c.crossing_id] = c
                search.by_source["forced"] += 1
        if "forced" not in search.sources_used:
            search.sources_used.append("forced")
        search.notes.append(
            "a crossing was FORCED into the candidate set for audit; it is "
            "not there because the relevance rule selected it")

    search.candidates = list(seen.values())
    if search.truncated:
        search.notes.append(
            f"bound of {max_candidates} reached after considering "
            f"{search.considered}: 'no sensitivity found' would NOT mean this "
            f"answer is robust")
    return search
