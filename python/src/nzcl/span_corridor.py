"""Which road between A and B the outage actually runs along.

THE QUESTION THIS ANSWERS, AND THE ONE IT MUST NOT
--------------------------------------------------
Two handles fix two POINTS. They do not fix a ROAD. Between any two points on
a network there are many ways through, and the shortest one is frequently not
the one the user drew:

              Queen Street
                   |
    Church St --A--+--B-- Church St
                   |

A and B are 300 m apart on Church Street. If Queen Street happens to offer a
shorter way round, a plain shortest-path search closes Queen Street and reports
a confident number about a road nobody selected.

So this module does not ask "what is the shortest path from A to B". It asks
"which corridor did the user mean", ranks the answers on evidence a person
could check, and says so when the evidence does not separate them.

WHERE THE EVIDENCE COMES FROM
-----------------------------
`corridor.Continuity`, unchanged and unedited. That type already encodes the
hierarchy this project settled on - route designation, then canonical name,
then degree-two, then state-highway status, then road class, then heading -
together with the argument for that ordering and the record of the
order-sensitivity defect that shaped it.

Reimplementing the hierarchy here would create a second copy to drift against
the first, and the two would disagree the first time either was tuned. This
module owns the SEARCH and the CANDIDATES; `corridor.py` owns what counts as
one road carrying on, and is imported read-only.

WHY THE SEARCH IS LOCAL RATHER THAN `routing.route`
---------------------------------------------------
Corridor selection needs a cost the measurement engine has no business
carrying: a search that PREFERS staying on the same named road, so the
same-road corridor is offered even where a side street is physically shorter.
That preference belongs to candidate generation, not to measurement, and
mixing it into the shared router would silently bend every detour in the
system towards named roads.

Measurement still runs through `routing.route` on the real graph. Nothing here
computes a distance anyone is shown.

WHAT IS DELIBERATELY NOT DONE
-----------------------------
No intermediate waypoint. A two-point span with visible ambiguity was the
agreed first version; a third handle is a later change and would alter the
candidate model, not extend it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Literal, Sequence

from . import db, snap
from .corridor import HEADING_TOLERANCE_DEG, Continuity
from .routing import Metric, Profile
from .vsplit import LinkInterval, Traversal

#: Bump when the SEARCH or the RANKING changes shape. Candidate ids embed it.
CORRIDOR_MODEL_VERSION = "1.0.0"

#: How many corridors to offer. Three is what an interface can present as a
#: choice; more is a list nobody reads.
MAX_CANDIDATES = 3

#: A rival that ties on every evidence tier is a genuine choice unless it is
#: also decisively longer. Beyond this ratio the shorter one is preferred
#: without asking, because "same evidence, 3x the road" is not a close call.
AMBIGUITY_LENGTH_RATIO = 1.25

#: Multiplier applied to links that continue the handle's own road in the
#: continuity-preferred search. Low enough to beat a moderately shorter side
#: street, not so low that it would drag a corridor miles along a trunk road:
#: this only ever GENERATES a candidate, and the ranking below decides.
CONTINUITY_PREFERENCE = 0.25

#: An alternative longer than this multiple of the best candidate is not a
#: plausible reading of what the user drew, and offering it would pad the
#: choice with noise. Applied after generation so it never suppresses the
#: only corridor found.
MAX_ALTERNATIVE_RATIO = 4.0

#: The search is confined to a box around the two handles, because a corridor
#: between two points 200 m apart does not wander across the country - and
#: searching as though it might costs the whole national edge set on every
#: request. Unbounded, one corridor query loaded 731,286 arcs and took 800 ms;
#: five of them made corridor selection 4.2 s.
#:
#: The box is the two host links' extents, grown by whichever is larger: a flat
#: margin, or a multiple of the span's own size. The flat margin keeps short
#: spans workable - a 50 m outage still gets a 2 km box to find a way round -
#: and the multiple keeps long spans proportionate.
#:
#: A bounded search that finds nothing is retried without the box, so this can
#: only ever cost time, never a corridor.
SEARCH_MARGIN_M = 2_000.0
SEARCH_MARGIN_RATIO = 4.0

_MODE_COLUMN = {"car": "mode_vehicle", "heavy": "mode_vehicle_heavy",
                "emergency": "mode_emergency"}

Origin = Literal["same_link", "shortest", "same_name", "same_designation",
                 "alternative"]


@dataclass(frozen=True)
class HandleOption:
    """One link a handle could legitimately be held to sit on.

    A crossroads produces several of these at one coordinate. Which one the
    handle belongs to decides which road the outage runs along, so they all
    reach candidate generation - see `snap.SnapResult.equivalent`.
    """

    link_id: int
    fraction: float


@dataclass(frozen=True)
class SpanStep:
    """One link the corridor occupies, and how much of it."""

    link_id: int
    amds_id: str
    road_name: str | None
    route_designation: str | None
    traversal: Traversal
    from_fraction: float
    to_fraction: float
    length_m: float

    @property
    def covered_m(self) -> float:
        return (self.to_fraction - self.from_fraction) * self.length_m

    @property
    def interval(self) -> LinkInterval:
        return LinkInterval(self.link_id, self.from_fraction, self.to_fraction,
                            self.traversal, self.length_m)


@dataclass
class SpanCandidate:
    """One corridor the outage could be, with the evidence for it."""

    candidate_id: str
    steps: list[SpanStep]
    length_m: float
    origin: Origin

    #: Per-joint evidence, one entry per pair of consecutive steps.
    joints: list[Continuity] = field(default_factory=list)

    @property
    def intervals(self) -> list[LinkInterval]:
        return [s.interval for s in self.steps]

    @property
    def link_ids(self) -> list[int]:
        return [s.link_id for s in self.steps]

    @property
    def designation_continuous(self) -> bool:
        return bool(self.joints) and all(
            j.route_designation_match for j in self.joints)

    @property
    def name_continuous(self) -> bool:
        return bool(self.joints) and all(j.road_name_match for j in self.joints)

    @property
    def heading_continuous(self) -> bool:
        return all(j.heading_continuous for j in self.joints)

    @property
    def road_changes(self) -> int:
        """Joints where neither the name nor the designation carries on.

        A corridor that changes road twice is a worse account of "the outage
        is on this road" than one that never changes, whatever the lengths.
        """
        return sum(1 for j in self.joints
                   if not (j.road_name_match or j.route_designation_match))

    @property
    def evidence_codes(self) -> tuple[str, ...]:
        """The weakest evidence present at every joint, as stated codes.

        Intersection rather than union: a corridor is only "name continuous"
        if the name continues at EVERY joint, and reporting a code that held
        at one joint out of four would overstate it.
        """
        if not self.joints:
            return ("SINGLE_LINK_NO_JOINT",)
        common = set(self.joints[0].codes)
        for j in self.joints[1:]:
            common &= set(j.codes)
        return tuple(sorted(common))

    @property
    def rank_key(self) -> tuple:
        """Lexicographic, smaller is better, in the brief's stated order.

        1 same route designation
        2 same road name / corridor
        3 heading continuity
        4 fewest road changes
        5 physical length
        6 stable id

        A single-link corridor has no joint, so tiers 1-3 cannot be evidenced
        and it ranks on changes, length and identity. That is correct: there is
        no continuity claim to make about one link, and inventing one would let
        a single link outrank a corridor that demonstrably stays on one road.
        """
        return (
            0 if self.designation_continuous else 1,
            0 if self.name_continuous else 1,
            0 if self.heading_continuous else 1,
            self.road_changes,
            round(self.length_m, 3),
            self.candidate_id,
        )


@dataclass
class CorridorChoice:
    """The ranked corridors, and whether choosing between them was safe."""

    candidates: list[SpanCandidate]
    chosen: SpanCandidate | None
    ambiguous: bool
    ambiguity_reason: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return self.chosen is not None


def options_from_snap(result: snap.SnapResult) -> list[HandleOption]:
    """Every link a snapped handle could sit on, at one coordinate.

    Reads `chosen` plus `equivalent`, never `alternatives`: alternatives are a
    different PLACE and are a question for the user, not a host to consider.
    """
    if result.chosen is None:
        return []
    return [HandleOption(c.link_id, c.fraction)
            for c in [result.chosen, *result.equivalent]]


def select(
    snapshot_id: str,
    a_options: Sequence[HandleOption],
    b_options: Sequence[HandleOption],
    *,
    profile: Profile = "car",
    limit: int = MAX_CANDIDATES,
    statement_timeout_ms: int = 20_000,
) -> CorridorChoice:
    """Rank the corridors A and B could describe, and flag a real tie."""
    if not a_options or not b_options:
        raise ValueError("both handles need at least one host link option")
    if _mode(profile) is None:
        raise ValueError(f"unsupported vehicle profile {profile!r}")

    link_ids = sorted({o.link_id for o in a_options}
                      | {o.link_id for o in b_options})
    links = _links(snapshot_id, link_ids)

    generated: list[SpanCandidate] = []
    for a in a_options:
        for b in b_options:
            if a.link_id == b.link_id:
                generated.extend(_same_link(links[a.link_id], a, b))
            else:
                generated.extend(_across_links(
                    snapshot_id, links, a, b, profile, limit,
                    statement_timeout_ms))

    # Deduplicate on what the corridor IS, keeping the first origin that found
    # it. Two searches converging on one corridor is agreement, not two
    # candidates, and presenting it twice would make a tie look like a choice.
    unique: dict[str, SpanCandidate] = {}
    for c in generated:
        unique.setdefault(c.candidate_id, c)

    plausible = _drop_implausible(list(unique.values()))
    ranked = sorted(plausible, key=lambda c: c.rank_key)[:limit]
    chosen = ranked[0] if ranked else None
    ambiguous, reason = _ambiguity(ranked)

    return CorridorChoice(candidates=ranked, chosen=chosen,
                          ambiguous=ambiguous, ambiguity_reason=reason)


def _drop_implausible(candidates: list[SpanCandidate]) -> list[SpanCandidate]:
    """Discard alternatives far longer than the shortest corridor found.

    The banning rounds will always find SOMETHING in a connected network, and
    in a dense one that something can be a corridor across town. Offering it as
    one of three readings of a 300 m outage would be padding the choice with
    noise. The shortest is never dropped, so this can empty nothing.
    """
    if len(candidates) < 2:
        return candidates
    shortest = min(c.length_m for c in candidates)
    if shortest <= 0:
        return candidates
    return [c for c in candidates
            if c.length_m / shortest <= MAX_ALTERNATIVE_RATIO]


def _same_link(link: dict, a: HandleOption, b: HandleOption
               ) -> list[SpanCandidate]:
    """Both handles on one link: the corridor is the stretch between them.

    There is no search to run and no continuity to evidence. The traversal
    records which way A -> B runs so a directional closure shuts the arc the
    traffic is actually on.
    """
    if abs(a.fraction - b.fraction) <= 0.0:
        return []
    lo, hi = sorted((a.fraction, b.fraction))
    traversal: Traversal = "forward" if a.fraction < b.fraction else "reverse"
    step = _step(link, traversal, lo, hi)
    return [_candidate([step], "same_link")]


def _across_links(snapshot_id: str, links: dict[int, dict], a: HandleOption,
                  b: HandleOption, profile: Profile, limit: int,
                  timeout_ms: int) -> list[SpanCandidate]:
    """Handles on different links: generate the plausible corridors.

    Two generation strategies, because they find different things.

    PREFERENCE finds the corridor that stays on the handle's own road even
    where a side street is shorter - the corner-cutting case.

    BANNING finds a corridor that is genuinely a different way round. It is
    needed because `pgr_dijkstra` returns ONE shortest path per pair: where two
    ways round are the same length, the planner picks one and the other is
    never generated at all. Without this the engine would silently choose
    between two equally good corridors and present the result as the only
    reading - the precise thing the ambiguity report exists to prevent. Each
    round bans the links the corridors so far run along and searches again;
    a corridor that shares every link with one already found is not an
    alternative.
    """
    la, lb = links[a.link_id], links[b.link_id]
    # The corridor leaves A through one end of its link and enters B through
    # one end of its own. Both ends of both are offered; the ranking decides.
    exits = {int(la["source_node"]): (0.0, a.fraction, "reverse"),
             int(la["target_node"]): (a.fraction, 1.0, "forward")}
    entries = {int(lb["source_node"]): (0.0, b.fraction, "forward"),
               int(lb["target_node"]): (b.fraction, 1.0, "reverse")}

    preferences: list[tuple[Origin, str | None, str | None]] = [
        ("shortest", None, None)]
    if la.get("road_name"):
        preferences.append(("same_name", str(la["road_name"]), None))
    if la.get("route_designation"):
        preferences.append(
            ("same_designation", None, str(la["route_designation"])))

    out: list[SpanCandidate] = []
    seen: set[str] = set()
    banned = {a.link_id, b.link_id}

    def build(u: int, v: int, middle: list[SpanStep], origin: Origin) -> bool:
        a_from, a_to, a_trav = exits[u]
        b_from, b_to, b_trav = entries[v]
        steps = [_step(la, a_trav, a_from, a_to), *middle,
                 _step(lb, b_trav, b_from, b_to)]
        # A handle exactly on a junction contributes no road at that end. The
        # corridor is still valid; the empty step is dropped rather than
        # closing a zero-length interval.
        steps = [s for s in steps if s.covered_m > 0.0]
        if not steps:
            return False
        candidate = _candidate(steps, origin)
        if candidate.candidate_id in seen:
            return False
        seen.add(candidate.candidate_id)
        out.append(candidate)
        return True

    # The handles' links meet directly: the corridor is simply the run from A
    # to the shared junction and on to B, with nothing in between.
    #
    # This is built here rather than left to the planner because pgRouting
    # returns no row for a pair whose start vertex IS its end vertex. Relying
    # on the search to produce it meant the most ordinary corridor of all -
    # two handles on adjacent links - was never generated, and selection fell
    # through to whatever long way round existed. On SH 6 that turned a 185 m
    # outage into an 813 m one across four other streets, with no sign
    # anything was wrong.
    for shared in sorted(set(exits) & set(entries)):
        build(shared, shared, [], "shortest")

    def collect(paths, origin: Origin) -> int:
        added = 0
        for (u, v), arc_ids in sorted(paths.items()):
            if u not in exits or v not in entries:
                continue
            middle = _middle_steps(snapshot_id, arc_ids)
            if middle is None:
                continue
            if build(u, v, middle, origin):
                added += 1
        return added

    # Bounded first. Retried without the box only if it found nothing at all,
    # so confining the search can cost time but never a corridor.
    for envelope in (_envelope(la, lb), None):
        for origin, name, designation in preferences:
            paths = _search(snapshot_id, sorted(exits), sorted(entries),
                            profile, excluded_links=sorted(banned),
                            prefer_name=name, prefer_designation=designation,
                            envelope=envelope, timeout_ms=timeout_ms)
            if paths is not None:
                collect(paths, origin)

        for _ in range(max(0, limit - 1)):
            middles = {lid for c in out for lid in c.link_ids[1:-1]}
            if not middles - banned:
                # Every corridor found runs only along the two handle links, so
                # there is nothing left to ban and no different way round.
                break
            banned |= middles
            paths = _search(snapshot_id, sorted(exits), sorted(entries),
                            profile, excluded_links=sorted(banned),
                            prefer_name=None, prefer_designation=None,
                            envelope=envelope, timeout_ms=timeout_ms)
            if paths is None or collect(paths, "alternative") == 0:
                break

        if out:
            break

    return out


def _envelope(la: dict, lb: dict) -> tuple[float, float, float, float]:
    """A box around both host links, grown enough to hold a way round.

    Returned in EPSG:2193 metres, which is the only frame anything here
    measures in.
    """
    minx = min(float(la["minx"]), float(lb["minx"]))
    miny = min(float(la["miny"]), float(lb["miny"]))
    maxx = max(float(la["maxx"]), float(lb["maxx"]))
    maxy = max(float(la["maxy"]), float(lb["maxy"]))
    diagonal = ((maxx - minx) ** 2 + (maxy - miny) ** 2) ** 0.5
    margin = max(SEARCH_MARGIN_M, SEARCH_MARGIN_RATIO * diagonal)
    return (minx - margin, miny - margin, maxx + margin, maxy + margin)


def _search(snapshot_id: str, sources: Sequence[int], targets: Sequence[int],
            profile: Profile, *, excluded_links: Sequence[int],
            prefer_name: str | None, prefer_designation: str | None,
            envelope: tuple[float, float, float, float] | None,
            timeout_ms: int) -> dict[tuple[int, int], list[int]] | None:
    """Arc paths for every (source, target) pair, from one edge-set load.

    The preference is applied as a multiplier inside the edge query rather
    than by filtering the network to matching links. Filtering would make a
    corridor that briefly crosses an unnamed service link unreachable, which
    is a common shape at roundabouts and bridges; a preference merely makes
    staying on the road cheaper, and still finds a way through when it cannot.

    Returns None when the search did not conclude. A caller must not read that
    as "no corridor exists" - it is the same distinction `ManyRouteResult`
    draws, and the reason this returns None rather than an empty mapping.
    """
    mode = _mode(profile)
    snap_id = snapshot_id.replace("'", "''")
    excluded = ",".join(str(int(i)) for i in excluded_links) or "-1"

    if prefer_name is not None:
        literal = prefer_name.replace("'", "''")
        factor = (f"CASE WHEN lower(trim(coalesce(dn.display_name, l.road_name))) "
                  f"= lower(trim('{literal}')) THEN {CONTINUITY_PREFERENCE} "
                  f"ELSE 1.0 END")
    elif prefer_designation is not None:
        literal = prefer_designation.replace("'", "''")
        factor = (f"CASE WHEN lower(trim(coalesce(dn.route_designation, ''))) "
                  f"= lower(trim('{literal}')) THEN {CONTINUITY_PREFERENCE} "
                  f"ELSE 1.0 END")
    else:
        factor = "1.0"

    # The spatial predicate goes on `links`, which carries the GIST index, and
    # the join then runs over the handful of links inside the box rather than
    # over every arc in the country.
    if envelope is None:
        bbox = ""
    else:
        minx, miny, maxx, maxy = (float(v) for v in envelope)
        bbox = (f"   AND l.geom_2193 && ST_MakeEnvelope("
                f"{minx!r}, {miny!r}, {maxx!r}, {maxy!r}, 2193)")

    edge_sql = (
        f"SELECT a.arc_id AS id, a.source, a.target, "
        f"       a.cost_distance_m * {factor} AS cost, "
        f"       -1::double precision AS reverse_cost "
        f"  FROM arcs a "
        f"  JOIN links l ON l.snapshot_id = a.snapshot_id "
        f"                AND l.link_id = a.link_id "
        f"  LEFT JOIN link_display_names dn ON dn.snapshot_id = l.snapshot_id "
        f"                AND dn.link_id = l.link_id "
        f" WHERE a.snapshot_id = '{snap_id}' AND a.{mode} "
        f"   AND a.cost_distance_m IS NOT NULL "
        f"   AND NOT (a.link_id = ANY (ARRAY[{excluded}]::bigint[]))"
        f"{bbox}"
    )

    try:
        with db.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT set_config('statement_timeout', %s, true)",
                        (str(timeout_ms),))
                    cur.execute(
                        "SELECT start_vid, end_vid, edge FROM pgr_dijkstra("
                        "%s, %s::bigint[], %s::bigint[], directed => true) "
                        "ORDER BY start_vid, end_vid, seq",
                        (edge_sql, list(sources), list(targets)))
                    rows = cur.fetchall()
    except Exception:  # noqa: BLE001
        # Never reported as "no corridor": see the docstring.
        return None

    paths: dict[tuple[int, int], list[int]] = {}
    for r in rows:
        key = (int(r["start_vid"]), int(r["end_vid"]))
        paths.setdefault(key, [])
        edge = r["edge"]
        if edge is not None and int(edge) != -1:
            paths[key].append(int(edge))
    return paths


def _middle_steps(snapshot_id: str, arc_ids: Sequence[int]
                  ) -> list[SpanStep] | None:
    """The whole links a corridor crosses between its two end links."""
    if not arc_ids:
        return []
    rows = db.query(
        """
        SELECT a.arc_id, a.link_id, a.direction, l.amds_id, l.length_m,
               coalesce(dn.display_name, l.road_name) AS road_name,
               dn.route_designation
          FROM arcs a
          JOIN links l ON l.snapshot_id = a.snapshot_id AND l.link_id = a.link_id
     LEFT JOIN link_display_names dn ON dn.snapshot_id = l.snapshot_id
                                    AND dn.link_id = l.link_id
         WHERE a.snapshot_id = %s AND a.arc_id = ANY(%s)
        """,
        (snapshot_id, list(arc_ids)),
    )
    by_arc = {int(r["arc_id"]): r for r in rows}
    steps: list[SpanStep] = []
    for arc_id in arc_ids:
        row = by_arc.get(int(arc_id))
        if row is None:
            # An arc the planner returned that this query cannot see is a
            # broken assumption, not a corridor - refuse rather than build a
            # span with a hole in it.
            return None
        steps.append(_step(row, str(row["direction"]), 0.0, 1.0))
    return steps


def _step(row: dict, traversal: str, from_fraction: float,
          to_fraction: float) -> SpanStep:
    return SpanStep(
        link_id=int(row["link_id"]),
        amds_id=str(row["amds_id"]),
        road_name=row.get("road_name"),
        route_designation=row.get("route_designation"),
        traversal="forward" if traversal == "forward" else "reverse",
        from_fraction=float(from_fraction),
        to_fraction=float(to_fraction),
        length_m=float(row["length_m"]),
    )


def _candidate(steps: list[SpanStep], origin: Origin) -> SpanCandidate:
    joints = [_joint(steps[i], steps[i + 1]) for i in range(len(steps) - 1)]
    return SpanCandidate(
        candidate_id=candidate_key(steps),
        steps=steps,
        length_m=sum(s.covered_m for s in steps),
        origin=origin,
        joints=joints,
    )


def _joint(prev: SpanStep, nxt: SpanStep) -> Continuity:
    """Evidence that `nxt` is `prev` carrying on, in `corridor.py`'s terms.

    Heading is left unevidenced here rather than guessed. The beam search in
    `corridor.py` measures it from arc endpoint coordinates it already has in
    hand; this search does not carry them, and a bearing computed from the
    wrong end would be worse than an absent one - it would be evidence.
    Degree-two is likewise absent: the corridor is a path that was found, not
    a walk that had no choice, so the field would not mean what it means there.
    """
    name_a = (prev.road_name or "").strip().casefold()
    name_b = (nxt.road_name or "").strip().casefold()
    des_a = (prev.route_designation or "").strip().casefold()
    des_b = (nxt.route_designation or "").strip().casefold()
    return Continuity(
        route_designation_match=bool(des_a) and des_a == des_b,
        road_name_match=bool(name_a) and name_a == name_b,
    )


def _ambiguity(ranked: Sequence[SpanCandidate]) -> tuple[bool, str | None]:
    """Do the top two corridors differ on anything a reader could check?

    Ambiguous when they tie on every evidence tier AND the runner-up is not
    decisively longer. Ties broken purely on length are exactly the case where
    silently picking one closes a road the user did not draw.
    """
    if len(ranked) < 2:
        return False, None
    best, rival = ranked[0], ranked[1]
    if best.rank_key[:4] != rival.rank_key[:4]:
        return False, None
    if best.link_ids == rival.link_ids:
        return False, None
    if best.length_m > 0 and rival.length_m / best.length_m > AMBIGUITY_LENGTH_RATIO:
        return False, None

    return True, (
        f"Two corridors between these points are equally well evidenced: "
        f"{_describe(best)} ({best.length_m / 1000:.2f} km) and "
        f"{_describe(rival)} ({rival.length_m / 1000:.2f} km). "
        f"Choose which one the outage is on."
    )


def _describe(c: SpanCandidate) -> str:
    """The roads a corridor runs along, in order, without repeats.

    An unnamed link is called "(unnamed road)", never given its AMDS id. The id
    is a data-maintenance GUID; shown where a road name goes it tells a reader
    nothing and implies the interface is broken. "(unnamed road)" is honest -
    about a third of AMDS vehicle links carry no resolved name - and it is the
    convention the rest of this project already uses for exactly this case.
    Consecutive unnamed steps collapse into one entry, the same way consecutive
    steps of one named road do.
    """
    names: list[str] = []
    for s in c.steps:
        label = s.road_name or s.route_designation or "(unnamed road)"
        if not names or names[-1] != label:
            names.append(label)
    return " - ".join(names)


def candidate_key(steps: Sequence[SpanStep]) -> str:
    """Identity of a corridor, in terms the publisher chose.

    Hashed from AMDS feature ids, traversal and position - never `link_id`,
    which the noding pass hands out in ingest order. `stableid.py` records what
    that cost the first time: shuffling a nine-link fixture flipped a corridor
    choice on three seeds out of eight without one metre of road changing.
    """
    payload = "|".join((
        "outage-corridor", CORRIDOR_MODEL_VERSION,
        ",".join(f"{s.amds_id}:{s.traversal}:{s.from_fraction:.9f}"
                 f":{s.to_fraction:.9f}" for s in steps),
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _mode(profile: str) -> str | None:
    return _MODE_COLUMN.get(profile)


def _links(snapshot_id: str, link_ids: Sequence[int]) -> dict[int, dict]:
    rows = db.query(
        """
        SELECT l.link_id, l.amds_id, l.closure_group_id, l.length_m,
               l.source_node, l.target_node,
               coalesce(dn.display_name, l.road_name) AS road_name,
               dn.route_designation,
               ST_XMin(l.geom_2193) AS minx, ST_YMin(l.geom_2193) AS miny,
               ST_XMax(l.geom_2193) AS maxx, ST_YMax(l.geom_2193) AS maxy
          FROM links l
     LEFT JOIN link_display_names dn ON dn.snapshot_id = l.snapshot_id
                                    AND dn.link_id = l.link_id
         WHERE l.snapshot_id = %s AND l.link_id = ANY(%s)
         ORDER BY l.link_id
        """,
        (snapshot_id, list(link_ids)),
    )
    found = {int(r["link_id"]): r for r in rows}
    missing = sorted(set(link_ids) - set(found))
    if missing:
        raise KeyError(f"links {missing} are not in snapshot {snapshot_id!r}")
    return found


def candidate_as_dict(c: SpanCandidate) -> dict:
    return {
        "candidateId": c.candidate_id,
        "origin": c.origin,
        "lengthM": round(c.length_m, 1),
        "roads": _describe(c),
        "linkIds": c.link_ids,
        "steps": [
            {
                "linkId": s.link_id,
                "amdsId": s.amds_id,
                "roadName": s.road_name,
                "routeDesignation": s.route_designation,
                "traversal": s.traversal,
                "fromFraction": round(s.from_fraction, 9),
                "toFraction": round(s.to_fraction, 9),
                "coveredM": round(s.covered_m, 1),
            }
            for s in c.steps
        ],
        "evidence": {
            "routeDesignationContinuous": c.designation_continuous,
            "roadNameContinuous": c.name_continuous,
            "roadChanges": c.road_changes,
            "codes": list(c.evidence_codes),
        },
    }


def as_dict(choice: CorridorChoice) -> dict:
    return {
        "found": choice.found,
        "corridor": (candidate_as_dict(choice.chosen)
                     if choice.chosen else None),
        "candidates": [candidate_as_dict(c) for c in choice.candidates],
        "ambiguous": choice.ambiguous,
        "ambiguityReason": choice.ambiguity_reason,
        "corridorModelVersion": CORRIDOR_MODEL_VERSION,
    }
