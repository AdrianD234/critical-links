# What V1 actually measures

Dated 7 August 2026. Snapshots `amds-national-2026-07-28-5b359d84` (375,696
links) and `amds-wellington-2026-07-27-6ef785ad` (36,397 links, the only
snapshot carrying a complete V1 batch in `detour_results`).

Three questions, three answers. **First:** V1's default closure scope removes
every graph link sharing a `closure_group_id` but labels the result with the
selected child's length, and nationally that gap is not marginal — 40.6% of
links belong to a multi-child group, 30.5% sit in a group whose total length is
at least twice their own, and summed across all 375,696 single-link scenarios
V1 would close 457,322 km of road while reporting 147,848 km. **Second:** the
endpoint measure returns DISCONNECTED on 87.9% of one-way state highway
results in Wellington, which confirms the repo's standing claim — but it also
returns DISCONNECTED on 44.2% of *two-way* results, so the one-way carriageway
is an aggravating factor, not the cause. **Third:** of 8,335 V1 "cut off"
results in Wellington, 1,829 (21.9%) name a selected link that is not an
undirected bridge and therefore cannot disconnect anything on its own. The
cause of those 1,829 splits two ways, and which one dominates depends on how
you count: over-closure accounts for 1,439 of them by result count, but
directed reachability accounts for 30,118 km of the 31,220 km of stranding V1
attributes to them.

---

## Q1 — Closure scope closes more road than it reports

Snapshot `amds-national-2026-07-28-5b359d84`.

V1's default scope closes the whole AMDS source feature. The interface shows
one selected segment and one length. Where a source feature was split into
several graph links — at intersections, at attribute changes, at the
directional split of a dual carriageway — the scenario simulated is larger than
the one described.

### Children per closure group

375,696 links across 272,426 groups. No link has a null `closure_group_id`.

```sql
WITH g AS (
  SELECT closure_group_id, count(*) AS n_children
  FROM links WHERE snapshot_id = 'amds-national-2026-07-28-5b359d84'
  GROUP BY 1
)
SELECT CASE WHEN n_children = 1 THEN '1'
            WHEN n_children = 2 THEN '2'
            WHEN n_children BETWEEN 3 AND 5 THEN '3-5'
            WHEN n_children BETWEEN 6 AND 10 THEN '6-10'
            ELSE '>10' END AS bucket,
       count(*) AS groups, sum(n_children) AS links
FROM g GROUP BY 1 ORDER BY min(n_children);
```

| Children | Groups | % of groups | Links | % of links |
| --- | ---: | ---: | ---: | ---: |
| 1 | 223,089 | 81.89% | 223,089 | 59.38% |
| 2 | 28,613 | 10.50% | 57,226 | 15.23% |
| 3-5 | 16,934 | 6.22% | 59,915 | 15.95% |
| 6-10 | 2,873 | 1.05% | 20,756 | 5.52% |
| >10 | 917 | 0.34% | 14,710 | 3.92% |

Groups are overwhelmingly single-child, but links are not: **40.62% of graph
links belong to a group with at least one sibling.** That is the population for
which V1's default answers a different question from the one asked. The group
percentage understates it because long, heavily-split source features contain
many links each.

### Ratio of group length to own length

For every graph link, the ratio of its closure group's total length to its own.
A ratio of 1.000 means the link is the whole source feature.

```sql
WITH g AS (
  SELECT closure_group_id, sum(length_m) AS group_len
  FROM links WHERE snapshot_id = 'amds-national-2026-07-28-5b359d84'
  GROUP BY 1
), r AS (
  SELECT g.group_len / NULLIF(l.length_m, 0) AS ratio
  FROM links l JOIN g USING (closure_group_id)
  WHERE l.snapshot_id = 'amds-national-2026-07-28-5b359d84'
)
SELECT percentile_cont(0.50) WITHIN GROUP (ORDER BY ratio) AS p50,
       percentile_cont(0.75) WITHIN GROUP (ORDER BY ratio) AS p75,
       percentile_cont(0.90) WITHIN GROUP (ORDER BY ratio) AS p90,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY ratio) AS p95,
       percentile_cont(0.99) WITHIN GROUP (ORDER BY ratio) AS p99,
       max(ratio) AS max_ratio,
       count(*) FILTER (WHERE ratio >= 2)  AS ge2,
       count(*) FILTER (WHERE ratio >= 5)  AS ge5,
       count(*) FILTER (WHERE ratio >= 10) AS ge10
FROM r;
```

| Percentile | Ratio | | Threshold | Links | % of 375,696 |
| --- | ---: | --- | --- | ---: | ---: |
| 50th | 1.000 | | ratio > 1 (any sibling) | 152,607 | 40.62% |
| 75th | 3.063 | | ratio >= 2 | 114,523 | 30.48% |
| 90th | 12.493 | | ratio >= 5 | 68,340 | 18.19% |
| 95th | 32.661 | | ratio >= 10 | 43,773 | 11.65% |
| 99th | 299.387 | | | | |
| max | 10,783,590.4 | | | | |

The maximum is an artefact of very short links: 2,642 links are under one
metre, 396 under 10 cm. Excluding all sub-metre links (373,054 remaining) the
distribution barely moves — median 1.000, 90th 11.506, 99th 165.389, max
15,053.3, ratio >= 2 at 30.03%, ratio >= 10 at 11.07%. The tail is not driven by
degenerate geometry; it is driven by long source features split into many
short graph links.

### Aggregate closed versus reported

```sql
WITH g AS (
  SELECT closure_group_id, sum(length_m) AS group_len
  FROM links WHERE snapshot_id = 'amds-national-2026-07-28-5b359d84'
  GROUP BY 1
)
SELECT sum(l.length_m) / 1000 AS selected_km, sum(g.group_len) / 1000 AS closed_km
FROM links l JOIN g USING (closure_group_id)
WHERE l.snapshot_id = 'amds-national-2026-07-28-5b359d84';
```

| | Kilometres |
| --- | ---: |
| Reported (selected child) | 147,848 |
| Actually closed (whole group) | 457,322 |
| Ratio | 3.093 |

This is the sum across all 375,696 single-link scenarios, not a union — each
scenario is independent and the same road is counted once per scenario that
touches it. Read as an expectation rather than a total: **the average V1
scenario closes 3.09 times the road length it reports.** For the 59.38% of
links that are their own whole source feature the ratio is exactly 1, so the
inflation is concentrated entirely in the other 40.62%.

---

## Q2 — DISCONNECTED is common everywhere, and near-total on one-way state highways

Snapshot `amds-wellington-2026-07-27-6ef785ad`, `closure_scope = 'physical'`,
37,059 result rows over 19,572 distinct links. Every row joins to `links`; no
orphans.

A link is one-way when `NOT (forward_allowed AND reverse_allowed)`. Every
one-way link in this snapshot is forward-only — there are no
`forward_allowed = false` rows — so one-way links carry a `forward` result and
no `reverse` result at all. The reverse direction on a one-way carriageway is
not reported as undefined; it is simply absent, so the rates below understate
how often the endpoint measure fails to produce an answer for one-way roads.

```sql
SELECT CASE WHEN (l.forward_allowed AND l.reverse_allowed) THEN 'two-way'
            ELSE 'one-way' END AS carriageway,
       CASE WHEN l.rca_code = 1 THEN 'state highway' ELSE 'local' END AS class,
       d.direction,
       count(*) AS results,
       count(*) FILTER (WHERE d.status = 'DISCONNECTED') AS disconnected
FROM detour_results d
JOIN links l ON l.snapshot_id = d.snapshot_id AND l.link_id = d.link_id
WHERE d.snapshot_id = 'amds-wellington-2026-07-27-6ef785ad'
  AND d.closure_scope = 'physical'
GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;
```

| Carriageway | Class | Forward | Reverse | Both | DISCONNECTED | Rate |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| one-way | local | 819 / 1,424 | — | 1,424 | 819 | 57.51% |
| one-way | state highway | 581 / 661 | — | 661 | 581 | **87.90%** |
| two-way | local | 7,700 / 17,365 | 7,637 / 17,365 | 34,730 | 15,337 | 44.16% |
| two-way | state highway | 59 / 122 | 49 / 122 | 244 | 108 | 44.26% |

Per-direction rates: one-way local 57.51% forward; one-way SH 87.90% forward;
two-way local 44.34% forward and 43.98% reverse; two-way SH 48.36% forward and
40.16% reverse. `SOURCE_DATA_ERROR` accounts for 59 rows (58 two-way local, 1
one-way local) and is left in the denominators.

**This partly contradicts the framing in the repo's own docs.** The claim that
the endpoint measure is routinely undefined on one-way carriageways holds, and
holds strongly for state highways: 87.90% against 44.26% for two-way state
highways, 43.6 points apart on the same road class. But the two-way baseline is
itself 44%. A measure that fails on nearly half of ordinary two-way suburban
streets is not failing *because of* one-way geometry. One-way geometry makes a
broken measure worse; it is not the defect.

### Coverage limitation

The batch covers 19,572 of 36,397 links (53.8%) and 17,835 of 29,053 closure
groups, and the covered set is not a random sample: 10.65% of covered links are
one-way against 3.58% of uncovered, and 4.00% are state highway against 6.42%
uncovered. Rates within each cell are valid; the overall mix is not a
Wellington-wide mix. Wellington is also urban-weighted — its one-way share
(7.39% of all links, 5.12% of state highways) is far above a rural or national
network. **These rates are Wellington-only and must not be read as national.**
There is no V1 batch for the national snapshot and this audit did not create
one.

---

## Q3 — One in five V1 "cut off" results names a link that cannot cut anything

Snapshot `amds-wellington-2026-07-27-6ef785ad`. A V1 cut-off result is a
`detour_results` row with `status = 'DISCONNECTED'` and
`isolation_link_count > 0`. Bridge status comes from `physical_access_links`
with `profile = 'car'`, `derivation_version = '1.0.0'`, where `is_bridge` is an
exact undirected-bridge flag validated against networkx.

Of 16,845 DISCONNECTED rows, 8,335 carry a positive isolation count and 8,510
carry zero. Every one of the 8,335 joins to a `physical_access_links` row.

```sql
SELECT count(*) AS cutoff_results,
       count(*) FILTER (WHERE p.is_bridge)          AS bridge,
       count(*) FILTER (WHERE p.is_bridge IS FALSE) AS not_bridge
FROM detour_results d
JOIN physical_access_links p
  ON p.snapshot_id = d.snapshot_id AND p.link_id = d.link_id
 AND p.profile = 'car' AND p.derivation_version = '1.0.0'
WHERE d.snapshot_id = 'amds-wellington-2026-07-27-6ef785ad'
  AND d.closure_scope = 'physical'
  AND d.status = 'DISCONNECTED'
  AND d.isolation_link_count > 0;
```

| | Results | Share |
| --- | ---: | ---: |
| V1 cut-off results | 8,335 | 100% |
| selected link is an undirected bridge | 6,506 | 78.06% |
| **selected link is not a bridge** | **1,829** | **21.94%** |

For those 1,829 results, removing the selected segment from the undirected car
graph disconnects nothing. Whatever V1 reported as stranded cannot be a
consequence of closing that segment.

### By class and carriageway

| Carriageway | Class | Cut-off results | Not a bridge | Share |
| --- | --- | ---: | ---: | ---: |
| one-way | local | 174 | 170 | 97.70% |
| one-way | state highway | 30 | 28 | 93.33% |
| two-way | local | 8,042 | 1,544 | 19.20% |
| two-way | state highway | 89 | 87 | **97.75%** |

By direction: 1,028 of 4,283 forward results (24.00%) and 801 of 4,052 reverse
results (19.77%).

State highways are the striking row. **Of 119 state highway cut-off results in
Wellington, 115 (96.6%) name a non-bridge link.** State highways are the most
redundant part of the network and the most heavily split into parallel and
directional children, so both failure modes concentrate there — which is also
where a user is most likely to test V1.

### Which cause dominates

Split the 1,829 non-bridge results by whether the selected link's closure group
has siblings. A multi-child group means over-closure is available as an
explanation: V1 removed sibling links too, and the combination may genuinely
cut. A single-child group rules that out entirely — one non-bridge link was
removed, nothing else, so the reported stranding can only have come from
*directed* reachability.

```sql
WITH gc AS (
  SELECT closure_group_id, count(*) AS n
  FROM links WHERE snapshot_id = 'amds-wellington-2026-07-27-6ef785ad'
  GROUP BY 1
)
SELECT gc.n = 1 AS single_child,
       count(*) AS results,
       sum(d.isolation_length_m) / 1000 AS isolation_km,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY d.isolation_length_m) AS median_m
FROM detour_results d
JOIN links l ON l.snapshot_id = d.snapshot_id AND l.link_id = d.link_id
JOIN gc ON gc.closure_group_id = l.closure_group_id
JOIN physical_access_links p
  ON p.snapshot_id = d.snapshot_id AND p.link_id = d.link_id
 AND p.profile = 'car' AND p.derivation_version = '1.0.0'
WHERE d.snapshot_id = 'amds-wellington-2026-07-27-6ef785ad'
  AND d.status = 'DISCONNECTED' AND d.isolation_link_count > 0
  AND NOT p.is_bridge
GROUP BY 1;
```

| Cause | Results | Share | Isolation attributed | Median per result | Results over 10 km |
| --- | ---: | ---: | ---: | ---: | ---: |
| Multi-child group (over-closure available) | 1,439 | 78.68% | 1,102 km | 157 m | 6 |
| Single-child group (directed reachability only) | 390 | 21.32% | **30,118 km** | **98,108 m** | 237 |
| Total non-bridge | 1,829 | 100% | 31,220 km | 231 m | 243 |

**The two causes dominate on different measures, and this needs stating
plainly rather than picking one.** By result count, over-closure dominates
almost four to one. By the magnitude of the claim V1 makes, directed
reachability dominates by twenty-seven to one: 390 results account for 96.5% of
the 31,220 km of stranding attributed to non-bridge links, with a median
stranding of 98.1 km per result and a maximum of 166.3 km over 1,787 links.

Those 390 are unambiguous. One link was removed, it is not a bridge, the
undirected graph stayed connected, and V1 still reported roughly a whole
component as cut off. That is a directed-reachability artefact: the measure
asked whether one endpoint could still reach the other along a
direction-respecting path, and so answered a question about routing rather than
about physical severance.

The 1,439 over-closure cases are smaller individually (median 157 m) and their
explanation is weaker — a multi-child group is a *necessary* condition for
over-closure to be the cause, not proof of it. This audit did not recompute
whether each group is genuinely a cut set.

For contrast, the 6,506 genuine-bridge cut-off results attribute 5,984 km in
total, median 239 m; 653 of them (10.04%) also sit in multi-child groups, so
even the correct positives carry some Q1 inflation in their isolation figure.

The Q2 limitation applies here too: one urban snapshot, 53.8% link coverage,
no national generalisation.

---

## What this implies for PR 2

1. **Closure scope and reported length must agree.** Either default to closing
   the selected link only, or report the group length that is actually being
   closed. The status quo is a label that does not describe the simulation for
   40.62% of the national network, and understates it by 2x or more for 30.48%.

2. **Bridge status has to gate the stranding claim.** 21.94% of Wellington's
   cut-off results assert stranding behind a link that cannot strand anything.
   `physical_access_links.is_bridge` already exists for both snapshots and is
   validated; a result claiming isolation behind a non-bridge selected link is
   detectably wrong before it reaches a user.

3. **Fix the directed-reachability path first, by impact.** It is the smaller
   cause by count and by far the larger by magnitude — 390 results carrying 96.5%
   of the wrongly attributed length. Over-closure produces more wrong answers;
   directed reachability produces the wrong answers that are visibly absurd.

4. **Do not lead with one-way carriageways.** The one-way state highway
   DISCONNECTED rate of 87.90% is real and worth quoting, but two-way results
   sit at 44.16-44.26%. The endpoint measure is broadly unfit, not
   situationally unfit.

5. **Wellington is the evidence base and it is urban.** Before quoting any Q2
   or Q3 rate as a property of the product, a V1-equivalent batch on the
   national snapshot is needed. Q1 is already national and can be quoted as is.
