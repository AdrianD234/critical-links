/** Typed client for the detour API. */

const BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8787';

export interface LinkSummary {
  linkId: number;
  amdsId: string;
  sourceObjectId: number;
  closureGroupId: string;
  roadName: string | null;
  modelAssetTypeName: string | null;
  surfaceTypeName: string | null;
  assetOwnerOrganisation: number | null;
  rca: string | null;
  lengthM: number;
  oneway: boolean;
  forwardAllowed: boolean;
  reverseAllowed: boolean;
  modeVehicleHeavy: boolean;
  modeEmergency: boolean;
  lifeLineRoute: boolean;
  speedKph: number | null;
  speedSource: string;
  qualityFlags: string[];
  centroid: { lat: number; lon: number };
  inAnalysisArea: boolean;
}

export interface Metrics {
  selectedLinkLengthM: number | null;
  normalPathDistanceM: number | null;
  alternativeDistanceM: number | null;
  addedDistanceVsLinkM: number | null;
  networkPenaltyM: number | null;
  detourRatioVsLink: number | null;
  normalPathTimeS: number | null;
  alternativeTimeS: number | null;
  addedTimeS: number | null;
}

export interface Corridor {
  status: string;
  hopsUpstream: number;
  hopsDownstream: number;
  corridorDistanceM: number | null;
  normalDistanceM: number | null;
  alternativeDistanceM: number | null;
  penaltyM: number | null;
  penaltyTimeS: number | null;
  truncated: boolean;
  exitReachable: boolean;
  meaning: string;
}

export interface Isolation {
  side: 'downstream' | 'upstream' | 'none';
  pocketNodeCount: number;
  pocketLinkCount: number;
  pocketLengthM: number;
  bounded: boolean;
  exact: boolean;
}

export interface DirectionResult {
  direction: 'forward' | 'reverse';
  status: string;
  statusMeaning: string;
  metrics: Metrics;
  corridor: Corridor | null;
  isolation: Isolation | null;
  routeGeoJson: GeoJSON.FeatureCollection | null;
  routeLinkIds: number[];
  removedArcIds: number[];
  qualityFlags: string[];
  errorDetail: string | null;
  runtimeMs: number;
}

export interface DetourResponse {
  snapshotId: string;
  sourceDataset: string;
  retrievedAtUtc: string;
  attribution: string;
  licence: string;
  algorithm: string;
  algorithmVersion: string;
  clippedExtract: boolean;
  cached: boolean;
  calculatedAtUtc: string;
  request: { metric: string; vehicle: string; closureScope: string; directions: string[] };
  selectedLink: LinkSummary;
  closure: {
    scope: string;
    closureGroupId: string;
    removedLinkCount: number;
    removedArcCount: number;
    removedAmdsIds: string[];
    geoJson: GeoJSON.FeatureCollection;
  };
  forward: DirectionResult | null;
  reverse: DirectionResult | null;
  fitBounds: [number, number, number, number] | null;
  limitations: string[];
  permalink: string;
}

export interface NetworkMetadata {
  snapshotId: string;
  sourceDataset: string;
  sourceUrl: string;
  retrievedAtUtc: string;
  attribution: string;
  licence: string;
  snapshotStatus: string;
  clippedExtract: boolean;
  graph: {
    links: number;
    arcs: number;
    nodes: number;
    components: number;
    turnRestrictions: number;
  };
  analysisExtentWgs84: {
    southWest: { lat: number; lon: number };
    northEast: { lat: number; lon: number };
  } | null;
  ingestNotes: string[];
  limitations: string[];
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      detail = (await res.json()).error ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  base: BASE,
  metadata: () => get<NetworkMetadata>('/api/v1/network/metadata'),
  search: (params: { name?: string; amdsId?: string; limit?: number }) => {
    const q = new URLSearchParams();
    if (params.name) q.set('name', params.name);
    if (params.amdsId) q.set('amdsId', params.amdsId);
    q.set('limit', String(params.limit ?? 25));
    return get<{ count: number; results: LinkSummary[] }>(
      `/api/v1/links/search?${q}`,
    );
  },
  detour: (
    id: string | number,
    opts: {
      metric: string;
      vehicle: string;
      closureScope: string;
      direction: string;
    },
  ) => {
    const q = new URLSearchParams({
      metric: opts.metric,
      vehicle: opts.vehicle,
      closure_scope: opts.closureScope,
      direction: opts.direction,
    });
    return get<DetourResponse>(
      `/api/v1/links/${encodeURIComponent(String(id))}/detour?${q}`,
    );
  },
};
