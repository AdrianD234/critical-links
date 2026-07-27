/**
 * Reproducible source discovery for the NZTA AMDS Network Model.
 *
 * Entry point is the published Experience Builder application. The app item is
 * NOT the data - it references a web map, which references the feature service.
 * This walks that chain, then interrogates the service directly, and writes
 * both a machine-readable report and a human-readable summary into a dated
 * snapshot directory.
 *
 *   npm run discover
 *   npm run discover -- --refresh-projection-truth
 */

import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

import { config, DEFAULT_ATTRIBUTION } from '../lib/config.js';
import { fetchJson, getCount, getLayerMeta, queryUrl } from '../lib/arcgis.js';

const GUID_RE = /[0-9a-f]{32}/gi;

interface ItemReport {
  id: string;
  title?: string;
  type?: string;
  owner?: string;
  url?: string;
  licenceInfo?: string;
  accessInformation?: string;
  modified?: string;
  numViews?: number;
  referencedItemIds: string[];
  error?: string;
}

async function inspectItem(id: string): Promise<ItemReport> {
  try {
    const meta = await fetchJson<any>(
      `${config.sharingApi}/content/items/${id}?f=pjson`,
      { label: `item ${id}` },
    );
    let referenced: string[] = [];
    try {
      const data = await fetchJson<any>(
        `${config.sharingApi}/content/items/${id}/data?f=pjson`,
        { label: `item ${id} data`, retries: 2 },
      );
      const text = JSON.stringify(data ?? {});
      referenced = [...new Set(text.match(GUID_RE) ?? [])].filter((g) => g !== id);
    } catch {
      // Items with no /data payload (e.g. feature services) are normal.
    }
    return {
      id,
      title: meta.title,
      type: meta.type,
      owner: meta.owner,
      url: meta.url,
      licenceInfo: stripHtml(meta.licenseInfo),
      accessInformation: meta.accessInformation,
      modified: meta.modified ? new Date(meta.modified).toISOString() : undefined,
      numViews: meta.numViews,
      referencedItemIds: referenced,
    };
  } catch (err) {
    return {
      id,
      referencedItemIds: [],
      error: err instanceof Error ? err.message : String(err),
    };
  }
}

function stripHtml(s: string | undefined | null): string | undefined {
  if (!s) return undefined;
  return s
    .replace(/<[^>]*>/g, ' ')
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/\s+/g, ' ')
    .trim();
}

/** Fields we care about when classifying a layer for routing suitability. */
function classifyFields(fields: { name: string; type: string }[]) {
  const names = fields.map((f) => f.name);
  const has = (re: RegExp) => names.filter((n) => re.test(n));
  return {
    suspectedStableId: has(/^amdsID/i),
    suspectedSourceTargetNode: has(/(fromnode|tonode|source|target|startnode|endnode)/i),
    directionality: has(/(oneway|direction|flow)/i),
    modeAccess: has(/^mode/i),
    restriction: has(/(restrict|prohibit|ban)/i),
    speed: has(/(speed|kph|kmh|limit)/i),
    zLevel: has(/(zlevel|z_level|grade|elevation|level)/i),
    roadName: has(/(name|route|road)/i),
    authority: has(/(authority|rca|owner|organisation)/i),
  };
}

async function main(): Promise<void> {
  const startedAt = new Date().toISOString();
  const dateDir = startedAt.slice(0, 10);
  const outDir = path.join(config.dataDir, 'source-metadata', 'amds', dateDir);
  await mkdir(outDir, { recursive: true });

  console.log('AMDS source discovery');
  console.log(`  output: ${outDir}\n`);

  // --- 1. walk the item graph from the published application ----------------
  const visited = new Map<string, ItemReport>();
  const queue = [config.amds.experienceItemId];
  while (queue.length > 0) {
    const id = queue.shift()!;
    if (visited.has(id)) continue;
    const rep = await inspectItem(id);
    visited.set(id, rep);
    console.log(
      `  item ${id}  ${rep.type ?? 'ERROR'}  ${rep.title ?? rep.error ?? ''}`,
    );
    // Only follow ids that resolve to real items, and only one level past a map.
    for (const ref of rep.referencedItemIds) {
      if (!visited.has(ref) && visited.size < 30) queue.push(ref);
    }
  }

  // --- 2. the feature service itself ---------------------------------------
  const serviceMeta = await fetchJson<any>(`${config.amds.serviceUrl}?f=json`, {
    label: 'feature service',
  });
  const serviceItem = await inspectItem(config.amds.itemId);

  const layerIds: number[] = [
    ...(serviceMeta.layers ?? []).map((l: any) => l.id),
    ...(serviceMeta.tables ?? []).map((t: any) => t.id),
  ];

  const layers = [] as any[];
  for (const id of layerIds) {
    const m = await getLayerMeta(config.amds.serviceUrl, id);
    let count: number | null = null;
    try {
      count = await getCount(config.amds.serviceUrl, id, { where: '1=1' });
    } catch (err) {
      count = null;
    }
    layers.push({
      id: m.id,
      name: m.name,
      type: m.type,
      geometryType: m.geometryType ?? null,
      objectIdField: m.objectIdField,
      globalIdField: m.globalIdField ?? null,
      maxRecordCount: m.maxRecordCount,
      supportsPagination: m.supportsPagination,
      supportsStatistics: m.supportsStatistics,
      featureCount: count,
      spatialReference: m.extent?.spatialReference ?? null,
      fieldCount: m.fields.length,
      fields: m.fields.map((f) => ({
        name: f.name,
        type: f.type.replace('esriFieldType', ''),
        alias: f.alias,
        codedValues: f.domain?.codedValues
          ? Object.fromEntries(
              f.domain.codedValues.map((c: any) => [c.code, c.name]),
            )
          : undefined,
      })),
      classification: classifyFields(m.fields),
    });
    console.log(
      `  layer ${String(id).padStart(2)}  ${m.name.padEnd(52)} ${
        count === null ? '?' : count
      }`,
    );
  }

  // --- 3. routable-subset profiling on the network layer --------------------
  const L = config.amds.linkLayerId;
  const profileWheres = [
    '1=1',
    'status=1',
    'status=1 AND modeVehicle=1',
    'status=1 AND modeVehicle=1 AND modelAssetType=1',
    'status=1 AND modeVehicle=1 AND oneway=1',
    'status=1 AND modeVehicle=1 AND oneway IS NULL',
    'status=1 AND modeVehicleHeavy=1',
    'status=1 AND modeEmergencyManagement=1',
    'status=1 AND modeFerry=1',
    'status=1 AND assetOwnerOrganisation=1',
  ];
  const profile: Record<string, number | string> = {};
  for (const w of profileWheres) {
    try {
      profile[w] = await getCount(config.amds.serviceUrl, L, { where: w });
    } catch (err) {
      profile[w] = `ERROR: ${err instanceof Error ? err.message : String(err)}`;
    }
  }

  // --- 4. can we actually extract? -----------------------------------------
  const capabilities: string = serviceMeta.capabilities ?? '';
  const extractionProbe: Record<string, unknown> = {
    capabilities,
    declaresExtract: capabilities.includes('Extract'),
  };
  try {
    const ids = await fetchJson<any>(
      queryUrl(config.amds.serviceUrl, L, {
        where: 'status=1 AND modeVehicle=1 AND assetOwnerOrganisation=1',
        returnIdsOnly: true,
        f: 'json',
      }),
      { label: 'id-list probe' },
    );
    extractionProbe.returnIdsOnlySupported = Array.isArray(ids.objectIds);
    extractionProbe.idListSampleSize = ids.objectIds?.length ?? 0;
  } catch (err) {
    extractionProbe.returnIdsOnlySupported = false;
    extractionProbe.idListError = err instanceof Error ? err.message : String(err);
  }
  try {
    const sample = await fetchJson<any>(
      queryUrl(config.amds.serviceUrl, L, {
        where: '1=1',
        outFields: 'amdsIDNetworkModel',
        returnGeometry: true,
        outSR: 2193,
        resultRecordCount: 1,
        f: 'json',
      }),
      { label: 'outSR probe' },
    );
    extractionProbe.nativeOutSR2193 =
      sample.spatialReference?.latestWkid === 2193 ||
      sample.spatialReference?.wkid === 2193;
  } catch (err) {
    extractionProbe.nativeOutSR2193 = false;
  }

  // --- 5. write reports -----------------------------------------------------
  const report = {
    discoveredAtUtc: startedAt,
    entryPoints: {
      nztaPage:
        'https://www.nzta.govt.nz/roads-and-rail/asset-management-data-standard/amds-network-model',
      experienceApp: `https://experience.arcgis.com/experience/${config.amds.experienceItemId}`,
    },
    itemGraph: [...visited.values()],
    featureService: {
      url: config.amds.serviceUrl,
      itemId: config.amds.itemId,
      itemTitle: serviceItem.title,
      owner: serviceItem.owner,
      licenceInfo: serviceItem.licenceInfo ?? null,
      accessInformation: serviceItem.accessInformation ?? null,
      currentVersion: serviceMeta.currentVersion,
      capabilities,
      maxRecordCount: serviceMeta.maxRecordCount,
      copyrightText: serviceMeta.copyrightText || null,
      spatialReference: serviceMeta.spatialReference ?? null,
    },
    layers,
    routableProfile: profile,
    extractionProbe,
    attributionUsed: DEFAULT_ATTRIBUTION,
  };

  await writeFile(
    path.join(outDir, 'discovery-report.json'),
    JSON.stringify(report, null, 2),
    'utf8',
  );
  await writeFile(
    path.join(outDir, 'feature-service.raw.json'),
    JSON.stringify(serviceMeta, null, 2),
    'utf8',
  );
  await writeFile(path.join(outDir, 'summary.md'), renderSummary(report), 'utf8');

  console.log(`\nWrote discovery-report.json, feature-service.raw.json, summary.md`);
  console.log(`Network layer ${L} feature count: ${profile['1=1']}`);
  console.log(`Vehicle-routable current links:  ${profile['status=1 AND modeVehicle=1']}`);
}

function renderSummary(r: any): string {
  const lines: string[] = [];
  lines.push('# AMDS source discovery summary\n');
  lines.push(`Discovered at: ${r.discoveredAtUtc}\n`);
  lines.push('## Item chain\n');
  lines.push('| item id | type | title |');
  lines.push('| --- | --- | --- |');
  for (const it of r.itemGraph) {
    lines.push(`| \`${it.id}\` | ${it.type ?? 'ERROR'} | ${it.title ?? it.error ?? ''} |`);
  }
  lines.push('\n## Feature service\n');
  lines.push(`- URL: ${r.featureService.url}`);
  lines.push(`- Item id: \`${r.featureService.itemId}\``);
  lines.push(`- Owner: ${r.featureService.owner}`);
  lines.push(`- Capabilities: \`${r.featureService.capabilities}\``);
  lines.push(`- maxRecordCount: ${r.featureService.maxRecordCount}`);
  lines.push(`- Licence info: ${r.featureService.licenceInfo ?? '(none published on item)'}`);
  lines.push(`- Access information: ${r.featureService.accessInformation ?? '(none)'}`);
  lines.push('\n## Layers and tables\n');
  lines.push('| id | name | geometry | features | fields |');
  lines.push('| --- | --- | --- | --- | --- |');
  for (const l of r.layers) {
    lines.push(
      `| ${l.id} | ${l.name} | ${l.geometryType ?? 'table'} | ${l.featureCount ?? '?'} | ${l.fieldCount} |`,
    );
  }
  lines.push('\n## Routable subset profile\n');
  lines.push('| where | count |');
  lines.push('| --- | --- |');
  for (const [k, v] of Object.entries(r.routableProfile)) {
    lines.push(`| \`${k}\` | ${v} |`);
  }
  lines.push('\n## Extraction probe\n');
  lines.push('```json');
  lines.push(JSON.stringify(r.extractionProbe, null, 2));
  lines.push('```');
  return lines.join('\n') + '\n';
}

main().catch((err) => {
  console.error('discovery failed:', err);
  process.exitCode = 1;
});
