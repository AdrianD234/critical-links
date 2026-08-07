# Fixing the AMDS side first

Dated 7 August 2026. Snapshot `amds-national-2026-07-28-5b359d84`.

Before reaching for an external source, the project's own source was read
properly. This records what was wrong, what each fix was worth, and the proof
that none of it moved a routing result.

Baseline measurements are in
[`road-name-coverage-before.md`](road-name-coverage-before.md). Source
descriptions are in [`../ROAD_NAME_SOURCES.md`](../ROAD_NAME_SOURCES.md).

---

## What was read

Tables 11 (`RouteName`) and 13 (the `NetworkModel` ↔ `RouteName` join), every
field, on 7 August 2026.

| | |
| --- | --- |
| Table 11 | 92,065 route names |
| Table 13 | 98,266 join rows, 96,862 distinct source features |
| Orphan join rows | **0** |
| Candidates per feature | 95,539 have one, 1,243 have two, 79 have three, 1 has four |

The previous ingest requested six columns of table 11. It needed sixteen.

---

## The four defects, measured

### 1. It displayed `routeNameFullASCII`

That column is not a transliteration of `routeNameFull`. It is separately
maintained, and it disagrees.

| | Records |
| --- | --- |
| `routeNameFull` blank | 10 |
| **`routeNameFullASCII` blank** | **638** |
| Both present and different | 48 |
| Macronated (`isMacronated = 1`) | 14 |

Only 14 records are macronated, so the ASCII column is not mostly doing
transliteration work. It is doing something else. Three examples:

```
routeNameFull  "Kotare Lane"                      nameBody1 "Kotare"   nameType Lane
routeNameFullASCII  "SH 1N/458 RAMP (SH) #4 OFF"
```

```
routeNameFull  "Kotlowski Road"                   nameBody1 "Kotlowski"
routeNameFullASCII  "SH 1N/458 RAMP (SH) #6 ON"
```

```
routeNameFull  "MOOHAN ST Roundabout (WRIGHT ST)"
routeNameFullASCII  "MOOHAN ST Rab (WRIGHT ST)"
```

In the first two the structured name components — `nameBody1`, `nameType` —
agree with `routeNameFull` and not with the ASCII column. The third shows the
ASCII column is an *abbreviated* form, not a folded one.

**Fixed:** display reads `routeNameFull`. The search key is folded from it here
(NFKD, combining marks dropped) rather than taken from the published column, so
"Mangamōteo Street" displays with its macron and still matches a search for
"Mangamoteo".

### 2. It ignored `status` and the effective dates

| `status` | Records |
| --- | --- |
| 1 Current | 92,020 |
| 4 Proposed/Indicative | 17 |
| 2 Retired | 4 |
| 3 Future | 1 |
| *null* | 23 |

Nine records have `status = 1` but an `effectiveTo` already in the past —
"Conlon Street" expired 4 June 2025, "ROBBS BUSH ROAD" 4 December 2024.

AMDS uses far-past and far-future sentinels rather than nulls: `effectiveFrom`
bottoms out at 1900-01-01 and `effectiveTo` tops out at 9999-12-31. Both are
treated as open-ended.

**Fixed:** 22 source features had every candidate name filtered out and are now
flagged `ALL_AMDS_NAMES_EXPIRED_OR_RETIRED` rather than shown a dead name.

### 3. Primary selection was whichever join row arrived first

| | Source features |
| --- | --- |
| Live candidates but **no** `isPrimary` flag anywhere | **18,552** |
| More than one `isPrimary` flag | 194 |
| …of which the primaries carry different names | 168 |

That is 19.4% of named features where the previous rule had nothing to go on
and fell back to iteration order. Two ingests of the same data could disagree.

**Fixed:** a total order — kind, then the publisher's primary flag, then route
group, then the route-name GUID as a stable tie-break. `select_amds_name` is a
pure function and the ordering test asserts that reversing the input does not
change the answer.

### 4. Nothing but the winning string was kept

**Fixed:** `link_names` retains the native name, the folded search key, every
alternate, all contributing route-name identifiers, the primary identifier, the
effective window, the locality code, the ramp flag, the raw designation string
and the notes explaining any of it.

---

## One judgement, not a defect

AMDS route group 1 is "Roadway State Highway", and it does not hold street
names. Its values look like this:

```
SH 1S/774        SH 2/883 INCREASING        SH 1N/491 RAMP (SH) #1 ON
```

Those encode a route and a reference station. Group 6, "Roadway Local", holds
the street name for the same link — `Essex Street (Sh 1)`. **1,095 source
features carry both.** The previous rule could return either.

Group is not a sufficient test on its own: 652 group-6 records are also written
as route codes, and 337 more carry only a RAMM-style section code like
`01N-0475-R4`, whose leading digits are the highway number.

So the test is on the string, not the group. After a leading route reference,
if nothing survives except code words — `RS`, `RAMP`, `ON`, `OFF`,
`INCREASING`, `DECREASING`, `REVOKE` and the like — it is a designation. If a
real word survives, it is a name:

| String | Verdict |
| --- | --- |
| `SH 16/47` | designation → **State Highway 16** |
| `State Highway #63 (Rs 0)` | designation → **State Highway 63** |
| `01N-0475-R4` | designation → **State Highway 1** |
| `Sh 67 Buller Road` | a name, kept as-is |
| `SH 8 [BEAUMONT BRIDGE]` | a name, kept as-is |
| `State Highway 3 Interchange 279 Roundabout` | a name, kept as-is |

A street name always outranks a designation, and the designation is reported
alongside rather than discarded. A link showing "Essex Street (Sh 1)" also
carries `route_designation = State Highway 1`.

---

## What changed on the map

| | Graph links |
| --- | --- |
| Named before | 139,980 |
| Named after | **140,036** |
| Gained a name | 56 |
| Lost a name (expired or retired) | 26 |
| **Display text changed** | **13,633** |

The count barely moves. That is the honest headline: **the native fixes are a
correctness and readability change, not a coverage change.** 62.7% of links
still have no name, and only an external source will change that.

What did change is what those links say:

| Before | After | Links |
| --- | --- | --- |
| `State Highway 6 Highway (NZTA)` | `State Highway 6` | 171 |
| `SH 16/47` | `State Highway 16` | 64 |
| `SH 14/0` | `State Highway 14` | 55 |
| `SH 1N/336` | `State Highway 1` | 40 |
| `SH 2/873` | `Opaki Road (SH2)` | 27 |
| `SH 1N/65` | `Te Hapua Road (Sh1)` | 26 |

12,137 links previously labelled with a reference-station code now name their
highway. 1,578 previously labelled with a highway code now show the street name
that was in the source all along.

### Name states after the native pass

| State | Source features | Graph links |
| --- | --- | --- |
| `unresolved` | 180,731 (66.3%) | 235,642 (62.7%) |
| `amds_named` | 88,002 (32.3%) | 127,127 (33.8%) |
| `route_designation_only` | 3,619 (1.3%) | 12,695 (3.4%) |
| `ambiguous_conflict` | 74 (0.0%) | 232 (0.1%) |

A further rule landed after the first pass and is included above: once a
**reference station** appears - the "/414" in `SH 1N/414` - the string is a code
whatever trails it. `SH 1N/414-BUS` and `SH 1N/1030 ROUNDABOUT (SH)` are
locations on State Highway 1, not roads called BUS or ROUNDABOUT. It was found
by a browser test asserting that no displayed name is reference-station-shaped,
not by reading the data again.

The 74 conflicts are surfaced, not resolved. They are features where AMDS holds
two different roadway names of the same kind — `Mount Nicholas Road (Te Anau
Ward)` against `Von Road`, both flagged primary. A street name sitting beside
its own highway designation is *not* counted as a conflict, and neither is
`Terrace Road` beside `Terrace Road (Gdc Rd Te Tipua Ward)`.

### Notes recorded

| Note | Features |
| --- | --- |
| `NO_PRIMARY_FLAG_IN_SOURCE` | 15,175 |
| `MULTIPLE_AMDS_ROADWAY_NAMES` | 84 |
| `MULTIPLE_PRIMARY_FLAGS` | 55 |
| `ALL_AMDS_NAMES_EXPIRED_OR_RETIRED` | 22 |

---

## Nothing moved

Names live in `link_names`, keyed by source feature, and a view fans them out
to graph links. `links`, `nodes`, `arcs` and `arc_transitions` are not written
by any naming process.

That is checked rather than asserted. `nzcl-names verify` digests every table a
route search reads, and re-runs 41 real detours — evenly spaced by link id, plus
link 373604 from the reported screenshot — recording every number each produces.

Taken before the backfill and again after:

```
  arc_transitions    unchanged
  arcs               unchanged
  closure_groups     unchanged
  geometry           unchanged
  links              unchanged
  nodes              unchanged
  routing            unchanged

PASS: every topology and cost fingerprint is byte-identical
```

The fingerprints are committed at
[`topology-fingerprint-before.json`](topology-fingerprint-before.json).

---

## Residue

About 100 source features still display a string that is arguably a code:
`SH 1 E HATEPE 01N-0639` (7 links), `Sh 1 Rs 583 B Oamaru North` (4). They
survive the code-word test because they contain place names. They name their
highway, which is better than the reference-station strings they replaced, and
they are a candidate for external corroboration rather than a further rule.
