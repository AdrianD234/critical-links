/**
 * Source-data and graph quality assurance for a snapshot.
 *
 *   npm run qa -- <snapshotId>
 *
 * Writes qa-report.json and qa-summary.md into the snapshot directory. Nothing
 * here "repairs" data: the job is to measure and expose, so that a number in
 * the application can be traced to a stated condition of the source.
 */

import { writeFile, readFile } from 'node:fs/promises';
import path from 'node:path';

import { loadSnapshot, snapshotDir } from '../../packages/core/src/snapshot.js';
import { AMDS_MODEL_ASSET_TYPE, AMDS_SURFACE_TYPE } from '../../packages/core/src/types.js';
import { config } from '../lib/config.js';

interface Issue {
  severity: 'error' | 'warning' | 'info';
  issueType: string;
  entityType: 'link' | 'node' | 'graph' | 'restriction';
  count: number;
  detail: string;
  sampleIds?: string[];
}

async function main() {
  const snapshotId = process.argv[2];
  if (!snapshotId) throw new Error('usage: npm run qa -- <snapshotId>');

  const snap = await loadSnapshot(config.dataDir, snapshotId);
  const { graph: g, links, meta } = snap;
  const issues: Issue[] = [];
  const add = (i: Issue) => issues.push(i);

  // ---------------------------------------------------------------- counts
  const byAssetType = new Map<number, number>();
  const bySurface = new Map<number, number>();
  const byOwner = new Map<number, number>();
  const bySpeedSource = new Map<string, number>();
  let oneWay = 0;
  let twoWay = 0;
  let named = 0;
  let lifeline = 0;
  let heavyAllowed = 0;
  let emergencyAllowed = 0;
  let totalLengthM = 0;

  for (const l of links) {
    byAssetType.set(l.modelAssetType ?? -1, (byAssetType.get(l.modelAssetType ?? -1) ?? 0) + 1);
    bySurface.set(l.surfaceType ?? -1, (bySurface.get(l.surfaceType ?? -1) ?? 0) + 1);
    byOwner.set(l.assetOwnerOrganisation ?? -1, (byOwner.get(l.assetOwnerOrganisation ?? -1) ?? 0) + 1);
    bySpeedSource.set(l.speedSource, (bySpeedSource.get(l.speedSource) ?? 0) + 1);
    if (l.oneway === 1) oneWay++;
    else twoWay++;
    if (l.roadName) named++;
    if (l.lifeLineRoute) lifeline++;
    if (l.modeVehicleHeavy) heavyAllowed++;
    if (l.modeEmergency) emergencyAllowed++;
    totalLengthM += l.lengthM;
  }

  // ------------------------------------------------------------ link checks
  const zeroLength = links.filter((l) => !(l.lengthM > 0));
  if (zeroLength.length) {
    add({
      severity: 'error',
      issueType: 'ZERO_LENGTH_LINK',
      entityType: 'link',
      count: zeroLength.length,
      detail: 'Link has non-positive length and cannot carry traffic.',
      sampleIds: zeroLength.slice(0, 10).map((l) => l.amdsId),
    });
  }

  const selfLoops = links.filter((l) => l.sourceNode === l.targetNode);
  if (selfLoops.length) {
    add({
      severity: 'warning',
      issueType: 'SELF_LOOP',
      entityType: 'link',
      count: selfLoops.length,
      detail:
        'Link starts and ends at the same node (typically a closed loop road). ' +
        'Excluded from arc generation; detour results report SOURCE_DATA_ERROR.',
      sampleIds: selfLoops.slice(0, 10).map((l) => l.amdsId),
    });
  }

  const dupAmds = new Map<string, number>();
  for (const l of links) dupAmds.set(l.amdsId, (dupAmds.get(l.amdsId) ?? 0) + 1);
  const dupes = [...dupAmds.entries()].filter(([, c]) => c > 1);
  if (dupes.length) {
    add({
      severity: 'error',
      issueType: 'DUPLICATE_STABLE_ID',
      entityType: 'link',
      count: dupes.length,
      detail: 'Two graph links share an amdsId. Identifiers must be unique within a snapshot.',
      sampleIds: dupes.slice(0, 10).map(([id]) => id),
    });
  }

  const implausible = links.filter((l) => l.lengthM > 50_000);
  if (implausible.length) {
    add({
      severity: 'warning',
      issueType: 'IMPLAUSIBLE_LENGTH',
      entityType: 'link',
      count: implausible.length,
      detail: 'Link longer than 50 km. Verify against the source geometry.',
      sampleIds: implausible.slice(0, 10).map((l) => l.amdsId),
    });
  }

  const flagCounts = new Map<string, number>();
  for (const l of links) for (const f of l.qualityFlags) flagCounts.set(f, (flagCounts.get(f) ?? 0) + 1);

  // ----------------------------------------------------------- graph checks
  const degree = new Int32Array(g.nodeCount);
  for (let a = 0; a < g.arcCount; a++) {
    degree[g.arcFrom[a]]++;
    degree[g.arcTo[a]]++;
  }
  let dangling = 0;
  for (let n = 0; n < g.nodeCount; n++) if (degree[n] <= 2) dangling++;

  const compLinks = new Map<number, number>();
  for (const l of links) {
    if (l.sourceNode < 0) continue;
    const c = g.component[l.sourceNode];
    compLinks.set(c, (compLinks.get(c) ?? 0) + 1);
  }
  const compSizes = [...compLinks.values()].sort((a, b) => b - a);
  const largestShare = compSizes.length ? compSizes[0] / links.length : 0;
  // New Zealand is two main islands with no road connection between them, so a
  // national graph is legitimately dominated by TWO components, not one. Judging
  // it on the largest alone reports Cook Strait as a defect. The pilot extract
  // also spans the strait, so the same rule applies there.
  const topTwoShare =
    compSizes.length > 1 ? (compSizes[0] + compSizes[1]) / links.length : largestShare;

  if (topTwoShare < 0.9) {
    add({
      severity: 'error',
      issueType: 'FRAGMENTED_GRAPH',
      entityType: 'graph',
      count: g.componentCount,
      detail:
        `The two largest connected components hold only ${(topTwoShare * 100).toFixed(1)}% of links ` +
        `(largest ${(largestShare * 100).toFixed(1)}%). A road network should be dominated by ` +
        'one component per landmass. Investigate junction splitting.',
    });
  } else {
    add({
      severity: 'info',
      issueType: 'COMPONENT_STRUCTURE',
      entityType: 'graph',
      count: g.componentCount,
      detail:
        `Largest component ${(largestShare * 100).toFixed(1)}% of links; two largest ` +
        `${(topTwoShare * 100).toFixed(1)}%. Two dominant components is the expected shape ` +
        'for New Zealand: the North and South Islands have no road connection. Smaller ' +
        'components are ferry-only islands (Waiheke, Great Barrier), isolated peninsulas, ' +
        'and off-network parking or access areas.',
    });
  }

  // Closure groups: how many graph links does one physical closure remove?
  const groupSizes = new Map<string, number>();
  for (const l of links) groupSizes.set(l.closureGroupId, (groupSizes.get(l.closureGroupId) ?? 0) + 1);
  const groupDist = new Map<number, number>();
  for (const s of groupSizes.values()) groupDist.set(s, (groupDist.get(s) ?? 0) + 1);

  // Near misses recorded at ingest.
  let nearMisses: any[] = [];
  try {
    nearMisses = JSON.parse(
      await readFile(path.join(snapshotDir(config.dataDir, snapshotId), 'near-misses.json'), 'utf8'),
    );
  } catch {
    /* older snapshots may predate this file */
  }
  if (nearMisses.length) {
    add({
      severity: 'warning',
      issueType: 'UNCONNECTED_NEAR_MISS',
      entityType: 'node',
      count: nearMisses.length,
      detail:
        'A link endpoint lies between 0.05 m and 5 m of another link but was NOT connected. ' +
        'Either the source has a genuine gap or the split tolerance is too tight. ' +
        'These are listed in near-misses.json for review.',
    });
  }

  // Turn restrictions.
  const longRestrictions = snap.restrictions.filter((r) => r.linkSeq.length > 2);
  add({
    severity: longRestrictions.length ? 'warning' : 'info',
    issueType: 'TURN_RESTRICTION_COVERAGE',
    entityType: 'restriction',
    count: snap.restrictions.length,
    detail:
      `${snap.restrictions.length} turn restrictions are applied, of which ` +
      `${longRestrictions.length} span more than two links. Two-link restrictions are ` +
      'enforced exactly by the arc-expanded search; longer sequences are checked against ' +
      'the predecessor chain and are approximate. NOTE: AMDS publishes only 60 restricted ' +
      'turns nationally, so banned-turn coverage is effectively negligible and routes ' +
      'through complex intersections must not be presented as road-legal.',
  });

  // Speed provenance.
  const nslr = bySpeedSource.get('nslr') ?? 0;
  add({
    severity: nslr === 0 ? 'warning' : 'info',
    issueType: 'SPEED_PROVENANCE',
    entityType: 'link',
    count: links.length - nslr,
    detail:
      'AMDS publishes no speed attribute. All time-metric results are derived from ' +
      'estimated speeds and are flagged TIME_ESTIMATED. Distance is the defensible metric.',
  });

  const report = {
    snapshotId,
    generatedAtUtc: new Date().toISOString(),
    snapshot: {
      sourceDataset: meta.sourceDataset,
      retrievedAtUtc: meta.retrievedAtUtc,
      status: meta.status,
      where: meta.where,
      extent: meta.extent,
      analysisExtent: meta.analysisExtent,
      sourceFeatureCount: meta.sourceFeatureCount,
      downloadedFeatureCount: meta.downloadedFeatureCount,
      routableLinkCount: meta.routableLinkCount,
      notes: meta.notes,
    },
    totals: {
      links: links.length,
      arcs: g.arcCount,
      nodes: g.nodeCount,
      components: g.componentCount,
      largestComponentLinks: compSizes[0] ?? 0,
      largestComponentSharePct: Number((largestShare * 100).toFixed(2)),
      twoLargestComponentsSharePct: Number((topTwoShare * 100).toFixed(2)),
      totalNetworkLengthKm: Number((totalLengthM / 1000).toFixed(1)),
      danglingNodes: dangling,
      danglingNodeSharePct: Number(((dangling / g.nodeCount) * 100).toFixed(2)),
      oneWayLinks: oneWay,
      twoWayLinks: twoWay,
      namedLinks: named,
      namedSharePct: Number(((named / links.length) * 100).toFixed(2)),
      lifelineRouteLinks: lifeline,
      heavyVehicleLinks: heavyAllowed,
      emergencyVehicleLinks: emergencyAllowed,
    },
    byModelAssetType: Object.fromEntries(
      [...byAssetType.entries()].map(([k, v]) => [
        (AMDS_MODEL_ASSET_TYPE as any)[k] ?? `code ${k}`,
        v,
      ]),
    ),
    bySurfaceType: Object.fromEntries(
      [...bySurface.entries()].map(([k, v]) => [(AMDS_SURFACE_TYPE as any)[k] ?? `code ${k}`, v]),
    ),
    bySpeedSource: Object.fromEntries(bySpeedSource),
    topOwners: [...byOwner.entries()].sort((a, b) => b[1] - a[1]).slice(0, 15),
    componentSizes: compSizes.slice(0, 20),
    closureGroupSizeDistribution: Object.fromEntries(
      [...groupDist.entries()].sort((a, b) => a[0] - b[0]),
    ),
    qualityFlagCounts: Object.fromEntries([...flagCounts.entries()].sort((a, b) => b[1] - a[1])),
    issues,
  };

  const dir = snapshotDir(config.dataDir, snapshotId);
  await writeFile(path.join(dir, 'qa-report.json'), JSON.stringify(report, null, 2), 'utf8');
  await writeFile(path.join(dir, 'qa-summary.md'), renderMd(report), 'utf8');

  console.log(`QA report for ${snapshotId}`);
  console.log(`  links ${report.totals.links}  arcs ${report.totals.arcs}  nodes ${report.totals.nodes}`);
  console.log(`  components ${report.totals.components}, largest ${report.totals.largestComponentSharePct}%`);
  console.log(`  network length ${report.totals.totalNetworkLengthKm} km`);
  console.log(`  named links ${report.totals.namedSharePct}%`);
  console.log('\n  issues:');
  for (const i of issues) {
    console.log(`    [${i.severity.toUpperCase()}] ${i.issueType} (${i.count})`);
  }
  console.log(`\n  written: ${path.join(dir, 'qa-report.json')}`);
}

function renderMd(r: any): string {
  const L: string[] = [];
  L.push(`# QA report - ${r.snapshotId}\n`);
  L.push(`Generated ${r.generatedAtUtc}\n`);
  L.push('## Snapshot\n');
  L.push('| field | value |');
  L.push('| --- | --- |');
  for (const [k, v] of Object.entries(r.snapshot)) {
    if (k === 'notes') continue;
    L.push(`| ${k} | ${typeof v === 'object' ? `\`${JSON.stringify(v)}\`` : v} |`);
  }
  L.push('\n### Ingest notes\n');
  for (const n of r.snapshot.notes) L.push(`- ${n}`);
  L.push('\n## Totals\n');
  L.push('| metric | value |');
  L.push('| --- | --- |');
  for (const [k, v] of Object.entries(r.totals)) L.push(`| ${k} | ${v} |`);
  L.push('\n## Composition\n');
  L.push('### By model asset type\n');
  L.push('| type | links |');
  L.push('| --- | --- |');
  for (const [k, v] of Object.entries(r.byModelAssetType)) L.push(`| ${k} | ${v} |`);
  L.push('\n### By speed source\n');
  L.push('| source | links |');
  L.push('| --- | --- |');
  for (const [k, v] of Object.entries(r.bySpeedSource)) L.push(`| ${k} | ${v} |`);
  L.push('\n### Closure group size (graph links removed per physical closure)\n');
  L.push('| links in group | groups |');
  L.push('| --- | --- |');
  for (const [k, v] of Object.entries(r.closureGroupSizeDistribution)) L.push(`| ${k} | ${v} |`);
  L.push('\n### Quality flags\n');
  L.push('| flag | links |');
  L.push('| --- | --- |');
  for (const [k, v] of Object.entries(r.qualityFlagCounts)) L.push(`| ${k} | ${v} |`);
  L.push('\n## Issues\n');
  for (const i of r.issues) {
    L.push(`### [${i.severity.toUpperCase()}] ${i.issueType} - ${i.count}\n`);
    L.push(i.detail + '\n');
    if (i.sampleIds?.length) L.push('Samples: ' + i.sampleIds.map((s: string) => `\`${s}\``).join(', ') + '\n');
  }
  return L.join('\n') + '\n';
}

main().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
