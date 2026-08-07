"""Matching an AMDS source feature to an external road-name feature.

Nearest line is not sufficient, and it is not sufficient in a way that produces
confident wrong answers rather than obvious ones. A dual carriageway has its
twin 20 m away; a motorway ramp runs alongside the mainline for hundreds of
metres; a service lane parallels the road it serves. All three are the nearest
line to something they are not.

So a candidate is scored on how much of the road it actually covers, how far it
strays, which way it points, whether the two ends land on it, and whether the
administrative attributes agree - and then on how much better it is than the
best candidate carrying a *different* name. That last one does most of the
work: a match is only trustworthy when the alternatives are visibly worse.

Every candidate is kept with its scores, not only the winner, so a rule change
can be re-scored without re-running the spatial work and a reviewer can ask why
something was rejected.

Geometry is EPSG:2193 throughout. The AMDS feature is reassembled from its
graph children rather than re-downloaded: matching the children separately is
how one road ends up with three different names.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from . import db
from .naming import (
    SOURCE_LINZ_ROAD_SECTIONS,
    SOURCE_NZTA_RAMM,
    SOURCE_NZTA_STREET_NAMES,
    format_designation,
    is_designation,
    parse_route_number,
    search_key,
)

#: How far from the road to look for candidates, in metres. Wide enough to
#: reach the other carriageway of a divided road - which must be SEEN in order
#: to be rejected on the margin test, not merely missed.
SEARCH_RADIUS_M = 40.0

#: Distance at which a sample point counts as "on" the candidate.
COVER_TOLERANCE_M = 12.0

#: Sample points along each geometry. Enough that a multi-kilometre rural road
#: is not judged on eleven points spaced hundreds of metres apart.
SAMPLES = 20

HIGH, MEDIUM, LOW, NONE = "HIGH", "MEDIUM", "LOW", "NONE"

ALL_SOURCES = [SOURCE_NZTA_STREET_NAMES, SOURCE_LINZ_ROAD_SECTIONS,
               SOURCE_NZTA_RAMM]


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------

_MATCH_SQL = """
WITH merged AS (
    SELECT l.closure_group_id                                    AS gid,
           ST_LineMerge(ST_Collect(l.geom_2193 ORDER BY l.link_id)) AS geom,
           min(l.rca_code)                                       AS rca_code,
           min(l.rca_name)                                       AS rca_name,
           bool_or(l.oneway = 1)                                 AS oneway,
           min(l.urban_rural)                                    AS urban_rural,
           count(*)                                              AS link_count
      FROM links l
     WHERE l.snapshot_id = %(snap)s
       AND l.closure_group_id = ANY(%(gids)s)
     GROUP BY 1
),
-- A source feature whose children do not reconnect into one line is kept as
-- its longest part, and the fact is recorded rather than hidden: a partial
-- geometry can only under-state coverage, never invent it.
target AS (
    SELECT m.gid, m.rca_code, m.rca_name, m.oneway, m.urban_rural,
           m.link_count,
           GeometryType(m.geom) <> 'LINESTRING' AS split_parent,
           d.geom,
           ST_Length(d.geom)     AS len,
           ST_StartPoint(d.geom) AS p0,
           ST_EndPoint(d.geom)   AS p1
      FROM merged m
      CROSS JOIN LATERAL (
          SELECT g.geom FROM ST_Dump(m.geom) g
           ORDER BY ST_Length(g.geom) DESC LIMIT 1
      ) d
     WHERE ST_Length(d.geom) > 0
),
cand AS (
    SELECT t.*, e.source, e.feature_id, e.part, e.display_name, e.name_key,
           e.is_unnamed, e.is_state_highway, e.is_dual_carriageway,
           e.oneway AS cand_oneway, e.status AS cand_status,
           e.locality, e.locality_alt, e.territorial_authority,
           e.territorial_authority_alt, e.linz_road_section_ids,
           e.corridor, e.route_code, e.extra,
           e.length_m AS cand_len, e.geom_2193 AS cgeom
      FROM target t
      JOIN ext_road_names e
        ON e.geom_2193 && ST_Expand(t.geom, %(radius)s)
       AND ST_DWithin(e.geom_2193, t.geom, %(radius)s)
     WHERE e.source = ANY(%(sources)s::text[])
)
SELECT c.gid, c.source, c.feature_id, c.part, c.display_name, c.name_key,
       c.is_unnamed, c.is_state_highway, c.is_dual_carriageway,
       c.cand_oneway, c.cand_status, c.locality, c.locality_alt,
       c.territorial_authority, c.territorial_authority_alt,
       c.linz_road_section_ids, c.corridor, c.route_code, c.extra,
       c.rca_code, c.rca_name, c.oneway AS target_oneway, c.urban_rural,
       c.link_count, c.split_parent,
       c.len AS target_len, c.cand_len,
       -- Distance from each sample point along the ROAD to this candidate,
       -- returned in full rather than reduced here. One external feature is
       -- often a fragment of the road - a 5 km rural road can be eight LINZ
       -- sections all carrying the same name - and a per-candidate coverage
       -- figure would score every one of them low. The caller merges these
       -- element-wise across candidates sharing a name, which is exact.
       f.sep AS sep,
       -- How much of the CANDIDATE the road covers: catches a long arterial
       -- offered as the match for a 40 m stub of a side street.
       r.covered AS cand_covered_frac,
       ST_Distance(c.p0, c.cgeom) AS start_dist,
       ST_Distance(c.p1, c.cgeom) AS end_dist,
       degrees(ST_Azimuth(c.p0, c.p1))                             AS target_az,
       degrees(ST_Azimuth(ST_StartPoint(c.cgeom), ST_EndPoint(c.cgeom))) AS cand_az
  FROM cand c
  CROSS JOIN LATERAL (
      SELECT array_agg(d ORDER BY i) AS sep
        FROM (SELECT i, ST_Distance(
                         ST_LineInterpolatePoint(c.geom, i::float / %(n)s),
                         c.cgeom) AS d
                FROM generate_series(0, %(n)s) i) s
  ) f
  CROSS JOIN LATERAL (
      SELECT avg(CASE WHEN d <= %(tol)s THEN 1.0 ELSE 0.0 END) AS covered
        FROM (SELECT ST_Distance(
                       ST_LineInterpolatePoint(c.cgeom, i::float / %(n)s),
                       c.geom) AS d
                FROM generate_series(0, %(n)s) i) s
  ) r
"""


def score_candidates(snapshot_id: str, gids: Sequence[str], *,
                     sources: Sequence[str] | None = None,
                     radius_m: float = SEARCH_RADIUS_M,
                     tolerance_m: float = COVER_TOLERANCE_M,
                     samples: int = SAMPLES,
                     statement_timeout_ms: int = 600_000
                     ) -> dict[str, list[dict[str, Any]]]:
    """Every candidate within reach of each source feature, with its metrics."""
    if not gids:
        return {}
    with db.connection() as conn:
        with conn.cursor() as cur:
            # set_config, not SET: SET takes no bind parameters, a mistake this
            # project has made before.
            cur.execute("SELECT set_config('statement_timeout', %s, true)",
                        (str(statement_timeout_ms),))
            cur.execute(_MATCH_SQL, {
                "snap": snapshot_id, "gids": list(gids), "radius": radius_m,
                "tol": tolerance_m, "n": samples,
                "sources": list(sources) if sources else ALL_SOURCES,
            })
            rows = cur.fetchall()
    out: dict[str, list[dict[str, Any]]] = {g: [] for g in gids}
    for r in rows:
        out.setdefault(r["gid"], []).append(dict(r))
    return out


# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------

@dataclass
class Scored:
    """One NAME offered by one source, and the numbers the decision rests on.

    A name, not a feature: every feature from the same source carrying the same
    name is merged, because a road is routinely several features there and
    scoring each fragment separately makes a perfectly good name look like a
    12% match.
    """

    source: str
    feature_id: str
    name: str | None
    name_key: str | None
    is_unnamed: bool | None
    covered_frac: float
    cand_covered_frac: float
    mean_sep: float
    max_sep: float
    start_dist: float
    end_dist: float
    heading_diff: float | None
    length_ratio: float
    locality_agrees: bool | None
    ta_agrees: bool | None
    sh_agrees: bool | None
    is_dual_carriageway: bool | None
    score: float
    parts: int = 1
    is_designation_name: bool = False
    is_ramp_name: bool = False
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    def evidence(self) -> dict[str, Any]:
        return {
            "covered_frac": round(self.covered_frac, 4),
            "cand_covered_frac": round(self.cand_covered_frac, 4),
            "mean_sep_m": round(self.mean_sep, 2),
            "max_sep_m": round(self.max_sep, 2),
            "start_dist_m": round(self.start_dist, 2),
            "end_dist_m": round(self.end_dist, 2),
            "heading_diff_deg": (None if self.heading_diff is None
                                 else round(self.heading_diff, 1)),
            "length_ratio": round(self.length_ratio, 3),
            "locality_agrees": self.locality_agrees,
            "ta_agrees": self.ta_agrees,
            "state_highway_agrees": self.sh_agrees,
            "dual_carriageway": self.is_dual_carriageway,
            "source_features_merged": self.parts,
            "is_designation": self.is_designation_name,
            "is_ramp_name": self.is_ramp_name,
            "score": round(self.score, 4),
        }


#: A name the source itself says belongs to a ramp. Both sources write these
#: out in full - "QUARRY ROAD OFF RAMP", "LAMBIE DRIVE ON RAMP" - which is a
#: far better ramp detector than any attribute either of them publishes.
_RAMP_NAME = re.compile(r"\b(on|off)[\s/-]*ramp\b|\bramp\b|\binterchange\b",
                        re.IGNORECASE)


def is_ramp_name(name: str | None) -> bool:
    return bool(name and _RAMP_NAME.search(name))


def _heading_difference(a: float | None, b: float | None) -> float | None:
    """Smallest angle between two undirected bearings, in degrees.

    Undirected: a road digitised the other way round is the same road, so 180
    degrees apart counts as agreement.
    """
    if a is None or b is None:
        return None
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


def _agrees(a: str | None, *others: str | None) -> bool | None:
    """Whether a normalised value matches any of the alternatives offered.

    None when either side is silent - "we do not know" is not "they disagree",
    and conflating the two is how a missing locality becomes evidence.
    """
    if not a:
        return None
    vals = [o for o in others if o]
    if not vals:
        return None
    key = a.strip().casefold()
    return any(key == v.strip().casefold() for v in vals)


def _group_key(row: dict[str, Any]) -> tuple[str, str]:
    """One entry per (source, name). Unnamed rows group under a sentinel so an
    explicit "this road has no name" is still a single answer per source."""
    return (row["source"], row.get("name_key") or "\x00unnamed")


def evaluate_group(rows: Sequence[dict[str, Any]], *,
                   tolerance_m: float = COVER_TOLERANCE_M) -> Scored:
    """Merge every feature of one source carrying one name, then score it."""
    first = rows[0]
    target_len = float(first["target_len"] or 0.0)

    # Element-wise minimum across the group's separation profiles: for each
    # sample point along the road, how far to the NEAREST feature of this name.
    n = max(len(r["sep"] or []) for r in rows)
    best_sep = [9e9] * n
    for r in rows:
        for i, d in enumerate(r["sep"] or []):
            if d is not None and d < best_sep[i]:
                best_sep[i] = float(d)
    covered = (sum(1 for d in best_sep if d <= tolerance_m) / n) if n else 0.0
    mean_sep = sum(best_sep) / n if n else 9e9
    max_sep = max(best_sep) if best_sep else 9e9

    cand_len = sum(float(r["cand_len"] or 0.0) for r in rows)
    cand_covered = max(float(r["cand_covered_frac"] or 0.0) for r in rows)
    heading = min(
        (h for h in (_heading_difference(r.get("target_az"), r.get("cand_az"))
                     for r in rows) if h is not None), default=None)
    ratio = (min(target_len, cand_len) / max(target_len, cand_len)
             if target_len and cand_len else 0.0)

    locality = next((v for v in (_agrees(r.get("locality"), r.get("locality_alt"))
                                 for r in rows) if v is not None), None)
    ta = next((v for v in (_agrees(r.get("rca_name"),
                                   r.get("territorial_authority"),
                                   r.get("territorial_authority_alt"))
                           for r in rows) if v is not None), None)

    # A feature controlled by NZTA should match a candidate the other source
    # also calls a state highway.
    sh = None
    if first.get("is_state_highway") is not None:
        sh = bool(first["is_state_highway"]) == (first.get("rca_code") == 1)

    # Weights: coverage and closeness dominate, because they are the two that
    # a parallel road fails. The attribute signals adjust at the margin - they
    # are corroboration, not evidence on their own.
    score = (
        0.45 * covered
        + 0.20 * cand_covered
        + 0.20 * max(0.0, 1.0 - mean_sep / tolerance_m)
        + 0.10 * max(0.0, 1.0 - (heading if heading is not None else 90.0) / 45.0)
        + 0.05 * ratio
    )
    if locality is False or ta is False:
        score -= 0.05
    if sh is False:
        score -= 0.10

    name = first.get("display_name")
    return Scored(
        source=first["source"],
        feature_id=",".join(sorted({r["feature_id"] for r in rows}))[:200],
        name=name, name_key=first.get("name_key"),
        is_unnamed=first.get("is_unnamed"),
        covered_frac=covered, cand_covered_frac=cand_covered,
        mean_sep=mean_sep, max_sep=max_sep,
        start_dist=min(float(r.get("start_dist") or 9e9) for r in rows),
        end_dist=min(float(r.get("end_dist") or 9e9) for r in rows),
        heading_diff=heading, length_ratio=ratio,
        locality_agrees=locality, ta_agrees=ta, sh_agrees=sh,
        is_dual_carriageway=any(r.get("is_dual_carriageway") for r in rows),
        score=max(0.0, score), parts=len(rows),
        is_designation_name=is_designation(name),
        is_ramp_name=is_ramp_name(name),
        raw=first,
    )


@dataclass
class MatchOutcome:
    """What the matcher concluded for one source feature, and why."""

    gid: str
    confidence: str
    name: str | None = None
    source: str | None = None
    feature_id: str | None = None
    officially_unnamed: bool = False
    rival_name: str | None = None
    margin: float | None = None
    #: The route the road carries, where a source says so. Reported alongside
    #: the street name, never instead of it.
    designation: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    candidates: list[Scored] = field(default_factory=list)
    reasons: tuple[str, ...] = ()


#: Thresholds. Set deliberately high: the gate this feeds asks for 99% reviewed
#: precision on the HIGH class, and the cost of a wrong name on a map is a
#: reader who trusts the next one less.
HIGH_COVERED = 0.90
HIGH_MEAN_SEP = 6.0
HIGH_MAX_SEP = 18.0
HIGH_HEADING = 20.0
HIGH_MARGIN = 0.15
MEDIUM_COVERED = 0.70
MEDIUM_MEAN_SEP = 12.0
LOW_COVERED = 0.30

#: Where a name conflict is decided. Below this the two are treated as one
#: name written two ways rather than two different roads.
NAME_AGREEMENT = 0.999


def score_all(rows: Iterable[dict[str, Any]], *,
              tolerance_m: float = COVER_TOLERANCE_M) -> list[Scored]:
    """Merge each source's same-named features, then rank."""
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in rows:
        grouped.setdefault(_group_key(r), []).append(r)
    return sorted(
        (evaluate_group(g, tolerance_m=tolerance_m) for g in grouped.values()),
        key=lambda s: (-s.score, s.source, s.feature_id))


def classify(gid: str, rows: Iterable[dict[str, Any]], *,
             is_ramp: bool = False,
             preferred_sources: Sequence[str] = ()) -> MatchOutcome:
    """Decide what, if anything, the external sources say about one feature.

    `preferred_sources` changes attribution, never the answer. Where two
    sources independently arrive at the same name and both would stand on
    their own, the name is credited to the preferred one - in practice the one
    whose licence permits it to be displayed. A source is never promoted to
    supply a name it did not earn on its own geometry.
    """
    scored = score_all(rows)
    if not scored:
        return MatchOutcome(gid=gid, confidence=NONE, reasons=("NO_CANDIDATE",))

    reasons: list[str] = []
    # A road whose own candidates are called "QUARRY ROAD OFF RAMP" is a ramp,
    # whatever the asset attributes say. Both sources write ramps out in full,
    # and this catches the ones RAMM's shRampType misses.
    if any(s.is_ramp_name for s in scored if s.name_key):
        is_ramp = True

    # A highway designation is not a competing name for a street; it is the
    # route the street carries. Both sources publish both - "GREAT SOUTH ROAD"
    # in one and "State Highway 1" in the other for the same road - and reading
    # that as a disagreement is how a real street name gets suppressed.
    streets = [s for s in scored if s.name_key and not s.is_designation_name]
    designations = [s for s in scored if s.name_key and s.is_designation_name]
    named = streets or designations
    designation = next(
        (normalise_external_name(s.name) for s in designations
         if _geometry_is_convincing(s)), None)

    # Nothing named nearby, but a source that classifies unnamed roads says
    # this one has no name. That is a resolved answer, not a failure - provided
    # the geometry actually agrees.
    if not named:
        unnamed = [s for s in scored
                   if s.is_unnamed and s.source == SOURCE_NZTA_STREET_NAMES]
        best_unnamed = unnamed[0] if unnamed else None
        if best_unnamed and _geometry_is_convincing(best_unnamed):
            return MatchOutcome(
                gid=gid, confidence=MEDIUM if is_ramp else HIGH,
                name=None, source=best_unnamed.source,
                feature_id=best_unnamed.feature_id, officially_unnamed=True,
                evidence=best_unnamed.evidence(), candidates=scored,
                reasons=("SOURCE_MARKS_ROAD_UNNAMED",)
                + (("RAMP_NOT_AUTO_ADOPTED",) if is_ramp else ()),
            )
        return MatchOutcome(gid=gid, confidence=NONE, candidates=scored,
                            reasons=("NO_NAMED_CANDIDATE",))

    best = named[0]
    # Credit an equally good candidate from a preferred source. Same name, same
    # bar; only the attribution moves.
    if preferred_sources and best.source not in preferred_sources:
        swap = next((s for s in named
                     if s.source in preferred_sources
                     and s.name_key == best.name_key
                     and _geometry_is_convincing(s)), None)
        if swap is not None:
            best = swap
            reasons.append("ATTRIBUTED_TO_PREFERRED_SOURCE")

    # Rivals are compared like with like: a street name against another street
    # name, a designation against another designation. State Highway 1 against
    # State Highway 59 IS a disagreement and stays one.
    rival = next((s for s in named if s.name_key != best.name_key), None)
    margin = None if rival is None else best.score - rival.score

    # The decisive test. A road with a plausible neighbour carrying a different
    # name is exactly the dual-carriageway and frontage-road case, and it is
    # not resolved by picking the closer one.
    if rival is not None and margin is not None and margin < HIGH_MARGIN:
        reasons.append("RIVAL_NAME_TOO_CLOSE")

    confidence = NONE
    if _geometry_is_convincing(best) and "RIVAL_NAME_TOO_CLOSE" not in reasons:
        confidence = HIGH
    elif (best.covered_frac >= MEDIUM_COVERED
          and best.mean_sep <= MEDIUM_MEAN_SEP):
        confidence = MEDIUM
    elif best.covered_frac >= LOW_COVERED:
        confidence = LOW

    # A ramp runs alongside the mainline for its whole length, so geometry
    # alone cannot tell them apart. Never adopted automatically.
    if is_ramp and confidence == HIGH:
        confidence = MEDIUM
        reasons.append("RAMP_NOT_AUTO_ADOPTED")
    # A divided road's other carriageway is 20 m away and carries the same
    # name as often as not. Demand that the road be genuinely covered.
    if (best.is_dual_carriageway and confidence == HIGH
            and best.covered_frac < 0.97):
        confidence = MEDIUM
        reasons.append("DIVIDED_CARRIAGEWAY_NEEDS_FULL_COVER")
    if best.is_designation_name:
        reasons.append("DESIGNATION_ONLY")

    return MatchOutcome(
        gid=gid, confidence=confidence, name=best.name, source=best.source,
        feature_id=best.feature_id, rival_name=rival.name if rival else None,
        margin=margin, designation=designation, evidence=best.evidence(),
        candidates=scored, reasons=tuple(reasons),
    )


def _geometry_is_convincing(s: Scored) -> bool:
    return (s.covered_frac >= HIGH_COVERED
            and s.mean_sep <= HIGH_MEAN_SEP
            and s.max_sep <= HIGH_MAX_SEP
            and (s.heading_diff is None or s.heading_diff <= HIGH_HEADING))


# --------------------------------------------------------------------------
# name normalisation across sources
# --------------------------------------------------------------------------

def normalise_external_name(name: str | None) -> str | None:
    """Apply the same designation rules to an external name as to an AMDS one.

    NZTA street names writes "SH 3" where LINZ writes "State Highway 3". Both
    mean the same road, and the interface should not show one of them.
    """
    if not name:
        return None
    if is_designation(name):
        n = parse_route_number(name)
        if n is not None:
            return format_designation(n)
    return name


def persist_candidates(snapshot_id: str, outcomes: Sequence[MatchOutcome],
                       *, keep: int = 8) -> int:
    """Write every candidate and its evidence, best first.

    `keep` bounds what is stored per source feature, not what was considered.
    A national pass over 180,731 features with every 40 m neighbour retained is
    tens of millions of rows for no analytical gain; the rejected leaders are
    what a reviewer needs.
    """
    written = 0
    gids = [o.gid for o in outcomes]
    with db.direct_connection(autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM road_name_candidates "
                " WHERE snapshot_id = %s AND closure_group_id = ANY(%s)",
                (snapshot_id, gids))
            with cur.copy(
                "COPY road_name_candidates (snapshot_id, closure_group_id, "
                "source, candidate_rank, candidate_name, candidate_ref, "
                "is_unnamed, score, evidence) FROM STDIN"
            ) as cp:
                for outcome in outcomes:
                    per_source: dict[str, int] = {}
                    for s in outcome.candidates:
                        rank = per_source.get(s.source, 0) + 1
                        if rank > keep:
                            continue
                        per_source[s.source] = rank
                        cp.write_row((
                            snapshot_id, outcome.gid, s.source, rank,
                            normalise_external_name(s.name), s.feature_id,
                            s.is_unnamed, s.score,
                            json.dumps(s.evidence()),
                        ))
                        written += 1
        conn.commit()
    return written
