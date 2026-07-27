/**
 * Wire types for the detour API.
 *
 * Pure types only — the runtime client lives in ./client.ts. Keeping them apart
 * means a component can import a shape without pulling `fetch` into its module
 * graph, and it makes the client the single place request behaviour is defined.
 */

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
  /**
   * The stranded links themselves.
   *
   * Null when there is nothing stranded, or when the set is too large to ship
   * — in which case the counts are still exact and the interface must say the
   * extent is not drawn rather than drawing part of it.
   *
   * Never a polygon. The engine identifies links that lose connectivity, not a
   * service area, and a hull around them would claim an extent the analysis
   * does not compute.
   */
  linkGeoJson: GeoJSON.FeatureCollection | null;
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
  tileSchemaVersion?: number;
  /**
   * What this build of the backend can actually do. Optional because the
   * frontend must keep working against a backend that predates it; when it is
   * absent the scenario adapter falls back to a static description. See
   * ./scenario.ts for why the frontend does not read the raw enum.
   */
  capabilities?: {
    closureScopes: string[];
    metrics: string[];
    vehicles: string[];
    /** Bumped when a change invalidates previously computed figures. */
    algorithmVersion: string;
    processingVersion: string;
  };
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

export interface SearchResponse {
  count: number;
  results: LinkSummary[];
}
