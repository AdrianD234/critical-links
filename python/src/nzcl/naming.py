"""Which name a road link should display, and on whose authority.

Pure logic: no database, no network, no clock of its own. Everything that
decides a name is an argument, so the whole thing is unit-testable and gives
the same answer on every run — which is the point, because the previous
implementation did not.

Four defects in the previous ingest are corrected here, each one measured
against the real tables before the rule was written (see
docs/audits/road-name-native-fixes.md):

1. It displayed ``routeNameFullASCII``. That column is not a transliteration of
   ``routeNameFull`` - it is separately maintained and sometimes stale. 628
   records have a name in ``routeNameFull`` and nothing in the ASCII column, and
   a handful carry an unrelated string: one record reads "Kotare Lane" in
   ``routeNameFull`` and "SH 1N/458 RAMP (SH) #4 OFF" in the ASCII column, while
   its structured components (nameBody1 "Kotare", nameType "Lane") agree with
   the former. Display uses ``routeNameFull``; the search key is folded from it
   here rather than taken from the published column.

2. It ignored ``status`` and the effective dates. Nine route names have already
   expired and four are retired.

3. Its primary selection was whichever join row happened to arrive first.
   18,552 links have candidate names but no ``isPrimary`` flag at all, and 194
   carry more than one primary - 168 of those with genuinely different names.
   Selection here is a total order, so it is reproducible.

4. It kept nothing but the winning string. Alternates, route-name identifiers,
   effective dates and the state-highway designation are all retained.

One rule is a product judgement rather than a data fix. AMDS route group 1
("Roadway State Highway") does not hold street names: its values look like
"SH 1S/774" and "SH 2/883 INCREASING", which encode a route and a reference
station. Group 6 ("Roadway Local") holds the street name - "Essex Street
(Sh 1)". Where a link has both, the street name is the display name and the
highway string becomes a separate designation. Where group 1 is all there is,
the display name is the normalised designation ("State Highway 1"), never the
raw reference-station string.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

# --------------------------------------------------------------------------
# AMDS coded domains, from the published layer metadata (table 11).
# --------------------------------------------------------------------------

STATUS_CURRENT = 1

ROUTE_GROUP_STATE_HIGHWAY = 1
ROUTE_GROUP_WATERWAY = 2
ROUTE_GROUP_BUSWAY = 3
ROUTE_GROUP_PATHWAY = 4
ROUTE_GROUP_RAILWAY = 5
ROUTE_GROUP_LOCAL = 6
ROUTE_GROUP_ROUTE = 7

#: AMDS uses far-past and far-future sentinels rather than nulls for
#: open-ended validity: 1900-01-01 and 9999-12-31 in epoch milliseconds.
EPOCH_MS_1900 = -2208988800000
EPOCH_MS_9999 = 253402214400000

#: The two ways AMDS writes a route reference at the start of a name.
#: "SH 2/483", "State Highway #63 (Rs 0)", and the RAMM-style section code
#: "002-0161-R1" / "01N-0475-R4", whose leading digits are the highway number.
#: The alpha suffix must be attached to the digits ("1S"), never separated, or
#: the pattern eats the first letter of the next word.
_ROUTE_REFERENCE = re.compile(
    r"^\s*(?:(?:SH|MSR|State\s+Highway)\s*#?\s*(?P<sh>\d+)(?![0-9])[A-Za-z]?(?![A-Za-z])"
    r"|(?P<code>\d{2,3})[A-Za-z]?-\d{3,4})",
    re.IGNORECASE,
)

#: A route reference followed by a reference station: "SH 1N/414", "SH 2/678".
#: The number after the slash locates a point along the highway.
_REFERENCE_STATION = re.compile(
    r"^\s*(?:SH|MSR|State\s+Highway)\s*#?\s*\d+(?![0-9])[A-Za-z]?\s*/\s*\d",
    re.IGNORECASE,
)

#: Residual words that still leave a string a pure route code rather than a
#: name. Taken from the published values, not invented: every one of these
#: appears after a route reference in AMDS table 11.
_CODE_WORDS = frozenset({
    "rs", "ramp", "on", "off", "increasing", "decreasing", "int", "rab",
    "revoke", "combined", "sh", "msr", "sp", "cy", "co", "bnd", "sth", "nth",
    "highway", "nzta", "north", "south", "east", "west",
    "w", "i", "d", "r", "c", "n", "s", "e",
})


# --------------------------------------------------------------------------
# name states
# --------------------------------------------------------------------------

#: A live AMDS roadway name. The strongest outcome and never overwritten.
AMDS_NAMED = "amds_named"
#: The only name AMDS holds is a state-highway route designation.
ROUTE_DESIGNATION_ONLY = "route_designation_only"
#: AMDS holds nothing; an external source supplied a high-confidence name.
EXTERNALLY_ENRICHED = "externally_enriched"
#: An authoritative source records that the road has no name.
OFFICIALLY_UNNAMED = "officially_unnamed"
#: Sources disagree, or AMDS holds two different roadway names for one link.
AMBIGUOUS_CONFLICT = "ambiguous_conflict"
#: Nothing found. Honest, and distinct from "officially unnamed".
UNRESOLVED = "unresolved"

NAME_STATES = (
    AMDS_NAMED, ROUTE_DESIGNATION_ONLY, EXTERNALLY_ENRICHED,
    OFFICIALLY_UNNAMED, AMBIGUOUS_CONFLICT, UNRESOLVED,
)

SOURCE_AMDS = "amds_routename"
SOURCE_NZTA_STREET_NAMES = "nzta_street_names"
SOURCE_LINZ_ROAD_SECTIONS = "linz_road_sections"
SOURCE_NZTA_RAMM = "nzta_ramm_carriageway"


# --------------------------------------------------------------------------
# text
# --------------------------------------------------------------------------

def fold_ascii(name: str | None) -> str | None:
    """Search-normalisation key: macrons folded, case and spacing normalised.

    Derived from the display name rather than read from AMDS's own ASCII
    column, which cannot be trusted to describe the same road. Display never
    uses this - "Mangamōteo Street" must stay spelled that way.
    """
    if not name:
        return None
    stripped = "".join(
        ch for ch in unicodedata.normalize("NFKD", name)
        if not unicodedata.combining(ch)
    )
    collapsed = re.sub(r"\s+", " ", stripped).strip()
    return collapsed or None


def search_key(name: str | None) -> str | None:
    """Aggressive key for comparing two names for equality across sources.

    Case-folded, punctuation dropped, parenthetical qualifiers removed, and
    common road-type words expanded, so "Essex Street (Sh 1)" and "ESSEX ST"
    compare equal. Used for agreement tests, never for display.
    """
    folded = fold_ascii(name)
    if not folded:
        return None
    folded = re.sub(r"\([^)]*\)", " ", folded)
    folded = folded.casefold()
    raw = re.findall(r"[a-z0-9]+", folded)
    words: list[str] = []
    for i, w in enumerate(raw):
        # "St" is Street at the end of a name and Saint at the start of one.
        # Expanding it blindly makes "St Andrews Road" and "Saint Andrews Road"
        # look like different roads, which is the opposite of the point.
        if w == "st":
            words.append("saint" if i == 0 and len(raw) > 1 else "street")
            continue
        words.append(_ROAD_TYPE_EXPANSION.get(w, w))
    key = " ".join(words).strip()
    return key or None


#: Abbreviations that appear in one source and not another. Expanded to the
#: long form on both sides so the comparison is symmetric.
_ROAD_TYPE_EXPANSION = {
    "rd": "road", "ave": "avenue", "av": "avenue",
    "dr": "drive", "cres": "crescent", "cr": "crescent", "pl": "place",
    "tce": "terrace", "ter": "terrace", "gr": "grove", "grv": "grove",
    "ln": "lane", "cl": "close", "ct": "court", "hwy": "highway",
    "pde": "parade", "sq": "square", "esp": "esplanade", "blvd": "boulevard",
    "bvd": "boulevard", "cway": "causeway", "mwy": "motorway",
    "rab": "roundabout", "ww": "walkway", "sh": "state highway",
    "n": "north", "s": "south", "e": "east", "w": "west",
}


def starts_with_route_reference(name: str | None) -> bool:
    """True when the string opens with a highway number or a section code."""
    return bool(name and _ROUTE_REFERENCE.match(name))


def parse_route_number(name: str | None) -> int | None:
    """The highway number a route reference carries, if it carries one.

    "SH 16/47" and "016-0047-R1" both mean State Highway 16. Reading the number
    out of the string is what lets a link whose only name is a reference-station
    code still be labelled with the road it is part of.
    """
    if not name:
        return None
    m = _ROUTE_REFERENCE.match(name)
    if not m:
        return None
    raw = m.group("sh") or m.group("code")
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return n or None


def is_designation(name: str | None, group: int | None = None) -> bool:
    """True when the string is a route code rather than the name of a road.

    A route reference at the front is not enough on its own: "Sh 67 Buller
    Road" and "SH 8 [BEAUMONT BRIDGE]" name real roads and structures. The test
    is whether anything survives the reference except code words - reference
    stations, ramp markers, direction words and the like.
    """
    if not name or not name.strip():
        return group == ROUTE_GROUP_STATE_HIGHWAY
    m = _ROUTE_REFERENCE.match(name)
    if not m:
        return False
    # A reference station - the "/414" in "SH 1N/414" - is an internal locator
    # along the route. Once one is present the string is a code, whatever
    # trails it: "SH 1N/414-BUS" and "SH 1N/1030 ROUNDABOUT (SH)" are both
    # locations on State Highway 1, not roads called BUS or ROUNDABOUT.
    if _REFERENCE_STATION.match(name):
        return True
    # Round brackets hold qualifiers - "(Rs 0)", "(11.65)". Square brackets
    # hold names - "SH 8 [BEAUMONT BRIDGE]" - so they are not stripped.
    residual = re.sub(r"\([^)]*\)", " ", name[m.end():])
    words = re.findall(r"[A-Za-z]+", residual)
    return all(w.lower() in _CODE_WORDS for w in words)


def format_designation(route_number: int | None,
                       route_alpha: str | None = None) -> str | None:
    """"State Highway 1" from a highway number.

    The alpha suffix distinguishes the two halves of State Highway 1 in the
    asset data ("1N", "1S"). It is an internal split, not part of the road's
    public name, so it is dropped from the display string and kept in
    provenance.
    """
    if route_number is None:
        return None
    return f"State Highway {int(route_number)}"


# --------------------------------------------------------------------------
# records
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class RouteName:
    """One row of AMDS table 11, reduced to the fields that decide a name."""

    route_name_id: str
    name_full: str | None = None
    name_ascii_published: str | None = None
    group: int | None = None
    sub_group: int | None = None
    route_number: int | None = None
    route_alpha: str | None = None
    ramp_number: int | None = None
    ramp_type: int | None = None
    interchange_number: int | None = None
    locality_code: int | None = None
    direction: int | None = None
    status: int | None = None
    effective_from: int | None = None
    effective_to: int | None = None

    @staticmethod
    def from_attributes(a: dict[str, Any]) -> "RouteName":
        return RouteName(
            route_name_id=a["amdsIDRouteName"],
            name_full=(a.get("routeNameFull") or None),
            name_ascii_published=(a.get("routeNameFullASCII") or None),
            group=a.get("routeGroup"),
            sub_group=a.get("routeSubGroup"),
            route_number=a.get("routeNumber1"),
            route_alpha=a.get("routeAlpha1"),
            ramp_number=a.get("rampNumber"),
            ramp_type=a.get("rampType"),
            interchange_number=a.get("interchangeNumber"),
            locality_code=a.get("localityName"),
            direction=a.get("direction"),
            status=a.get("status"),
            effective_from=a.get("effectiveFrom"),
            effective_to=a.get("effectiveTo"),
        )

    @property
    def display_candidate(self) -> str | None:
        return (self.name_full or "").strip() or None

    def is_live(self, now_ms: int) -> bool:
        if self.status != STATUS_CURRENT:
            return False
        if self.effective_from is not None and self.effective_from > now_ms:
            return False
        if self.effective_to is not None and self.effective_to < now_ms:
            return False
        return True

    @property
    def is_ramp(self) -> bool:
        return self.ramp_type is not None or self.ramp_number is not None


@dataclass(frozen=True)
class Candidate:
    """A route name offered to one link, with the join's primary flag."""

    route_name: RouteName
    is_primary: bool

    @property
    def kind(self) -> str:
        """What sort of thing the string is.

        Four buckets rather than two, because "Queen Street" and "Sh 67 Buller
        Road" are both names but only one of them should win a tie, and a name
        that opens with a route reference is not evidence that two sources
        disagree about the road.
        """
        rn = self.route_name
        name = rn.display_candidate
        if is_designation(name, rn.group):
            return "designation"
        if rn.group == ROUTE_GROUP_PATHWAY:
            return "pathway"
        if starts_with_route_reference(name):
            return "route_prefixed"
        return "roadway"

    @property
    def order(self) -> tuple:
        """Total order over candidates. Lower sorts first.

        Every component is derived from published data and the last one is a
        stable identifier, so there is exactly one winner for any candidate set
        and it does not depend on download or iteration order.
        """
        rn = self.route_name
        return (
            KIND_RANK[self.kind],            # a street name beats a route code
            0 if self.is_primary else 1,     # then the publisher's own flag
            0 if rn.display_candidate else 1,
            _GROUP_RANK.get(rn.group, 9),
            rn.route_name_id,                # stable tie-break: a GUID
        )


KIND_RANK = {"roadway": 0, "route_prefixed": 1, "pathway": 2, "designation": 3}

#: Kinds that carry a name a person would recognise as the road's name.
NAMED_KINDS = ("roadway", "route_prefixed", "pathway")


#: Within a kind, prefer the group that most specifically describes a road.
_GROUP_RANK = {
    ROUTE_GROUP_LOCAL: 0,
    ROUTE_GROUP_BUSWAY: 1,
    ROUTE_GROUP_ROUTE: 2,
    ROUTE_GROUP_STATE_HIGHWAY: 3,
    ROUTE_GROUP_PATHWAY: 4,
    ROUTE_GROUP_RAILWAY: 5,
    ROUTE_GROUP_WATERWAY: 6,
}


@dataclass(frozen=True)
class NameSelection:
    """What to display for a link, and everything needed to justify it."""

    display_name: str | None
    name_status: str
    name_source: str | None
    source_field: str | None
    native_name: str | None = None
    native_name_key: str | None = None
    route_designation: str | None = None
    designation_raw: str | None = None
    #: The compact route number as AMDS writes it, "1N"/"1S"/"3". Kept
    #: separately from the display designation because search and the route
    #: chip in the interface have always used this form.
    route_number: str | None = None
    alternates: tuple[str, ...] = ()
    route_name_ids: tuple[str, ...] = ()
    primary_route_name_id: str | None = None
    effective_from: int | None = None
    effective_to: int | None = None
    conflict: bool = False
    is_ramp: bool = False
    locality_code: int | None = None
    notes: tuple[str, ...] = ()


def select_amds_name(candidates: Sequence[Candidate], *, now_ms: int) -> NameSelection:
    """Choose the AMDS name for one source feature.

    Returns UNRESOLVED rather than raising when there is nothing usable: a link
    with no name is a normal, reportable outcome, not an error.
    """
    live = sorted((c for c in candidates if c.route_name.is_live(now_ms)),
                  key=lambda c: c.order)
    notes: list[str] = []
    if candidates and not live:
        notes.append("ALL_AMDS_NAMES_EXPIRED_OR_RETIRED")
    if not live:
        return NameSelection(
            display_name=None, name_status=UNRESOLVED, name_source=None,
            source_field=None, notes=tuple(notes),
        )

    by_kind = {k: [c for c in live if c.kind == k] for k in KIND_RANK}
    designations = by_kind["designation"]

    # A state-highway string is not a competing name for a street; it is the
    # route the street carries. Keep both, and never let one hide the other.
    #
    # The number is read from the structured routeNumber1 where AMDS supplies
    # it and parsed out of the string where it does not - 337 group-6 records
    # carry only a section code like "01N-0475-R4", whose leading digits are
    # the highway number.
    designation = None
    designation_raw = None
    route_number = None
    for c in designations:
        rn = c.route_name
        n = rn.route_number if rn.route_number is not None else parse_route_number(
            rn.display_candidate)
        if designation is None and n is not None:
            designation = format_designation(n)
            route_number = f"{n}{rn.route_alpha or ''}"
        designation_raw = designation_raw or rn.display_candidate

    named = next((by_kind[k] for k in NAMED_KINDS if by_kind[k]), [])
    if named:
        winner = named[0]
        rn = winner.route_name
        display = rn.display_candidate
        # "Sh 67 Buller Road" names a road AND states its route. Report both.
        if designation is None:
            designation = format_designation(parse_route_number(display))
        # Two different names of the SAME kind is a real conflict in the source.
        # It is surfaced, not resolved away. Comparing across kinds would report
        # "Queen Street" against "SH 2/883" as a disagreement, which it is not.
        distinct = {search_key(c.route_name.display_candidate) for c in named}
        distinct.discard(None)
        conflict = len(distinct) > 1
        if conflict:
            notes.append("MULTIPLE_AMDS_ROADWAY_NAMES")
        if sum(1 for c in named if c.is_primary) > 1:
            notes.append("MULTIPLE_PRIMARY_FLAGS")
        if not any(c.is_primary for c in live):
            notes.append("NO_PRIMARY_FLAG_IN_SOURCE")
        alternates = tuple(dict.fromkeys(
            c.route_name.display_candidate for c in live
            if c.route_name.display_candidate
            and c.route_name.display_candidate != display
        ))
        return NameSelection(
            display_name=display,
            name_status=AMBIGUOUS_CONFLICT if conflict else AMDS_NAMED,
            name_source=SOURCE_AMDS,
            source_field="routeNameFull",
            native_name=display,
            native_name_key=fold_ascii(display),
            route_designation=designation,
            designation_raw=designation_raw,
            route_number=route_number,
            alternates=alternates,
            route_name_ids=tuple(c.route_name.route_name_id for c in live),
            primary_route_name_id=rn.route_name_id,
            effective_from=rn.effective_from,
            effective_to=rn.effective_to,
            conflict=conflict,
            is_ramp=any(c.route_name.is_ramp for c in live),
            locality_code=rn.locality_code,
            notes=tuple(notes),
        )

    # Designations only. The display name is the normalised route, because
    # "SH 1S/774" names a reference station, not a road.
    winner = live[0]
    rn = winner.route_name
    display = designation or rn.display_candidate
    if designation is None:
        notes.append("DESIGNATION_WITHOUT_ROUTE_NUMBER")
    alternates = tuple(dict.fromkeys(
        c.route_name.display_candidate for c in live
        if c.route_name.display_candidate and c.route_name.display_candidate != display
    ))
    return NameSelection(
        display_name=display,
        name_status=ROUTE_DESIGNATION_ONLY,
        name_source=SOURCE_AMDS,
        source_field="routeNumber1" if designation else "routeNameFull",
        native_name=None,
        native_name_key=fold_ascii(display),
        route_designation=designation,
        designation_raw=designation_raw or rn.display_candidate,
        route_number=route_number,
        alternates=alternates,
        route_name_ids=tuple(c.route_name.route_name_id for c in live),
        primary_route_name_id=rn.route_name_id,
        effective_from=rn.effective_from,
        effective_to=rn.effective_to,
        conflict=False,
        is_ramp=any(c.route_name.is_ramp for c in live),
        locality_code=rn.locality_code,
        notes=tuple(notes),
    )


def build_candidates(
    join_rows: Iterable[dict[str, Any]],
    route_name_rows: Iterable[dict[str, Any]],
) -> dict[str, list[Candidate]]:
    """Group table 13 join rows against table 11 detail, by source feature."""
    detail = {}
    for a in route_name_rows:
        rid = a.get("amdsIDRouteName")
        if rid:
            detail[rid] = RouteName.from_attributes(a)
    out: dict[str, list[Candidate]] = {}
    for j in join_rows:
        link = j.get("amdsIDNetworkModel")
        rn = detail.get(j.get("amdsIDRouteName"))
        if not link or rn is None:
            continue
        out.setdefault(link, []).append(
            Candidate(route_name=rn, is_primary=j.get("isPrimary") == 1)
        )
    return out


# --------------------------------------------------------------------------
# the one authoritative label
# --------------------------------------------------------------------------
"""
`display_label` replaces four separate places that each decided, differently,
what to put in the road-name position. The map chip collapsed `unresolved`,
`ambiguous_conflict` and a licence-withheld name to the single string
"No name"; the inspector said "Name not recorded"; the search list said
something else again. A reader could not tell the four states apart, and three
of them are actionable.

The rule is a strict priority. The first line that has something to say wins,
and the caller is told WHICH line won so the interface can style provenance
without re-deriving the decision:

  1  road_name              a canonical name from a licensed source
  2  route_designation      "State Highway 1" - the route this road carries
  3  officially_unnamed     an authority records that it HAS no name
  4  withheld               a name is known and may not be shown yet
  5  contextual             what can be said from class, locality and RCA
  6  identifier             a short stable id, as secondary context only

Line 5 is the one that kills the generic "No name". A state highway near
Tokoroa with no matched name is still a state highway near Tokoroa, and saying
so is both true and more useful than saying nothing. Line 6 is never a headline
on its own - it is returned as `secondary`, for the caller to show beside a
contextual label rather than instead of one.

Pure: every input is an argument. No database, no clock.
"""


@dataclass(frozen=True)
class DisplayLabel:
    """The label, and enough provenance to render it honestly."""

    label: str
    #: road_name | route_designation | officially_unnamed | withheld |
    #: contextual | identifier
    kind: str
    #: Short stable identifier, offered as secondary context. Never the label
    #: unless nothing else exists at all.
    secondary: str | None = None
    #: Plain-English reason this label was chosen, for the provenance panel.
    basis: str = ""


#: Trailing words stripped from an RCA name to get something that reads in a
#: sentence. "Waitomo District Council" -> "Waitomo District".
_RCA_TRIM = (" Council", " Corporation", " Limited", " Ltd")

#: RCAs that are not a place and must not be rendered as one.
_NZTA_RCA_CODE = 1


def short_rca(rca_name: str | None) -> str | None:
    """"Waitomo District Council" -> "Waitomo District". Idempotent."""
    if not rca_name:
        return None
    out = rca_name.strip()
    for suffix in _RCA_TRIM:
        if out.endswith(suffix):
            out = out[: -len(suffix)].strip()
            break
    return out or None


def short_identifier(amds_id: str | None, link_id: int | None = None) -> str | None:
    """A short, stable handle for a link.

    AMDS ids are braced GUIDs with a "#n" child suffix. The full string is
    unreadable in an interface and the bare integer link id is not durable
    across snapshots, so this takes the leading GUID block and keeps the child
    suffix, which is the part that distinguishes siblings.
    """
    if amds_id:
        core = amds_id.strip().lstrip("{")
        part = ""
        if "#" in core:
            core, part = core.split("#", 1)
            part = f"#{part}"
        core = core.rstrip("}").split("-")[0]
        if core:
            return f"{core}{part}"
    return None if link_id is None else f"link {link_id}"


def display_label(
    *,
    road_name: str | None = None,
    route_designation: str | None = None,
    name_status: str | None = None,
    withheld_source: str | None = None,
    rca_code: int | None = None,
    rca_name: str | None = None,
    locality: str | None = None,
    amds_id: str | None = None,
    link_id: int | None = None,
) -> DisplayLabel:
    """The single label the interface shows. Never "No name", never empty."""
    secondary = short_identifier(amds_id, link_id)
    rca_short = short_rca(rca_name)
    is_state_highway = rca_code == _NZTA_RCA_CODE

    if road_name and road_name.strip():
        return DisplayLabel(road_name.strip(), "road_name", secondary,
                            "a canonical road name from a licensed source")

    if route_designation and route_designation.strip():
        return DisplayLabel(
            route_designation.strip(), "route_designation", secondary,
            "no street name is recorded; this is the route the road carries")

    if name_status == "officially_unnamed":
        return DisplayLabel(
            "Unnamed road", "officially_unnamed", secondary,
            "an authoritative source records that this road has no name")

    if name_status == "ambiguous_conflict":
        # Sources hold more than one name and none was chosen. Saying so is
        # both true and actionable; falling through to a contextual label would
        # hide that names ARE known, which is a different problem to have.
        return DisplayLabel(
            "Name disputed", "conflict", secondary,
            "more than one source holds a name for this road and they "
            "disagree; neither has been chosen over the other")

    if withheld_source:
        # Name the authority, not the source system: "Name withheld - LINZ Data
        # Service" tells a reader nothing about the road, whereas the RCA does.
        who = rca_short or "this road controlling authority"
        if is_state_highway:
            who = "NZTA Waka Kotahi"
        return DisplayLabel(
            f"Name withheld - {who}", "withheld", secondary,
            "a name is recorded for this road but its source's licence has "
            "not been confirmed for display")

    # --- contextual -------------------------------------------------------
    if is_state_highway:
        label = (f"State-highway section near {locality}" if locality
                 else "State-highway section")
    elif locality:
        label = f"Local-road section near {locality}"
    elif rca_short:
        label = f"Road section managed by {rca_short}"
    else:
        # Nothing at all to say. The identifier becomes the label, which is the
        # only case where line 6 is a headline.
        return DisplayLabel(
            secondary or "Unidentified road section", "identifier", None,
            "no name, route, locality or managing authority is recorded")

    return DisplayLabel(label, "contextual", secondary,
                        "no name is recorded; this describes the road from its "
                        "classification, locality and managing authority")
