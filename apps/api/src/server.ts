/**
 * Detour analysis API.
 *
 *   npm run api
 *
 * The snapshot is loaded once at start-up and held in memory. Every response
 * carries its provenance - snapshot id, source dataset, retrieval time,
 * attribution and the known limitations that apply to the numbers returned -
 * so a figure can never be lifted out of the UI without the caveats that
 * belong to it.
 */

import Fastify from 'fastify';
import cors from '@fastify/cors';
import { readdir } from 'node:fs/promises';
import path from 'node:path';

import {
  ALGORITHM,
  ALGORITHM_VERSION,
  AMDS_MODEL_ASSET_TYPE,
  AMDS_SURFACE_TYPE,
  DetourEngine,
  LruCache,
  detourCacheKey,
  latLonToNztm,
  loadSnapshot,
  nztmToLatLon,
  type ClosureScope,
  type Direction,
  type DetourResult,
  type LinkRecord,
  type Metric,
  type VehicleProfile,
} from '@nzcl/core';

import { bboxOf, linkFeature, routeFeatures, type Feature } from './geojson.js';
import { openApiDocument } from './openapi.js';
import { buildTileIndex, type TileIndex } from './tiles.js';

const DATA_DIR = path.resolve(process.env.DATA_DIR ?? './data');
const PORT = Number(process.env.API_PORT ?? 8787);
const WEB_ORIGIN = process.env.APPLICATION_BASE_URL ?? 'http://localhost:5173';

/**
 * Limitations that apply to every number this service returns. Repeated in
 * each detour response on purpose - a caller should not have to go looking.
 */
const LIMITATIONS = [
  'Structural analysis only. This is a shortest replacement path, NOT a traffic assignment: it does not predict how much traffic uses each alternative route, and no origin-destination demand, capacity or congestion model is involved.',
  'AMDS publishes no speed attribute. Time results are derived from estimated speeds (urban/rural classification where available, otherwise asset type and ownership) and are flagged TIME_ESTIMATED. Distance is the defensible metric.',
  'AMDS publishes only 60 restricted turns nationally. Banned-turn coverage is effectively negligible, so routes through complex intersections must not be presented as road-legal.',
  'Junctions are inferred where one link ends on the interior of another, because AMDS does not split through roads at side roads. Interior-to-interior crossings are never noded, which preserves grade separation.',
  'Height, weight and other physical restrictions are recorded as link quality flags but do not yet constrain routing.',
];

interface Ctx {
  snapshot: Awaited<ReturnType<typeof loadSnapshot>>;
  engine: DetourEngine;
  byAmdsId: Map<string, number>;
  cache: LruCache<DetourResult>;
  tiles: TileIndex;
  loadedAtUtc: string;
}

let ctx: Ctx;

async function listSnapshots(): Promise<string[]> {
  try {
    const entries = await readdir(path.join(DATA_DIR, 'processed'), {
      withFileTypes: true,
    });
    return entries.filter((e) => e.isDirectory()).map((e) => e.name).sort();
  } catch {
    return [];
  }
}

function linkSummary(link: LinkRecord, s: Ctx): Record<string, unknown> {
  const coords = s.snapshot.graph.linkCoords(link.linkId);
  const mid = nztmToLatLon(
    coords[Math.floor(coords.length / 4) * 2],
    coords[Math.floor(coords.length / 4) * 2 + 1],
  );
  return {
    linkId: link.linkId,
    amdsId: link.amdsId,
    sourceObjectId: link.objectId,
    closureGroupId: link.closureGroupId,
    roadName: link.roadName,
    modelAssetType: link.modelAssetType,
    modelAssetTypeName:
      (AMDS_MODEL_ASSET_TYPE as any)[link.modelAssetType ?? -1] ?? null,
    surfaceType: link.surfaceType,
    surfaceTypeName: (AMDS_SURFACE_TYPE as any)[link.surfaceType ?? -1] ?? null,
    assetOwnerOrganisation: link.assetOwnerOrganisation,
    rca: link.assetOwnerOrganisation === 1 ? 'NZTA Waka Kotahi (state highway)' : null,
    lengthM: round(link.lengthM, 1),
    oneway: link.oneway === 1,
    forwardAllowed: link.forwardAllowed,
    reverseAllowed: link.reverseAllowed,
    modeVehicle: link.modeVehicle,
    modeVehicleHeavy: link.modeVehicleHeavy,
    modeEmergency: link.modeEmergency,
    lifeLineRoute: link.lifeLineRoute,
    speedKph: link.speedKph,
    speedSource: link.speedSource,
    qualityFlags: link.qualityFlags,
    sourceNode: link.sourceNode,
    targetNode: link.targetNode,
    centroid: { lat: round(mid.lat, 6), lon: round(mid.lon, 6) },
    inAnalysisArea: s.snapshot.coreLink
      ? s.snapshot.coreLink[link.linkId] === 1
      : true,
  };
}

const round = (v: number | null | undefined, dp: number): number | null =>
  v === null || v === undefined || !Number.isFinite(v)
    ? null
    : Math.round(v * 10 ** dp) / 10 ** dp;

function provenance(s: Ctx) {
  const m = s.snapshot.meta;
  return {
    snapshotId: m.snapshotId,
    sourceDataset: m.sourceDataset,
    sourceUrl: m.sourceUrl,
    retrievedAtUtc: m.retrievedAtUtc,
    licence: m.licence,
    attribution: m.attribution,
    processingVersion: m.processingVersion,
    algorithm: ALGORITHM,
    algorithmVersion: ALGORITHM_VERSION,
    snapshotStatus: m.status,
    clippedExtract: m.extent !== null,
  };
}

function serialiseDirection(d: any, s: Ctx, includeGeometry: boolean) {
  if (!d) return null;
  const routeGeoJson = includeGeometry && d.routeArcIds.length
    ? {
        type: 'FeatureCollection' as const,
        features: routeFeatures(s.snapshot.graph, s.snapshot.links, d.routeArcIds),
      }
    : null;
  return {
    direction: d.direction,
    status: d.status,
    statusMeaning: statusMeaning(d.status),
    sourceNode: d.sourceNode,
    targetNode: d.targetNode,
    metrics: {
      selectedLinkLengthM: round(d.selectedLinkLengthM, 1),
      normalPathDistanceM: round(d.normalPathDistanceM, 1),
      alternativeDistanceM: round(d.alternativeDistanceM, 1),
      addedDistanceVsLinkM: round(d.addedDistanceVsLinkM, 1),
      networkPenaltyM: round(d.networkPenaltyM, 1),
      detourRatioVsLink: round(d.detourRatioVsLink, 3),
      normalPathTimeS: round(d.normalPathTimeS, 1),
      alternativeTimeS: round(d.alternativeTimeS, 1),
      addedTimeS: round(d.addedTimeS, 1),
      units: { distance: 'metres', time: 'seconds' },
    },
    corridor: d.corridor
      ? {
          ...d.corridor,
          corridorDistanceM: round(d.corridor.corridorDistanceM, 1),
          normalDistanceM: round(d.corridor.normalDistanceM, 1),
          alternativeDistanceM: round(d.corridor.alternativeDistanceM, 1),
          penaltyM: round(d.corridor.penaltyM, 1),
          normalTimeS: round(d.corridor.normalTimeS, 1),
          alternativeTimeS: round(d.corridor.alternativeTimeS, 1),
          penaltyTimeS: round(d.corridor.penaltyTimeS, 1),
          meaning:
            'Through-trip comparison between the nearest upstream and downstream points at which a driver has a choice. Reported when the link-endpoint measure is undefined, which is routine on one-way carriageways.',
        }
      : null,
    isolation: d.isolation
      ? { ...d.isolation, pocketLengthM: round(d.isolation.pocketLengthM, 1) }
      : null,
    removedArcIds: d.removedArcIds,
    routeArcIds: d.routeArcIds,
    routeLinkIds: d.routeLinkIds,
    routeGeoJson,
    qualityFlags: d.qualityFlags,
    errorDetail: d.errorDetail,
    runtimeMs: d.runtimeMs,
    statesExplored: d.nodesExplored,
  };
}

function statusMeaning(status: string): string {
  switch (status) {
    case 'OK':
      return 'A valid replacement path was found.';
    case 'DISCONNECTED':
      return 'No replacement path exists between the closed link\'s own endpoints. Check the corridor and isolation fields before concluding traffic cannot get past: on a one-way carriageway this result is routine and does not mean the area is cut off.';
    case 'UNRESOLVED_TIMEOUT':
      return 'The search exceeded its budget. This is NOT a finding about the network - the answer is unknown.';
    case 'INVALID_GRAPH':
      return 'The request referenced nodes outside the graph.';
    case 'SOURCE_DATA_ERROR':
      return 'The source link cannot be routed on (for example it starts and ends at the same node).';
    case 'UNSUPPORTED_PROFILE':
      return 'The requested vehicle profile cannot use this link.';
    default:
      return 'An application error occurred. This is not a statement about the network.';
  }
}

function parseEnum<T extends string>(
  value: unknown,
  allowed: readonly T[],
  fallback: T,
  name: string,
): T {
  if (value === undefined || value === null || value === '') return fallback;
  if (typeof value !== 'string' || !allowed.includes(value as T)) {
    throw Object.assign(
      new Error(`invalid ${name}: expected one of ${allowed.join(', ')}`),
      { statusCode: 400 },
    );
  }
  return value as T;
}

async function build() {
  const app = Fastify({ logger: { level: process.env.LOG_LEVEL ?? 'warn' } });
  await app.register(cors, { origin: [WEB_ORIGIN, /localhost:\d+$/] });

  app.get('/health', async () => ({
    status: 'ok',
    snapshotId: ctx.snapshot.meta.snapshotId,
    links: ctx.snapshot.links.length,
    arcs: ctx.snapshot.graph.arcCount,
    nodes: ctx.snapshot.graph.nodeCount,
    loadedAtUtc: ctx.loadedAtUtc,
    cacheEntries: ctx.cache.size,
  }));

  app.get('/openapi.json', async () => openApiDocument());

  // ------------------------------------------------------------ vector tiles
  app.get('/tiles/tilejson.json', async () => ({
    tilejson: '2.2.0',
    name: `AMDS routable network - ${ctx.snapshot.meta.snapshotId}`,
    attribution: ctx.snapshot.meta.attribution,
    scheme: 'xyz',
    minzoom: 0,
    maxzoom: ctx.tiles.maxZoom,
    tiles: [`http://localhost:${PORT}/tiles/{z}/{x}/{y}.pbf`],
    vector_layers: [
      {
        id: 'network',
        fields: {
          linkId: 'Number',
          amdsId: 'String',
          roadName: 'String',
          oneway: 'Number',
          stateHighway: 'Number',
          core: 'Number',
        },
      },
    ],
  }));

  app.get('/tiles/:z/:x/:y.pbf', async (req, reply) => {
    const p = req.params as any;
    const z = Number(p.z);
    const x = Number(p.x);
    const y = Number(p['y.pbf'] ?? p.y);
    if (!Number.isInteger(z) || !Number.isInteger(x) || !Number.isInteger(y)) {
      return reply.status(400).send({ error: 'tile coordinates must be integers' });
    }
    if (z < 0 || z > 22 || x < 0 || y < 0 || x >= 2 ** z || y >= 2 ** z) {
      return reply.status(400).send({ error: 'tile coordinates out of range' });
    }
    const buf = ctx.tiles.getTile(z, x, y);
    reply
      .header('Content-Type', 'application/x-protobuf')
      .header('Cache-Control', 'public, max-age=3600');
    // 204 is the conventional "this tile is empty" response for MapLibre.
    return buf ? reply.send(buf) : reply.status(204).send();
  });

  app.get('/api/v1/network/snapshots', async () => ({
    available: await listSnapshots(),
    active: ctx.snapshot.meta.snapshotId,
  }));

  app.get('/api/v1/network/metadata', async () => {
    const m = ctx.snapshot.meta;
    const g = ctx.snapshot.graph;
    const ext = m.analysisExtent;
    return {
      ...provenance(ctx),
      where: m.where,
      sourceFeatureCount: m.sourceFeatureCount,
      downloadedFeatureCount: m.downloadedFeatureCount,
      graph: {
        links: ctx.snapshot.links.length,
        arcs: g.arcCount,
        nodes: g.nodeCount,
        components: g.componentCount,
        turnRestrictions: ctx.snapshot.restrictions.length,
      },
      extent2193: m.extent,
      analysisExtent2193: ext,
      analysisExtentWgs84: ext
        ? {
            southWest: nztmToLatLon(ext.xmin, ext.ymin),
            northEast: nztmToLatLon(ext.xmax, ext.ymax),
          }
        : null,
      ingestNotes: m.notes,
      limitations: LIMITATIONS,
    };
  });

  // ---------------------------------------------------------------- search
  app.get('/api/v1/links/search', async (req) => {
    const q = req.query as Record<string, string | undefined>;
    const limit = Math.min(Math.max(Number(q.limit ?? 50) || 50, 1), 500);
    const name = (q.name ?? q.q ?? '').trim().toLowerCase();
    const amdsId = (q.amdsId ?? '').trim();
    const rca = q.rca ? Number(q.rca) : null;

    let bbox: { xmin: number; ymin: number; xmax: number; ymax: number } | null = null;
    if (q.bbox) {
      const p = q.bbox.split(',').map(Number);
      if (p.length !== 4 || p.some((n) => !Number.isFinite(n))) {
        throw Object.assign(new Error('bbox must be minLon,minLat,maxLon,maxLat'), {
          statusCode: 400,
        });
      }
      const a = latLonToNztm(p[1], p[0]);
      const b = latLonToNztm(p[3], p[2]);
      bbox = {
        xmin: Math.min(a.x, b.x),
        ymin: Math.min(a.y, b.y),
        xmax: Math.max(a.x, b.x),
        ymax: Math.max(a.y, b.y),
      };
    }

    const out: LinkRecord[] = [];
    for (const l of ctx.snapshot.links) {
      if (amdsId && !l.amdsId.includes(amdsId)) continue;
      if (name && !(l.roadName ?? '').toLowerCase().includes(name)) continue;
      if (rca !== null && l.assetOwnerOrganisation !== rca) continue;
      if (bbox) {
        const c = ctx.snapshot.graph.linkCoords(l.linkId);
        let hit = false;
        for (let i = 0; i < c.length; i += 2) {
          if (
            c[i] >= bbox.xmin && c[i] <= bbox.xmax &&
            c[i + 1] >= bbox.ymin && c[i + 1] <= bbox.ymax
          ) {
            hit = true;
            break;
          }
        }
        if (!hit) continue;
      }
      out.push(l);
      if (out.length >= limit) break;
    }
    return {
      snapshotId: ctx.snapshot.meta.snapshotId,
      count: out.length,
      truncated: out.length >= limit,
      results: out.map((l) => linkSummary(l, ctx)),
    };
  });

  // ------------------------------------------------------------ link detail
  app.get('/api/v1/links/:id', async (req) => {
    const link = resolveLink((req.params as any).id);
    return {
      ...provenance(ctx),
      link: linkSummary(link, ctx),
      closureGroupMembers: ctx.snapshot.links
        .filter((l) => l.closureGroupId === link.closureGroupId)
        .map((l) => ({ linkId: l.linkId, amdsId: l.amdsId, lengthM: round(l.lengthM, 1) })),
      geoJson: {
        type: 'FeatureCollection',
        features: [linkFeature(ctx.snapshot.graph, link, { roadName: link.roadName })],
      },
    };
  });

  // ---------------------------------------------------------------- detour
  app.get('/api/v1/links/:id/detour', async (req) => {
    const q = req.query as Record<string, string | undefined>;
    const link = resolveLink((req.params as any).id);

    const metric = parseEnum<Metric>(q.metric, ['distance', 'time'], 'distance', 'metric');
    const vehicle = parseEnum<VehicleProfile>(
      q.vehicle, ['car', 'heavy', 'emergency'], 'car', 'vehicle',
    );
    const closureScope = parseEnum<ClosureScope>(
      q.closure_scope ?? q.closureScope, ['physical', 'directed'], 'physical', 'closure_scope',
    );
    const dirParam = parseEnum(q.direction, ['forward', 'reverse', 'both'], 'both', 'direction');
    const directions: Direction[] =
      dirParam === 'both'
        ? ([
            link.forwardAllowed ? 'forward' : null,
            link.reverseAllowed ? 'reverse' : null,
          ].filter(Boolean) as Direction[])
        : [dirParam as Direction];

    const key = detourCacheKey({
      snapshotId: ctx.snapshot.meta.snapshotId,
      linkId: link.linkId,
      closureScope,
      directions,
      profile: vehicle,
      metric,
    });
    let result = ctx.cache.get(key);
    const cached = result !== undefined;
    if (!result) {
      result = ctx.engine.compute({
        linkId: link.linkId,
        metric,
        profile: vehicle,
        closureScope,
        directions,
      });
      ctx.cache.set(key, result);
    }

    const includeGeometry = q.geometry !== 'false';
    const forward = serialiseDirection(result.forward, ctx, includeGeometry);
    const reverse = serialiseDirection(result.reverse, ctx, includeGeometry);

    const closedFeatures: Feature[] = result.removedLinkIds.map((id) =>
      linkFeature(ctx.snapshot.graph, ctx.snapshot.links[id], {
        role: 'closed',
        roadName: ctx.snapshot.links[id].roadName,
      }),
    );
    const allFeatures = [
      ...closedFeatures,
      ...((forward?.routeGeoJson?.features ?? []) as Feature[]),
      ...((reverse?.routeGeoJson?.features ?? []) as Feature[]),
    ];

    return {
      ...provenance(ctx),
      request: { metric, vehicle, closureScope, directions },
      cached,
      calculatedAtUtc: result.calculatedAtUtc,
      selectedLink: linkSummary(link, ctx),
      closure: {
        scope: closureScope,
        closureGroupId: result.closureGroupId,
        removedLinkCount: result.removedLinkIds.length,
        removedArcCount: result.removedArcIds.length,
        removedLinkIds: result.removedLinkIds,
        removedAmdsIds: result.removedAmdsIds,
        geoJson: { type: 'FeatureCollection', features: closedFeatures },
      },
      forward,
      reverse,
      fitBounds: bboxOf(allFeatures),
      limitations: LIMITATIONS,
      permalink:
        `${WEB_ORIGIN}/?link=${encodeURIComponent(link.amdsId)}` +
        `&snapshot=${encodeURIComponent(ctx.snapshot.meta.snapshotId)}` +
        `&metric=${metric}&vehicle=${vehicle}&scope=${closureScope}&direction=${dirParam}`,
    };
  });

  // ------------------------------------------------------------------- qa
  app.get('/api/v1/qa/summary', async () => {
    const fs = await import('node:fs/promises');
    const p = path.join(
      DATA_DIR, 'processed', ctx.snapshot.meta.snapshotId, 'qa-report.json',
    );
    try {
      return JSON.parse(await fs.readFile(p, 'utf8'));
    } catch {
      return {
        error: 'QA report not generated for this snapshot',
        hint: `run: npm run qa -- ${ctx.snapshot.meta.snapshotId}`,
      };
    }
  });

  app.setErrorHandler((err, _req, reply) => {
    const status = (err as any).statusCode ?? 500;
    reply.status(status).send({
      error: err.message,
      // A transport or application failure is never a network finding.
      status: status === 400 ? 'BAD_REQUEST' : 'API_ERROR',
    });
  });

  return app;
}

function resolveLink(id: string): LinkRecord {
  const decoded = decodeURIComponent(id);
  const byId = ctx.byAmdsId.get(decoded);
  if (byId !== undefined) return ctx.snapshot.links[byId];
  const n = Number(decoded);
  if (Number.isInteger(n) && n >= 0 && n < ctx.snapshot.links.length) {
    return ctx.snapshot.links[n];
  }
  throw Object.assign(new Error(`unknown link "${decoded}"`), { statusCode: 404 });
}

async function main() {
  const requested = process.env.SNAPSHOT_ID;
  const available = await listSnapshots();
  if (available.length === 0) {
    console.error(
      `No snapshots in ${path.join(DATA_DIR, 'processed')}.\n` +
        `Run:  npm run ingest -- --pilot wellington`,
    );
    process.exit(1);
  }
  const snapshotId = requested ?? available[available.length - 1];
  console.log(`loading snapshot ${snapshotId}...`);
  const t0 = Date.now();
  const snapshot = await loadSnapshot(DATA_DIR, snapshotId);
  ctx = {
    snapshot,
    engine: new DetourEngine(snapshot.graph, snapshot.links, {
      snapshotId: snapshot.meta.snapshotId,
      coreLink: snapshot.coreLink,
      clipped: snapshot.meta.extent !== null,
    }),
    byAmdsId: new Map(snapshot.links.map((l) => [l.amdsId, l.linkId])),
    cache: new LruCache<DetourResult>(20_000),
    tiles: buildTileIndex(snapshot.graph, snapshot.links, snapshot.coreLink),
    loadedAtUtc: new Date().toISOString(),
  };
  console.log(
    `  ${snapshot.links.length} links, ${snapshot.graph.arcCount} arcs in ${Date.now() - t0} ms`,
  );

  const app = await build();
  await app.listen({ port: PORT, host: '0.0.0.0' });
  console.log(`API listening on http://localhost:${PORT}`);
  console.log(`  OpenAPI: http://localhost:${PORT}/openapi.json`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
