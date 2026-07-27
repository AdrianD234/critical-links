/**
 * Ingest the AMDS Network Model into an immutable local snapshot.
 *
 *   npm run ingest -- --pilot wellington
 *   npm run ingest -- --national
 *   npm run ingest -- --bbox 1710000,5390000,1800000,5480000 --name my-area
 *
 * What this does, and why in this order:
 *   1. pins the exact OBJECTID set that matches the filter
 *   2. downloads those ids in bounded batches with retry/backoff
 *   3. reconciles what came back against what was asked for - a shortfall
 *      marks the snapshot `partial`, it never passes silently
 *   4. joins the attribute tables that make the result usable (road names,
 *      urban/rural, turn restrictions, height/weight restrictions)
 *   5. writes a snapshot keyed by a content-derived id
 *
 * Extent handling: a clipped extract is downloaded with a generous BUFFER
 * around the analysis area. Without it, every link near the edge would appear
 * to have no detour simply because the road that carries the detour was not
 * downloaded. Results that lean on buffer links are flagged, and DISCONNECTED
 * results in a clipped snapshot are flagged as unverified.
 */

import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';

import {
  assignSpeed,
  buildGraph,
  polylineLength,
  splitAtJunctions,
  writeSnapshot,
  PROCESSING_VERSION,
  type Bbox2193,
  type GeometryStore,
  type LinkRecord,
  type SnapshotMeta,
  type TurnRestriction,
} from '../../packages/core/src/index.js';
import { config, DEFAULT_ATTRIBUTION } from '../lib/config.js';
import {
  downloadByIds,
  getCount,
  getLayerMeta,
  getObjectIds,
  type ExtentFilter,
} from '../lib/arcgis.js';

/** Only current, vehicle-accessible links enter the routable graph. */
const LINK_WHERE = 'status=1 AND modeVehicle=1';

const LINK_FIELDS = [
  'OBJECTID',
  'amdsIDNetworkModel',
  'status',
  'modelAssetType',
  'oneway',
  'surfaceType',
  'assetOwnerOrganisation',
  'dataManagingOrganisation',
  'amdsIDAuthority',
  'lifeLineRoute',
  'sharedInfrastructure',
  'detour',
  'modeVehicle',
  'modeVehicleHeavy',
  'modeEmergencyManagement',
  'modeFerry',
  'isLengthCounted',
  'Shape__Length',
].join(',');

interface PilotPreset {
  name: string;
  description: string;
  analysis: Bbox2193;
  extract: Bbox2193;
}

/**
 * Wellington was chosen over Auckland because its topology is dominated by
 * genuine single points of failure - the Ngauranga Gorge, the Hutt corridor,
 * Rimutaka Hill Road, Mt Victoria and Terrace tunnels, the Wellington one-way
 * pairs - which is precisely what a criticality tool has to get right. It also
 * contains motorway, divided carriageway, complex interchanges, rural state
 * highway and dense urban local roads within one modest extent.
 *
 * The extract is buffered 60 km beyond the analysis area so that long regional
 * detours (for instance via SH2 and SH58) stay inside the downloaded network.
 */
const PILOTS: Record<string, PilotPreset> = {
  wellington: {
    name: 'wellington',
    description:
      'Wellington City, Porirua, Lower and Upper Hutt, with a 60 km network buffer',
    analysis: { xmin: 1735000, ymin: 5415000, xmax: 1775000, ymax: 5455000 },
    extract: { xmin: 1675000, ymin: 5355000, xmax: 1835000, ymax: 5515000 },
  },
  auckland: {
    name: 'auckland',
    description: 'Auckland isthmus and North Shore, with a 60 km network buffer',
    analysis: { xmin: 1740000, ymin: 5890000, xmax: 1780000, ymax: 5940000 },
    extract: { xmin: 1680000, ymin: 5830000, xmax: 1840000, ymax: 6000000 },
  },
};

interface Args {
  national: boolean;
  pilot?: string;
  bbox?: Bbox2193;
  analysisBbox?: Bbox2193;
  name?: string;
  concurrency: number;
}

function parseArgs(argv: string[]): Args {
  const out: Args = { national: false, concurrency: 6 };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--national') out.national = true;
    else if (a === '--pilot') out.pilot = argv[++i];
    else if (a === '--name') out.name = argv[++i];
    else if (a === '--concurrency') out.concurrency = Number(argv[++i]);
    else if (a === '--bbox') out.bbox = parseBbox(argv[++i]);
    else if (a === '--analysis-bbox') out.analysisBbox = parseBbox(argv[++i]);
  }
  return out;
}

function parseBbox(s: string): Bbox2193 {
  const p = s.split(',').map(Number);
  if (p.length !== 4 || p.some((n) => !Number.isFinite(n))) {
    throw new Error(`bad bbox "${s}", expected xmin,ymin,xmax,ymax in EPSG:2193`);
  }
  return { xmin: p[0], ymin: p[1], xmax: p[2], ymax: p[3] };
}

const toFilter = (b: Bbox2193 | null): ExtentFilter | null =>
  b ? { ...b, wkid: 2193 } : null;

const geometryPreSplit = (coords: number[], offset: number[]): GeometryStore => ({
  coords: Float64Array.from(coords),
  offset: Int32Array.from(offset),
});

/** Pull an entire attribute table, no geometry, using the id-list strategy. */
async function fetchTable(
  layerId: number,
  fields: string[],
  concurrency: number,
  label: string,
): Promise<any[]> {
  const meta = await getLayerMeta(config.amds.serviceUrl, layerId);
  const ids = await getObjectIds(config.amds.serviceUrl, layerId, { where: '1=1' });
  process.stdout.write(`  ${label}: ${ids.length} rows`);
  if (ids.length === 0) {
    console.log('');
    return [];
  }
  const res = await downloadByIds({
    serviceUrl: config.amds.serviceUrl,
    layerId,
    objectIds: ids,
    outFields: fields.join(','),
    returnGeometry: false,
    outSR: config.analysisSrid,
    batchSize: meta.maxRecordCount,
    concurrency,
    objectIdField: meta.objectIdField,
  });
  console.log(
    ` -> ${res.features.length} downloaded` +
      (res.missingIds.length ? `  MISSING ${res.missingIds.length}` : ''),
  );
  return res.features.map((f) => f.attributes);
}

async function main(): Promise<void> {
  const args = parseArgs(process.argv.slice(2));

  let extract: Bbox2193 | null = null;
  let analysis: Bbox2193 | null = null;
  let areaName: string;

  if (args.national) {
    areaName = 'national';
  } else if (args.pilot) {
    const p = PILOTS[args.pilot];
    if (!p) {
      throw new Error(
        `unknown pilot "${args.pilot}". Available: ${Object.keys(PILOTS).join(', ')}`,
      );
    }
    extract = p.extract;
    analysis = p.analysis;
    areaName = p.name;
  } else if (args.bbox) {
    extract = args.bbox;
    analysis = args.analysisBbox ?? args.bbox;
    areaName = args.name ?? 'custom';
  } else {
    throw new Error('specify --national, --pilot <name>, or --bbox xmin,ymin,xmax,ymax');
  }

  const retrievedAtUtc = new Date().toISOString();
  console.log(`AMDS ingest: ${areaName}`);
  console.log(`  service: ${config.amds.serviceUrl}`);
  console.log(`  where:   ${LINK_WHERE}`);
  console.log(`  extract: ${extract ? JSON.stringify(extract) : 'national (no extent filter)'}`);

  const linkLayer = await getLayerMeta(config.amds.serviceUrl, config.amds.linkLayerId);

  // --- 1. pin the id set --------------------------------------------------
  const sourceFeatureCount = await getCount(
    config.amds.serviceUrl,
    config.amds.linkLayerId,
    { where: LINK_WHERE, extent: toFilter(extract) },
  );
  const ids = await getObjectIds(config.amds.serviceUrl, config.amds.linkLayerId, {
    where: LINK_WHERE,
    extent: toFilter(extract),
  });
  console.log(`\n  service reports ${sourceFeatureCount} features; id list has ${ids.length}`);
  if (ids.length !== sourceFeatureCount) {
    console.warn(
      `  WARNING: count/id-list mismatch (${sourceFeatureCount} vs ${ids.length}). ` +
        `Service may be mid-edit. Proceeding against the id list.`,
    );
  }

  // --- 2. download ---------------------------------------------------------
  let lastPct = -1;
  const dl = await downloadByIds({
    serviceUrl: config.amds.serviceUrl,
    layerId: config.amds.linkLayerId,
    objectIds: ids,
    outFields: LINK_FIELDS,
    returnGeometry: true,
    outSR: config.analysisSrid,
    batchSize: linkLayer.maxRecordCount,
    concurrency: args.concurrency,
    objectIdField: linkLayer.objectIdField,
    onProgress: (done, total) => {
      const pct = Math.floor((done / total) * 100);
      if (pct !== lastPct && pct % 5 === 0) {
        lastPct = pct;
        process.stdout.write(`\r  downloading links... ${pct}% (${done}/${total})`);
      }
    },
  });
  console.log(`\r  downloading links... done (${dl.features.length}/${ids.length})      `);
  if (dl.missingIds.length > 0) {
    console.warn(`  WARNING: ${dl.missingIds.length} ids did not come back`);
  }
  if (dl.duplicateIds.length > 0) {
    console.warn(`  WARNING: ${dl.duplicateIds.length} duplicate ids returned`);
  }

  // --- 3. attribute tables -------------------------------------------------
  console.log('\n  joining attribute tables');
  const [routeJoin, routeNames, urbanRural, turns, restrictions, authorities] =
    await Promise.all([
      fetchTable(
        config.amds.routeNameTableId,
        ['amdsIDNetworkModel', 'amdsIDRouteName', 'isPrimary'],
        args.concurrency,
        'route-name join',
      ),
      fetchTable(
        11,
        [
          'amdsIDRouteName',
          'routeNameFullASCII',
          'routeNameAbbreviatedASCII',
          'routeNumber1',
          'routeAlpha1',
          'routeGroup',
          'status',
        ],
        args.concurrency,
        'route names',
      ),
      fetchTable(
        12,
        ['amdsIDNetworkModel', 'urbanRural', 'isFullLength', 'status'],
        args.concurrency,
        'urban/rural',
      ),
      fetchTable(
        config.amds.restrictedTurnTableId,
        [
          'amdsIDRestrictedTurn',
          ...Array.from({ length: 8 }, (_, i) => `amdsIDNetworkModel${i + 1}`),
          'modeVehicleRestricted',
          'modeVehicleHeavyRestricted',
          'modeEmergencyRestricted',
          'status',
        ],
        args.concurrency,
        'restricted turns',
      ),
      fetchTable(
        10,
        [
          'amdsIDNetworkModel',
          'modeRestriction',
          'heightRestriction',
          'heightInfo',
          'weightRestriction',
          'weightInfo',
          'isFullLength',
          'status',
        ],
        args.concurrency,
        'restrictions',
      ),
      fetchTable(
        config.amds.authorityTableId,
        ['amdsIDAuthority', 'controllingNameASCII'],
        args.concurrency,
        'authorities',
      ),
    ]);

  // --- 4. build link records ----------------------------------------------
  console.log('\n  building link records');

  const nameByRouteId = new Map<string, string>();
  const numberByRouteId = new Map<string, string>();
  for (const r of routeNames) {
    if (r.routeNameFullASCII) nameByRouteId.set(r.amdsIDRouteName, r.routeNameFullASCII);
    const num =
      r.routeNumber1 !== null && r.routeNumber1 !== undefined
        ? `${r.routeNumber1}${r.routeAlpha1 ?? ''}`
        : null;
    if (num) numberByRouteId.set(r.amdsIDRouteName, num);
  }
  const nameByLink = new Map<string, string>();
  const numberByLink = new Map<string, string>();
  for (const j of routeJoin) {
    const nm = nameByRouteId.get(j.amdsIDRouteName);
    const nu = numberByRouteId.get(j.amdsIDRouteName);
    if (nm && (j.isPrimary === 1 || !nameByLink.has(j.amdsIDNetworkModel))) {
      nameByLink.set(j.amdsIDNetworkModel, nm);
    }
    if (nu && (j.isPrimary === 1 || !numberByLink.has(j.amdsIDNetworkModel))) {
      numberByLink.set(j.amdsIDNetworkModel, nu);
    }
  }

  // urbanRural domain: 1 = Urban, 2 = Rural (verified against the layer domain
  // in the discovery report; anything else is treated as unknown).
  const urbanRuralByLink = new Map<string, 'urban' | 'rural'>();
  for (const u of urbanRural) {
    if (u.status !== 1) continue;
    const v = u.urbanRural === 1 ? 'urban' : u.urbanRural === 2 ? 'rural' : null;
    if (v && !urbanRuralByLink.has(u.amdsIDNetworkModel)) {
      urbanRuralByLink.set(u.amdsIDNetworkModel, v);
    }
  }

  const authorityName = new Map<string, string>();
  for (const a of authorities) {
    if (a.amdsIDAuthority) authorityName.set(a.amdsIDAuthority, a.controllingNameASCII);
  }

  const heightWeightByLink = new Map<string, string[]>();
  for (const r of restrictions) {
    if (r.status !== 1) continue;
    const flags: string[] = [];
    if (r.heightRestriction === 1) flags.push(`HEIGHT_LIMIT_${r.heightInfo ?? '?'}m`);
    if (r.weightRestriction === 1) flags.push(`WEIGHT_LIMIT_${r.weightInfo ?? '?'}t`);
    if (r.modeRestriction === 1) flags.push('MODE_RESTRICTED');
    if (flags.length === 0) continue;
    const prev = heightWeightByLink.get(r.amdsIDNetworkModel) ?? [];
    heightWeightByLink.set(r.amdsIDNetworkModel, [...new Set([...prev, ...flags])]);
  }

  const links: LinkRecord[] = [];
  const coordsArr: number[] = [];
  const offsetArr: number[] = [0];
  const linkIdByAmds = new Map<string, number>();

  let multipart = 0;
  let degenerate = 0;
  let duplicateAmds = 0;
  let lengthMismatch = 0;

  for (const f of dl.features) {
    const a = f.attributes;
    const amdsId: string = a.amdsIDNetworkModel;
    const paths: number[][][] | undefined = f.geometry?.paths;

    if (!paths || paths.length === 0 || paths[0].length < 2) {
      degenerate++;
      continue;
    }
    if (linkIdByAmds.has(amdsId)) {
      duplicateAmds++;
      continue;
    }

    const qualityFlags: string[] = [];
    if (paths.length > 1) {
      multipart++;
      qualityFlags.push('MULTIPART_GEOMETRY_FIRST_PATH_USED');
    }

    const linkId = links.length;
    const pts = paths[0];
    for (const p of pts) coordsArr.push(p[0], p[1]);
    offsetArr.push(coordsArr.length);

    const lengthM = polylineLength(coordsArr, offsetArr[linkId], offsetArr[linkId + 1]);
    if (lengthM <= 0) {
      // Rewind: a zero-length line cannot participate in routing.
      coordsArr.length = offsetArr[linkId];
      offsetArr.pop();
      degenerate++;
      continue;
    }
    const srcLen = typeof a.Shape__Length === 'number' ? a.Shape__Length : null;
    // Shape__Length is published in the service's own SR (Web Mercator), which
    // is inflated by roughly 1/cos(latitude) at NZ latitudes. It is kept only
    // as a cross-check and is never used as a distance.
    if (srcLen !== null && srcLen > 0) {
      const ratio = srcLen / lengthM;
      if (ratio < 1.2 || ratio > 1.6) lengthMismatch++;
    }

    const ur = urbanRuralByLink.get(amdsId) ?? null;
    const speed = assignSpeed({
      modelAssetType: a.modelAssetType ?? null,
      surfaceType: a.surfaceType ?? null,
      assetOwnerOrganisation: a.assetOwnerOrganisation ?? null,
      urbanRural: ur,
    });
    if (!ur) qualityFlags.push('NO_URBAN_RURAL_COVERAGE');
    const hw = heightWeightByLink.get(amdsId);
    if (hw) qualityFlags.push(...hw);

    const oneway = a.oneway;
    if (oneway !== 1 && oneway !== 2) qualityFlags.push('ONEWAY_UNSET_ASSUMED_TWO_WAY');

    linkIdByAmds.set(amdsId, linkId);
    links.push({
      linkId,
      amdsId,
      objectId: a.OBJECTID,
      // AMDS layer 1 exposes no carriageway or physical-asset relationship, so
      // the closure group is the source link itself. Both directed arcs of a
      // two-way road therefore share a group, which is the documented MVP
      // minimum. Merging divided carriageways or bridge decks would need a
      // source relationship that is not published - see docs/KNOWN_LIMITATIONS.md.
      closureGroupId: amdsId,
      roadName: nameByLink.get(amdsId) ?? null,
      assetOwnerOrganisation: a.assetOwnerOrganisation ?? null,
      dataManagingOrganisation: a.dataManagingOrganisation ?? null,
      amdsIDAuthority: a.amdsIDAuthority ?? null,
      modelAssetType: a.modelAssetType ?? null,
      surfaceType: a.surfaceType ?? null,
      status: a.status ?? null,
      oneway: oneway ?? null,
      lengthM,
      sourceLengthM: srcLen,
      forwardAllowed: true,
      reverseAllowed: oneway !== 1,
      modeVehicle: a.modeVehicle === 1,
      modeVehicleHeavy: a.modeVehicleHeavy === 1,
      modeEmergency: a.modeEmergencyManagement === 1,
      modeFerry: a.modeFerry === 1,
      lifeLineRoute: a.lifeLineRoute === 1,
      sharedInfrastructure: a.sharedInfrastructure === 1,
      detourAvailableFlag: a.detour === 1,
      speedKph: speed.speedKph,
      speedSource: speed.speedSource,
      qualityFlags,
      sourceNode: -1,
      targetNode: -1,
    });
  }

  // Road number as a separate searchable field lives in roadName when absent.
  for (const l of links) {
    const num = numberByLink.get(l.amdsId);
    if (num && !l.roadName) l.roadName = `SH ${num}`;
  }

  // --- 4b. split at junctions ---------------------------------------------
  // AMDS does not cut a through road where a side road terminates on it.
  // Without this step the graph is not a road network - see
  // packages/core/src/topology.ts and docs/KNOWN_LIMITATIONS.md.
  console.log('  splitting links at junctions');
  const preSplitLinkIdByAmds = linkIdByAmds;
  const splitRes = splitAtJunctions(links, geometryPreSplit(coordsArr, offsetArr));
  console.log(
    `    ${splitRes.parentsSplit} source links cut, ${splitRes.cutsMade} junctions inserted, ` +
      `${links.length} -> ${splitRes.linkCount} links`,
  );
  console.log(
    `    ${splitRes.nearMisses.length} endpoints within review distance but not connected`,
  );

  // --- 5. resolve turn restrictions to internal link ids -------------------
  // Splitting means one source link can now be several graph links, so a
  // restriction sequence has to be resolved to a CONNECTED chain of pieces.
  // Node ids are needed for that, so the graph is assembled once here with no
  // restrictions purely to establish topology.
  const splitLinks = splitRes.links;
  const geometry = splitRes.geometry;
  const topoOnly = buildGraph(splitLinks, geometry, []);

  const childrenByParent = new Map<string, number[]>();
  for (const l of splitLinks) {
    let a = childrenByParent.get(l.closureGroupId);
    if (!a) childrenByParent.set(l.closureGroupId, (a = []));
    a.push(l.linkId);
  }

  const touches = (linkId: number, node: number) =>
    splitLinks[linkId].sourceNode === node || splitLinks[linkId].targetNode === node;
  const sharedNode = (a: number, b: number): number => {
    const la = splitLinks[a];
    const lb = splitLinks[b];
    for (const n of [la.sourceNode, la.targetNode]) {
      if (touches(b, n)) return n;
    }
    void lb;
    return -1;
  };

  /** Depth-first search for a chain of pieces matching the parent sequence. */
  function resolveChain(parents: string[]): number[] | null {
    const options = parents.map((p) => childrenByParent.get(p) ?? []);
    if (options.some((o) => o.length === 0)) return null;
    const chain: number[] = [];
    const walk = (i: number): boolean => {
      if (i === options.length) return true;
      for (const cand of options[i]) {
        if (i > 0 && sharedNode(chain[i - 1], cand) < 0) continue;
        chain.push(cand);
        if (walk(i + 1)) return true;
        chain.pop();
      }
      return false;
    };
    return walk(0) ? [...chain] : null;
  }

  const resolvedTurns: TurnRestriction[] = [];
  let turnsOutsideExtract = 0;
  let turnsAmbiguousAfterSplit = 0;
  let turnsLongerThanTwo = 0;
  for (const t of turns) {
    if (t.status !== 1) continue;
    const seqIds: string[] = [];
    for (let i = 1; i <= 8; i++) {
      const v = t[`amdsIDNetworkModel${i}`];
      if (v) seqIds.push(v);
    }
    if (seqIds.length < 2 || seqIds.some((s) => !preSplitLinkIdByAmds.has(s))) {
      // References links that were not downloaded in this extract.
      turnsOutsideExtract++;
      continue;
    }
    const chain = resolveChain(seqIds);
    if (!chain) {
      turnsAmbiguousAfterSplit++;
      continue;
    }
    if (chain.length > 2) turnsLongerThanTwo++;
    resolvedTurns.push({
      amdsIDRestrictedTurn: t.amdsIDRestrictedTurn,
      linkSeq: chain,
      restrictedVehicle: t.modeVehicleRestricted === 1,
      restrictedVehicleHeavy: t.modeVehicleHeavyRestricted === 1,
      restrictedEmergency: t.modeEmergencyRestricted === 1,
    });
  }
  void topoOnly;

  // --- 6. snapshot id and metadata ----------------------------------------
  const snapshotId = [
    'amds',
    areaName,
    retrievedAtUtc.slice(0, 10),
    createHash('sha256')
      .update(dl.sha256)
      .update(LINK_WHERE)
      .update(JSON.stringify(extract))
      .update(PROCESSING_VERSION)
      .digest('hex')
      .slice(0, 8),
  ].join('-');

  const notes: string[] = [];
  if (dl.missingIds.length) notes.push(`${dl.missingIds.length} requested ids not returned`);
  if (dl.duplicateIds.length) notes.push(`${dl.duplicateIds.length} duplicate ids returned`);
  if (multipart) notes.push(`${multipart} multipart geometries, first path used`);
  if (degenerate) notes.push(`${degenerate} degenerate/zero-length features dropped`);
  if (duplicateAmds) notes.push(`${duplicateAmds} duplicate amdsIDNetworkModel values dropped`);
  notes.push(
    `junction splitting: ${splitRes.parentsSplit} source links cut at ${splitRes.cutsMade} junctions, ` +
      `${links.length} source links -> ${splitRes.linkCount} graph links`,
  );
  notes.push(
    `${splitRes.nearMisses.length} endpoints lie between the 0.05 m split tolerance and 5 m; ` +
      `these were NOT connected and are listed in the QA report`,
  );
  if (turnsOutsideExtract)
    notes.push(`${turnsOutsideExtract} turn restrictions reference links outside this extract`);
  if (turnsAmbiguousAfterSplit)
    notes.push(`${turnsAmbiguousAfterSplit} turn restrictions could not be resolved to a connected chain after splitting`);
  notes.push(
    `${resolvedTurns.length} turn restrictions applied, of which ${turnsLongerThanTwo} span more than two links`,
  );
  notes.push(
    `AMDS publishes no speed attribute; speeds are estimated (see packages/core/src/speed.ts)`,
  );

  const meta: SnapshotMeta = {
    snapshotId,
    sourceDataset: 'NZTA AMDS Network Model (AMDS_NetworkModel_PROD)',
    sourceVersion: String(linkLayer.raw?.currentVersion ?? ''),
    retrievedAtUtc,
    sourceUrl: `${config.amds.serviceUrl}/${config.amds.linkLayerId}`,
    layerId: config.amds.linkLayerId,
    licence:
      'Published by NZTA Waka Kotahi for open access and consumption. No explicit licence string is set on the ArcGIS item; see docs/LICENSING.md.',
    attribution: DEFAULT_ATTRIBUTION,
    rawSha256: dl.sha256,
    processingVersion: PROCESSING_VERSION,
    sourceFeatureCount,
    downloadedFeatureCount: dl.features.length,
    routableLinkCount: splitLinks.length,
    arcCount: 0,
    nodeCount: 0,
    extent: extract,
    analysisExtent: analysis,
    where: LINK_WHERE,
    status:
      dl.missingIds.length === 0 && dl.features.length === ids.length
        ? 'complete'
        : 'partial',
    notes,
  };

  const built = buildGraph(splitLinks, geometry, resolvedTurns);
  meta.arcCount = built.graph.arcCount;
  meta.nodeCount = built.graph.nodeCount;

  const dir = await writeSnapshot(
    config.dataDir,
    meta,
    splitLinks,
    geometry,
    resolvedTurns,
  );

  // Near misses are a source-data finding, not a routing result. They are kept
  // beside the snapshot so a data steward can review real gaps in the network.
  await writeFile(
    path.join(dir, 'near-misses.json'),
    JSON.stringify(splitRes.nearMisses.slice(0, 20000), null, 2),
    'utf8',
  );

  // Keep the raw manifest next to the processed snapshot for provenance.
  await mkdir(path.join(config.dataDir, 'raw'), { recursive: true });
  await writeFile(
    path.join(config.dataDir, 'raw', `${snapshotId}.manifest.json`),
    JSON.stringify(
      {
        snapshotId,
        retrievedAtUtc,
        serviceUrl: config.amds.serviceUrl,
        layerId: config.amds.linkLayerId,
        where: LINK_WHERE,
        extent: extract,
        requestedIds: ids.length,
        downloadedFeatures: dl.features.length,
        batches: dl.batches,
        missingIds: dl.missingIds.slice(0, 1000),
        duplicateIds: dl.duplicateIds.slice(0, 1000),
        sha256: dl.sha256,
      },
      null,
      2,
    ),
    'utf8',
  );

  console.log(`\nSnapshot written: ${snapshotId}`);
  console.log(`  path:            ${dir}`);
  console.log(`  status:          ${meta.status}`);
  console.log(`  links:           ${splitLinks.length} (from ${links.length} source links)`);
  console.log(`  arcs:            ${meta.arcCount}`);
  console.log(`  nodes:           ${meta.nodeCount}`);
  console.log(`  components:      ${built.graph.componentCount}`);
  console.log(`  turn restrict.:  ${resolvedTurns.length}`);
  console.log(`  named links:     ${splitLinks.filter((l) => l.roadName).length}`);
  console.log(`  urban/rural cov: ${splitLinks.filter((l) => l.speedSource === "estimated_urban_rural").length}`);
  for (const n of notes) console.log(`  note: ${n}`);
}

main().catch((err) => {
  console.error('\ningest failed:', err);
  process.exitCode = 1;
});
