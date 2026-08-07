"""The proof of concept: does external matching work well enough to adopt?

Runs the matcher over agreed cohorts of unresolved source features, then
measures how often it is right — using a check that is independent of the
decision.

The independence matters. The matcher decides from geometry and from the margin
over the best differently-named rival. Whether NZTA street names and LINZ road
sections, maintained separately, arrive at the same name is not an input to
that decision, so their agreement rate is a measurement of it rather than a
restatement of it.

Cohorts, from the unresolved population only:

    sh_all      every unresolved NZTA-controlled source feature
    urban       unresolved urban features, spread across authorities
    rural       unresolved rural features, spread across authorities
    linz_rca    unresolved features controlled by LINZ
    ramp        unresolved features lying along a RAMM ramp carriageway
    long        the longest unresolved features

Sampling is by `md5(closure_group_id)` rather than a random draw: no seed to
record, and the "before" and "after" of any rule change look at the same roads.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import db, namematch
from .naming import (
    SOURCE_LINZ_ROAD_SECTIONS,
    SOURCE_NZTA_STREET_NAMES,
    search_key,
)

COHORT_SIZES = {
    "urban": 150,
    "rural": 150,
    "linz_rca": 100,
    "ramp": 50,
    "long": 50,
}

#: NZTM northing through Cook Strait. Used only to report island balance.
COOK_STRAIT_NORTHING = 5_430_000


# --------------------------------------------------------------------------
# cohorts
# --------------------------------------------------------------------------

def _base(extra_where: str = "") -> str:
    """One unresolved source feature per row, with what the cohorts select on.

    DISTINCT ON has to lead its own ORDER BY, so this is always wrapped by the
    caller rather than having clauses appended to it.
    """
    return f"""
    SELECT DISTINCT ON (l.closure_group_id)
           l.closure_group_id AS gid,
           l.rca_name, l.rca_code, l.urban_rural,
           sum(l.length_m) OVER (PARTITION BY l.closure_group_id) AS group_len,
           ST_Y(ST_Centroid(l.geom_2193)) AS northing
      FROM links l
      JOIN link_names n
        ON n.snapshot_id = l.snapshot_id
       AND n.closure_group_id = l.closure_group_id
     WHERE l.snapshot_id = %(snap)s
       -- Features AMDS cannot name. Deliberately NOT "name_status =
       -- 'unresolved'": once enrichment has run, most of those features carry
       -- an external name and the cohorts would silently shrink to the hard
       -- residue, taking the measured precision with them. Keyed on the source
       -- instead, which enrichment does not move a feature out of.
       AND n.name_source IS DISTINCT FROM 'amds_routename'
       {extra_where}
     ORDER BY l.closure_group_id
    """


def _sample(sql: str, params: dict[str, Any], limit: int | None) -> list[str]:
    rows = db.query(sql, params)
    return [r["gid"] for r in rows][:limit] if limit else [r["gid"] for r in rows]


def cohorts(snapshot_id: str) -> dict[str, list[str]]:
    """Build every cohort. Deterministic, and reported with its real size."""
    snap = {"snap": snapshot_id}
    out: dict[str, list[str]] = {}

    # Every unresolved state-highway feature, not a sample of them.
    out["sh_all"] = _sample(
        f"SELECT gid FROM ({_base('AND l.rca_code = 1')}) b ORDER BY gid",
        snap, None)

    # Urban and rural, round-robined across controlling authorities so one
    # large council cannot supply the whole cohort.
    for cohort, value in (("urban", "urban"), ("rural", "rural")):
        out[cohort] = _sample(
            "SELECT gid FROM (SELECT b.gid, row_number() OVER ("
            "   PARTITION BY b.rca_name ORDER BY md5(b.gid)) AS rn"
            f"  FROM ({_base('AND l.urban_rural = %(ur)s')}) b) t "
            " ORDER BY rn, md5(gid)",
            {**snap, "ur": value}, COHORT_SIZES[cohort])

    linz_where = "AND l.rca_name = 'Land Information New Zealand'"
    out["linz_rca"] = _sample(
        f"SELECT gid FROM ({_base(linz_where)}) b ORDER BY md5(gid)",
        snap, COHORT_SIZES["linz_rca"])

    # Ramps are not flagged in AMDS for unnamed features - the ramp attributes
    # live on the route-name record, which is exactly what these features lack.
    # RAMM knows which carriageways are ramps, so the cohort is defined by
    # lying along one.
    ramp_where = """
       AND EXISTS (SELECT 1 FROM ext_road_names e
                    WHERE e.source = 'nzta_ramm_carriageway'
                      AND coalesce(e.extra->>'shRampType', '') <> ''
                      AND ST_DWithin(e.geom_2193, l.geom_2193, 20))
    """
    out["ramp"] = _sample(
        f"SELECT gid FROM ({_base(ramp_where)}) b ORDER BY md5(gid)",
        snap, COHORT_SIZES["ramp"])

    out["long"] = _sample(
        f"SELECT gid FROM ({_base()}) b ORDER BY group_len DESC",
        snap, COHORT_SIZES["long"])
    return out


# --------------------------------------------------------------------------
# running
# --------------------------------------------------------------------------

@dataclass
class Reviewed:
    gid: str
    cohort: str
    outcome: namematch.MatchOutcome
    corroboration: str          # agree | disagree | single_source | none
    linz_name: str | None
    nzta_name: str | None
    neighbour: str = "no_named_neighbour"   # agree | disagree | ...


_NEIGHBOUR_SQL = """
-- The AMDS names of source features that physically connect to each target.
-- A third check, and the only one drawn from the project's own data: if the
-- road continues into a feature AMDS already calls "Ballance Valley Road",
-- an external source calling this piece the same thing is corroborated by
-- something neither external source can see.
WITH target AS (
    SELECT DISTINCT closure_group_id AS gid, source_node AS node
      FROM links WHERE snapshot_id = %(snap)s AND closure_group_id = ANY(%(gids)s)
    UNION
    SELECT DISTINCT closure_group_id, target_node
      FROM links WHERE snapshot_id = %(snap)s AND closure_group_id = ANY(%(gids)s)
)
SELECT t.gid, n.native_name_key AS key, n.display_name AS name
  FROM target t
  JOIN links l
    ON l.snapshot_id = %(snap)s
   AND (l.source_node = t.node OR l.target_node = t.node)
   AND l.closure_group_id <> t.gid
  JOIN link_names n
    ON n.snapshot_id = l.snapshot_id
   AND n.closure_group_id = l.closure_group_id
 WHERE n.name_status = 'amds_named'
   AND n.native_name_key IS NOT NULL
 GROUP BY 1, 2, 3
"""


def neighbour_names(snapshot_id: str, gids: Sequence[str]) -> dict[str, set[str]]:
    """Search keys of the AMDS names on features connecting to each target."""
    if not gids:
        return {}
    out: dict[str, set[str]] = defaultdict(set)
    for r in db.query(_NEIGHBOUR_SQL, {"snap": snapshot_id, "gids": list(gids)}):
        key = search_key(r["name"])
        if key:
            out[r["gid"]].add(key)
    return out


def _neighbour_verdict(outcome: namematch.MatchOutcome,
                       keys: set[str]) -> str:
    if not keys:
        return "no_named_neighbour"
    if outcome.name is None:
        return "target_has_no_name"
    key = search_key(outcome.name)
    if key is None:
        return "target_has_no_name"
    return "agree" if key in keys else "no_match"


def _corroborate(outcome: namematch.MatchOutcome) -> tuple[str, str | None, str | None]:
    """Do the two independently maintained name sources say the same thing?

    Compared like with like, on the same rule the matcher uses: a street name
    against a street name, a designation against a designation. "GREAT SOUTH
    ROAD" and "State Highway 1" are the same road described two ways, and
    counting that as a disagreement would measure the comparison rather than
    the match. Two different designations - State Highway 1 against State
    Highway 59 - is a real disagreement and stays one.

    Only each source's best candidate is considered, and only when its geometry
    would stand on its own. A weak candidate agreeing by accident is not
    corroboration.
    """
    prefer_designation = bool(outcome.name
                              and namematch.is_designation(outcome.name))
    best: dict[str, namematch.Scored] = {}
    for s in outcome.candidates:
        if s.source not in (SOURCE_NZTA_STREET_NAMES, SOURCE_LINZ_ROAD_SECTIONS):
            continue
        if s.source in best or not namematch._geometry_is_convincing(s):
            continue
        if s.name_key and s.is_designation_name != prefer_designation:
            continue          # different kind of label: not the comparison
        best[s.source] = s

    linz = best.get(SOURCE_LINZ_ROAD_SECTIONS)
    nzta = best.get(SOURCE_NZTA_STREET_NAMES)
    linz_name = linz.name if linz else None
    nzta_name = nzta.name if nzta else None

    if linz is None and nzta is None:
        return "none", linz_name, nzta_name
    if linz is None or nzta is None:
        return "single_source", linz_name, nzta_name
    # A name on one side and an explicit "unnamed" on the other IS a
    # disagreement, and one worth seeing.
    lk, nk = search_key(linz_name), search_key(nzta_name)
    if lk is None and nk is None:
        return "agree", linz_name, nzta_name
    return ("agree" if lk == nk else "disagree"), linz_name, nzta_name


def run(snapshot_id: str, *, batch: int = 400,
        out_dir: Path | None = None) -> dict[str, Any]:
    groups = cohorts(snapshot_id)
    for name, gids in groups.items():
        print(f"  cohort {name:<10} {len(gids):>6,}")

    # A feature can qualify for more than one cohort. It is scored once and
    # reported under every cohort it belongs to, so cohort totals may sum to
    # more than the number of features scored - stated rather than hidden by
    # silently dropping the duplicate.
    membership: dict[str, list[str]] = defaultdict(list)
    for name, gids in groups.items():
        for g in gids:
            membership[g].append(name)
    all_gids = sorted(membership)
    print(f"  {len(all_gids):,} distinct source features to score")

    ramp_gids = set(groups["ramp"])
    reviewed: list[Reviewed] = []
    outcomes: list[namematch.MatchOutcome] = []

    for i in range(0, len(all_gids), batch):
        chunk = all_gids[i:i + batch]
        scored = namematch.score_candidates(snapshot_id, chunk)
        neighbours = neighbour_names(snapshot_id, chunk)
        for gid in chunk:
            outcome = namematch.classify(gid, scored.get(gid, []),
                                         is_ramp=gid in ramp_gids)
            outcomes.append(outcome)
            corr, linz_name, nzta_name = _corroborate(outcome)
            verdict = _neighbour_verdict(outcome, neighbours.get(gid, set()))
            for cohort in membership[gid]:
                reviewed.append(Reviewed(gid, cohort, outcome, corr,
                                         linz_name, nzta_name, verdict))
        print(f"\r  scored {min(i + batch, len(all_gids)):,}/{len(all_gids):,}",
              end="", flush=True)
    print()

    written = namematch.persist_candidates(snapshot_id, outcomes)
    print(f"  {written:,} candidate rows retained")

    report = summarise(reviewed)
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        _write_review_files(out_dir, reviewed, report)
    return report


def summarise(reviewed: Sequence[Reviewed]) -> dict[str, Any]:
    """Confidence mix per cohort, and measured precision on the HIGH class."""
    by_cohort: dict[str, Counter] = defaultdict(Counter)
    corr_by_conf: dict[str, Counter] = defaultdict(Counter)
    nbr_by_conf: dict[str, Counter] = defaultdict(Counter)
    reasons: Counter = Counter()
    seen: set[str] = set()
    single_source_high = Counter()

    for r in reviewed:
        by_cohort[r.cohort][r.outcome.confidence] += 1
        if r.gid not in seen:
            seen.add(r.gid)
            corr_by_conf[r.outcome.confidence][r.corroboration] += 1
            nbr_by_conf[r.outcome.confidence][r.neighbour] += 1
            for reason in r.outcome.reasons:
                reasons[reason] += 1
            if r.outcome.confidence == "HIGH" and r.corroboration == "single_source":
                single_source_high[r.neighbour] += 1

    high = corr_by_conf.get("HIGH", Counter())
    testable = high["agree"] + high["disagree"]
    precision = (high["agree"] / testable) if testable else None

    # The connecting-road test carries information in ONE direction only.
    # A road meeting a differently-named road is the ordinary case - Queen
    # Street meets Victoria Street - so "no_match" is not evidence of an error
    # and the ratio of agree to no_match is not a precision figure. Only the
    # positive count is reported, as corroboration.
    hn = nbr_by_conf.get("HIGH", Counter())
    ss_high = sum(v for k, v in corr_by_conf.get("HIGH", Counter()).items()
                  if k == "single_source")

    return {
        "features_scored": len(seen),
        "by_cohort": {k: dict(v) for k, v in sorted(by_cohort.items())},
        "corroboration_by_confidence": {k: dict(v)
                                        for k, v in sorted(corr_by_conf.items())},
        "neighbour_outcome_by_confidence": {k: dict(v)
                                            for k, v in sorted(nbr_by_conf.items())},
        # The measured precision figure: of the HIGH matches where both
        # independently maintained sources hold a comparable name, how often
        # they are the same name.
        "high_two_source_testable": testable,
        "high_two_source_agreement": precision,
        # Positive corroboration only, NOT a precision denominator.
        "high_neighbour_corroborated": hn["agree"],
        "high_single_source": ss_high,
        "high_single_source_neighbour_corroborated": single_source_high["agree"],
        "reasons": dict(reasons.most_common()),
    }


def _write_review_files(out_dir: Path, reviewed: Sequence[Reviewed],
                        report: dict[str, Any]) -> None:
    """Everything a reviewer needs, including the cases that failed."""
    (out_dir / "summary.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8")

    seen: set[str] = set()
    with (out_dir / "matches.jsonl").open("w", encoding="utf-8") as fh:
        for r in reviewed:
            if r.gid in seen:
                continue
            seen.add(r.gid)
            o = r.outcome
            fh.write(json.dumps({
                "gid": r.gid,
                "cohorts": [x.cohort for x in reviewed if x.gid == r.gid],
                "confidence": o.confidence,
                "name": namematch.normalise_external_name(o.name),
                "source": o.source,
                "feature_id": o.feature_id,
                "officially_unnamed": o.officially_unnamed,
                "rival_name": o.rival_name,
                "margin": o.margin,
                "corroboration": r.corroboration,
                "linz_name": r.linz_name,
                "nzta_name": r.nzta_name,
                "neighbour": r.neighbour,
                "reasons": list(o.reasons),
                "evidence": o.evidence,
            }, ensure_ascii=False, default=str) + "\n")

    reported: set[str] = set()
    disagreements = [r for r in reviewed
                     if r.corroboration == "disagree"
                     and r.outcome.confidence in ("HIGH", "MEDIUM")
                     and not (r.gid in reported or reported.add(r.gid))]
    with (out_dir / "disagreements.jsonl").open("w", encoding="utf-8") as fh:
        for r in disagreements:
            fh.write(json.dumps({
                "gid": r.gid, "cohort": r.cohort,
                "confidence": r.outcome.confidence,
                "linz_name": r.linz_name, "nzta_name": r.nzta_name,
                "chosen": r.outcome.name, "source": r.outcome.source,
                "evidence": r.outcome.evidence,
            }, ensure_ascii=False, default=str) + "\n")
