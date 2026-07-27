/**
 * Export batch detour results to CSV and XLSX for analysis in Excel.
 *
 *   npm run export -- --snapshot <id> [--run car-distance-physical]
 *
 * Excel is the downstream reporting surface, not the map. The workbook is
 * written as styled ranges rather than Excel Table objects, and every value is
 * a number computed by the validated backend - there are no formulas that could
 * drift from the engine.
 *
 * Sheets:
 *   Link Detours      one row per link and direction
 *   Network Metadata  snapshot provenance
 *   Quality Summary   QA counts and issues
 *   Metric Definitions what each column means, and what it does not mean
 *   Source Lineage    where every input came from
 */

import { createReadStream, existsSync } from 'node:fs';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createInterface } from 'node:readline';
import path from 'node:path';

import ExcelJS from 'exceljs';

import { loadSnapshot, snapshotDir } from '../../packages/core/src/index.js';
import type { DetourResult } from '../../packages/core/src/index.js';
import { config } from '../lib/config.js';

interface Args {
  snapshot: string;
  run: string;
  baseUrl: string;
}

function parseArgs(argv: string[]): Args {
  const a: Args = {
    snapshot: '',
    run: 'car-distance-physical',
    baseUrl: config.applicationBaseUrl,
  };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--snapshot') a.snapshot = argv[++i];
    else if (argv[i] === '--run') a.run = argv[++i];
    else if (argv[i] === '--base-url') a.baseUrl = argv[++i];
  }
  if (!a.snapshot) throw new Error('--snapshot <id> is required');
  return a;
}

const COLUMNS = [
  { header: 'Snapshot ID', key: 'snapshotId', width: 34 },
  { header: 'AMDS Link ID', key: 'amdsId', width: 40 },
  { header: 'Internal Link ID', key: 'linkId', width: 14 },
  { header: 'Closure Group ID', key: 'closureGroupId', width: 40 },
  { header: 'Road Name', key: 'roadName', width: 30 },
  { header: 'Controlling Authority', key: 'rca', width: 24 },
  { header: 'Road Class', key: 'roadClass', width: 16 },
  { header: 'Surface', key: 'surface', width: 12 },
  { header: 'Lifeline Route', key: 'lifeline', width: 13 },
  { header: 'Direction', key: 'direction', width: 10 },
  { header: 'Closure Scope', key: 'closureScope', width: 13 },
  { header: 'Vehicle Profile', key: 'vehicle', width: 14 },
  { header: 'Status', key: 'status', width: 22 },
  { header: 'Links Removed', key: 'removedLinks', width: 13 },
  { header: 'Selected Link Length (m)', key: 'linkLengthM', width: 20 },
  { header: 'Normal Path Distance (m)', key: 'normalM', width: 21 },
  { header: 'Alternative Distance (m)', key: 'altM', width: 21 },
  { header: 'Added Distance vs Link (m)', key: 'addedM', width: 23 },
  { header: 'Network Penalty (m)', key: 'penaltyM', width: 18 },
  { header: 'Detour Ratio vs Link', key: 'ratio', width: 18 },
  { header: 'Normal Time est. (s)', key: 'normalS', width: 18 },
  { header: 'Alternative Time est. (s)', key: 'altS', width: 21 },
  { header: 'Added Time est. (s)', key: 'addedS', width: 18 },
  { header: 'No Detour Exists', key: 'disconnected', width: 15 },
  { header: 'Corridor Status', key: 'corridorStatus', width: 15 },
  { header: 'Corridor Penalty (m)', key: 'corridorPenaltyM', width: 19 },
  { header: 'Stranded Side', key: 'isolationSide', width: 14 },
  { header: 'Stranded Links', key: 'isolationLinks', width: 14 },
  { header: 'Stranded Road Length (m)', key: 'isolationM', width: 21 },
  { header: 'Speed Source', key: 'speedSource', width: 22 },
  { header: 'Quality Flags', key: 'flags', width: 60 },
  { header: 'Calculated At (UTC)', key: 'calculatedAt', width: 22 },
  { header: 'Open in App', key: 'permalink', width: 40 },
];

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const dir = snapshotDir(config.dataDir, args.snapshot);
  const resultsPath = path.join(dir, 'detours', args.run, 'results.ndjson');
  if (!existsSync(resultsPath)) {
    throw new Error(
      `no batch results at ${resultsPath}\n` +
        `run: npm run detours -- --snapshot ${args.snapshot}`,
    );
  }

  console.log(`loading snapshot ${args.snapshot}...`);
  const snap = await loadSnapshot(config.dataDir, args.snapshot);
  const byLinkId = new Map(snap.links.map((l) => [l.linkId, l]));

  let progress: any = null;
  try {
    progress = JSON.parse(
      await readFile(path.join(dir, 'detours', args.run, 'progress.json'), 'utf8'),
    );
  } catch {
    /* a run killed before completion has no progress file */
  }
  let qa: any = null;
  try {
    qa = JSON.parse(await readFile(path.join(dir, 'qa-report.json'), 'utf8'));
  } catch {
    /* QA is optional */
  }

  const outDir = path.join(config.dataDir, 'exports');
  await mkdir(outDir, { recursive: true });
  const stem = `${args.snapshot}-${args.run}`;

  // --- stream results into rows -------------------------------------------
  console.log('reading results...');
  const rows: Record<string, unknown>[] = [];
  const rl = createInterface({
    input: createReadStream(resultsPath, 'utf8'),
    crlfDelay: Infinity,
  });
  for await (const line of rl) {
    if (!line) continue;
    let r: DetourResult;
    try {
      r = JSON.parse(line);
    } catch {
      continue;
    }
    const link = byLinkId.get(r.linkId);
    if (!link) continue;
    for (const d of [r.forward, r.reverse]) {
      if (!d) continue;
      rows.push({
        snapshotId: r.snapshotId,
        amdsId: r.amdsId,
        linkId: r.linkId,
        closureGroupId: r.closureGroupId,
        roadName: link.roadName ?? '',
        rca:
          link.assetOwnerOrganisation === 1
            ? 'NZTA (state highway)'
            : `code ${link.assetOwnerOrganisation ?? ''}`,
        roadClass: link.modelAssetType === 1 ? 'Roadway' : `type ${link.modelAssetType}`,
        surface: link.surfaceType === 1 ? 'Sealed' : `type ${link.surfaceType}`,
        lifeline: link.lifeLineRoute ? 'Yes' : 'No',
        direction: d.direction,
        closureScope: r.closureScope,
        vehicle: r.vehicleProfile,
        status: d.status,
        removedLinks: r.removedLinkIds.length,
        linkLengthM: num(d.selectedLinkLengthM),
        normalM: num(d.normalPathDistanceM),
        altM: num(d.alternativeDistanceM),
        addedM: num(d.addedDistanceVsLinkM),
        penaltyM: num(d.networkPenaltyM),
        ratio: num(d.detourRatioVsLink, 3),
        normalS: num(d.normalPathTimeS),
        altS: num(d.alternativeTimeS),
        addedS: num(d.addedTimeS),
        disconnected: d.status === 'DISCONNECTED' ? 'Yes' : 'No',
        corridorStatus: d.corridor?.status ?? '',
        corridorPenaltyM: num(d.corridor?.penaltyM ?? null),
        isolationSide: d.isolation?.side ?? '',
        isolationLinks: d.isolation ? d.isolation.pocketLinkCount : '',
        isolationM: num(d.isolation?.pocketLengthM ?? null),
        speedSource: link.speedSource,
        flags: d.qualityFlags.join(' '),
        calculatedAt: r.calculatedAtUtc,
        permalink:
          `${args.baseUrl}/?link=${encodeURIComponent(r.amdsId)}` +
          `&snapshot=${r.snapshotId}&metric=${r.metric}` +
          `&vehicle=${r.vehicleProfile}&scope=${r.closureScope}&direction=${d.direction}`,
      });
    }
  }
  console.log(`  ${rows.length} rows (one per link and direction)`);

  // --- CSV (also the Power Query surface) ----------------------------------
  const csvPath = path.join(outDir, `${stem}.csv`);
  const esc = (v: unknown) => {
    const s = v === null || v === undefined ? '' : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const csv = [
    COLUMNS.map((c) => esc(c.header)).join(','),
    ...rows.map((r) => COLUMNS.map((c) => esc(r[c.key])).join(',')),
  ].join('\n');
  await writeFile(csvPath, csv + '\n', 'utf8');
  console.log(`  wrote ${csvPath}`);

  // --- XLSX -----------------------------------------------------------------
  const wb = new ExcelJS.Workbook();
  wb.creator = 'nz-critical-links';
  wb.created = new Date(snap.meta.retrievedAtUtc);

  const ws = wb.addWorksheet('Link Detours', {
    views: [{ state: 'frozen', ySplit: 1 }],
  });
  ws.columns = COLUMNS.map((c) => ({ header: c.header, key: c.key, width: c.width }));
  styleHeader(ws.getRow(1));
  for (const r of rows) ws.addRow(r);

  // Styled ranges, not an Excel Table object.
  ws.autoFilter = { from: { row: 1, column: 1 }, to: { row: 1, column: COLUMNS.length } };
  const statusCol = COLUMNS.findIndex((c) => c.key === 'status') + 1;
  const ratioCol = COLUMNS.findIndex((c) => c.key === 'ratio') + 1;
  for (let i = 2; i <= rows.length + 1; i++) {
    const cell = ws.getCell(i, statusCol);
    const v = String(cell.value ?? '');
    if (v === 'DISCONNECTED') tint(cell, 'FFFFF2CC');
    else if (v !== 'OK') tint(cell, 'FFFFD6D6');
    const rc = ws.getCell(i, ratioCol);
    if (typeof rc.value === 'number') {
      rc.numFmt = '0.00';
      if (rc.value >= 5) tint(rc, 'FFFFE0E0');
    }
  }
  for (const key of ['linkLengthM', 'normalM', 'altM', 'addedM', 'penaltyM', 'corridorPenaltyM', 'isolationM']) {
    const c = COLUMNS.findIndex((x) => x.key === key) + 1;
    ws.getColumn(c).numFmt = '#,##0';
  }

  // --- metadata sheet -------------------------------------------------------
  const meta = wb.addWorksheet('Network Metadata');
  meta.columns = [{ width: 34 }, { width: 96 }];
  addKv(meta, 'Snapshot ID', snap.meta.snapshotId);
  addKv(meta, 'Source dataset', snap.meta.sourceDataset);
  addKv(meta, 'Source URL', snap.meta.sourceUrl);
  addKv(meta, 'Retrieved (UTC)', snap.meta.retrievedAtUtc);
  addKv(meta, 'Snapshot status', snap.meta.status);
  addKv(meta, 'Filter applied', snap.meta.where);
  addKv(meta, 'Raw SHA-256', snap.meta.rawSha256);
  addKv(meta, 'Processing version', snap.meta.processingVersion);
  addKv(meta, 'Service feature count', snap.meta.sourceFeatureCount);
  addKv(meta, 'Downloaded features', snap.meta.downloadedFeatureCount);
  addKv(meta, 'Graph links (after splitting)', snap.links.length);
  addKv(meta, 'Graph arcs', snap.graph.arcCount);
  addKv(meta, 'Graph nodes', snap.graph.nodeCount);
  addKv(meta, 'Connected components', snap.graph.componentCount);
  addKv(meta, 'Turn restrictions applied', snap.restrictions.length);
  addKv(meta, 'Extract extent (EPSG:2193)', JSON.stringify(snap.meta.extent));
  addKv(meta, 'Analysis extent (EPSG:2193)', JSON.stringify(snap.meta.analysisExtent));
  addKv(meta, 'Licence', snap.meta.licence);
  addKv(meta, 'Attribution', snap.meta.attribution);
  meta.addRow([]);
  meta.addRow(['Ingest notes']).font = { bold: true };
  for (const n of snap.meta.notes) meta.addRow(['', n]);
  if (progress) {
    meta.addRow([]);
    meta.addRow(['Batch run']).font = { bold: true };
    addKv(meta, 'Run', progress.run);
    addKv(meta, 'Eligible links', progress.eligibleLinks);
    addKv(meta, 'Computed links', progress.computedLinks);
    addKv(meta, 'Completeness', progress.completeness);
    addKv(meta, 'Unresolved directions', progress.unresolvedDirections);
    addKv(meta, 'Elapsed (s)', progress.performance?.elapsedSeconds);
    addKv(meta, 'p50 / p95 / max (ms)',
      `${progress.performance?.p50Ms} / ${progress.performance?.p95Ms} / ${progress.performance?.maxMs}`);
  }

  // --- quality sheet --------------------------------------------------------
  const qs = wb.addWorksheet('Quality Summary');
  qs.columns = [{ width: 34 }, { width: 96 }];
  const statusCounts = new Map<string, number>();
  for (const r of rows) statusCounts.set(String(r.status), (statusCounts.get(String(r.status)) ?? 0) + 1);
  qs.addRow(['Result status counts']).font = { bold: true };
  for (const [k, v] of [...statusCounts].sort((a, b) => b[1] - a[1])) qs.addRow([k, v]);
  qs.addRow([]);
  const flagCounts = new Map<string, number>();
  for (const r of rows) {
    for (const f of String(r.flags).split(' ').filter(Boolean)) {
      flagCounts.set(f, (flagCounts.get(f) ?? 0) + 1);
    }
  }
  qs.addRow(['Quality flag counts']).font = { bold: true };
  for (const [k, v] of [...flagCounts].sort((a, b) => b[1] - a[1])) qs.addRow([k, v]);
  if (qa) {
    qs.addRow([]);
    qs.addRow(['Source-data QA issues']).font = { bold: true };
    for (const i of qa.issues ?? []) qs.addRow([`[${i.severity}] ${i.issueType} (${i.count})`, i.detail]);
  }

  // --- definitions ----------------------------------------------------------
  const defs = wb.addWorksheet('Metric Definitions');
  defs.columns = [{ width: 30 }, { width: 120 }];
  defs.addRow(['Column', 'Definition']).font = { bold: true };
  for (const [k, v] of DEFINITIONS) defs.addRow([k, v]);

  // --- lineage --------------------------------------------------------------
  const lin = wb.addWorksheet('Source Lineage');
  lin.columns = [{ width: 30 }, { width: 60 }, { width: 60 }];
  lin.addRow(['Input', 'Source', 'Used for']).font = { bold: true };
  for (const r of LINEAGE) lin.addRow(r);

  const xlsxPath = path.join(outDir, `${stem}.xlsx`);
  await wb.xlsx.writeFile(xlsxPath);
  console.log(`  wrote ${xlsxPath}`);

  console.log('\nExport complete.');
  console.log(`  rows:   ${rows.length}`);
  console.log(`  status: ${[...statusCounts].map(([k, v]) => `${k}=${v}`).join(', ')}`);
  if (progress && !progress.complete) {
    console.log(`  NOTE:   ${progress.completeness}`);
  }
}

const num = (v: number | null | undefined, dp = 1): number | '' =>
  v === null || v === undefined || !Number.isFinite(v)
    ? ''
    : Math.round(v * 10 ** dp) / 10 ** dp;

function styleHeader(row: ExcelJS.Row) {
  row.font = { bold: true, color: { argb: 'FFFFFFFF' } };
  row.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FF1F3864' } };
  row.alignment = { vertical: 'middle', wrapText: true };
  row.height = 32;
}

function tint(cell: ExcelJS.Cell, argb: string) {
  cell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb } };
}

function addKv(ws: ExcelJS.Worksheet, k: string, v: unknown) {
  const row = ws.addRow([k, v as any]);
  row.getCell(1).font = { bold: true };
}

const DEFINITIONS: [string, string][] = [
  ['Selected Link Length (m)', 'Length of the closed link, computed from its polyline in EPSG:2193.'],
  ['Normal Path Distance (m)', 'Shortest distance between the closed link\'s own endpoints on the INTACT network. Often shorter than the link itself when a shortcut exists.'],
  ['Alternative Distance (m)', 'Shortest distance between the same endpoints after every link in the closure group is removed. This is the replacement path.'],
  ['Added Distance vs Link (m)', 'Alternative Distance minus Selected Link Length. Negative when the closed link was not itself the shortest way between its endpoints.'],
  ['Network Penalty (m)', 'Alternative Distance minus Normal Path Distance. The more rigorous measure: it does not assume the closed link was the normal route.'],
  ['Detour Ratio vs Link', 'Alternative Distance divided by Selected Link Length. A ratio of 3 means the replacement path is three times the length of the closed road.'],
  ['Status', 'OK = a replacement path exists. DISCONNECTED = none exists between the link\'s endpoints. UNRESOLVED_TIMEOUT = the search ran out of budget and the answer is UNKNOWN, not "no detour". Other values are application faults, never network findings.'],
  ['No Detour Exists', 'Yes when Status is DISCONNECTED. On a one-way carriageway this is routine and does NOT mean the area is cut off - read Corridor Status and Stranded Links.'],
  ['Corridor Status', 'Result of the through-trip comparison between the nearest upstream and downstream points at which a driver has a choice. Populated when the endpoint measure is undefined.'],
  ['Corridor Penalty (m)', 'Extra distance for a through trip when the corridor measure resolved. This is usually the meaningful number for one-way carriageways.'],
  ['Stranded Side / Links / Road Length', 'What is cut off when no replacement path exists. A handful of links is a cul-de-sac; hundreds is a community with a single road in.'],
  ['Normal / Alternative / Added Time (s)', 'ESTIMATED travel times. AMDS publishes no speed attribute; speeds are inferred from urban/rural classification where available, otherwise asset type and ownership. Never treat these as observed or posted travel times.'],
  ['Speed Source', 'How the assumed speed was derived. "nslr" would mean an actual posted limit; no rows carry that yet.'],
  ['Quality Flags', 'Machine-readable caveats attached to the result. See docs/METRIC_DEFINITIONS.md.'],
  ['Links Removed', 'How many graph links the closure removed. Greater than 1 when the source road was split at junctions during ingest.'],
  ['IMPORTANT', 'These are STRUCTURAL replacement paths. They do not predict how much traffic uses each alternative route. That requires an origin-destination demand matrix, capacities, congestion functions and a traffic-assignment model, none of which is present.'],
];

const LINEAGE: [string, string, string][] = [
  ['AMDS Network Model layer 1', 'services.arcgis.com/CXBb7LAjgIIdcsPt .../AMDS_NetworkModel_PROD/FeatureServer/1', 'Link geometry, direction of travel, mode permissions, ownership'],
  ['AMDS RouteName + join table', 'Same service, tables 11 and 13', 'Road names and state-highway numbers'],
  ['AMDS UrbanRural', 'Same service, table 12', 'Urban/rural classification driving the speed estimate'],
  ['AMDS RestrictedTurn', 'Same service, table 9', 'Banned manoeuvres (only 60 exist nationally)'],
  ['AMDS Restriction', 'Same service, table 10', 'Height and weight limits, recorded as flags only'],
  ['AMDS Authority', 'Same service, table 2', 'Controlling authority names'],
  ['LINZ Basemaps', 'basemaps.linz.govt.nz', 'Web map background only - not the routing network'],
  ['NOT USED', 'OpenStreetMap', 'No OSM data is present in this database. Any future OSM enrichment must be kept separable for ODbL reasons.'],
  ['NOT USED', 'National Speed Limit Register', 'Not yet integrated. Would replace estimated speeds and set Speed Source to nslr.'],
  ['NOT USED', 'Traffic counts / AADT', 'Not yet integrated. Needed before any statement about vehicles affected.'],
];

main().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
