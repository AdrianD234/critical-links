/**
 * Canonical domain types for the NZ road criticality model.
 *
 * Terminology (fixed, do not vary):
 *   line string / polyline - the source geometry of one physical road record
 *   link                   - one physical source record (AMDS Network Model feature)
 *   arc                    - one DIRECTED traversal of a link (a link yields 1 or 2 arcs)
 *   node                   - a graph vertex where arc endpoints meet
 *   closure group          - the set of links removed together when a road is "closed"
 */

/** Outcome of a detour calculation. These are never conflated. */
export type DetourStatus =
  | 'OK'
  | 'DISCONNECTED'
  | 'UNRESOLVED_TIMEOUT'
  | 'INVALID_GRAPH'
  | 'SOURCE_DATA_ERROR'
  | 'UNSUPPORTED_PROFILE'
  | 'API_ERROR';

export type Metric = 'distance' | 'time';
export type VehicleProfile = 'car' | 'heavy' | 'emergency';
export type ClosureScope = 'physical' | 'directed';
export type Direction = 'forward' | 'reverse';

/** AMDS `status` domain. Only `Current` is loaded into the routable graph. */
export const AMDS_STATUS = {
  1: 'Current',
  2: 'Retired',
  3: 'Future',
  4: 'Proposed/Indicative',
  5: 'Under Review',
  0: 'Under Review',
} as const;

/** AMDS `modelAssetType` domain. */
export const AMDS_MODEL_ASSET_TYPE = {
  1: 'Roadway',
  2: 'Pathway - Unformed',
  3: 'Pathway - Formed',
  4: 'Railway',
  5: 'Waterway',
  6: 'Connector',
  7: 'Railway - Yard Track',
  8: 'Railway - Crossover',
} as const;

/** AMDS `oneway` domain. */
export const AMDS_ONEWAY = { 1: 'Oneway', 2: 'Both Directions' } as const;

/** AMDS `surfaceType` domain. */
export const AMDS_SURFACE_TYPE = {
  1: 'Sealed',
  2: 'Metalled',
  3: 'Unsurfaced',
  4: 'Rail',
  5: 'Water',
} as const;

/**
 * A physical source link, as ingested. Geometry lives in a side-car buffer
 * (see GeometryStore) so that attribute records stay small.
 */
export interface LinkRecord {
  /** Dense internal index, stable only within one snapshot. */
  linkId: number;
  /** Durable AMDS identifier, e.g. "{050745fb-...}". This is the canonical ID. */
  amdsId: string;
  /** ArcGIS OBJECTID. Recorded for traceability only; NOT durable. */
  objectId: number;
  /** Closure group - links removed together for a physical closure. */
  closureGroupId: string;
  roadName: string | null;
  /** AMDS authority id of the asset owner (RCA). */
  assetOwnerOrganisation: number | null;
  dataManagingOrganisation: number | null;
  amdsIDAuthority: string | null;
  modelAssetType: number | null;
  surfaceType: number | null;
  status: number | null;
  oneway: number | null;
  /** Length in metres, computed from the polyline in EPSG:2193. */
  lengthM: number;
  /** `Shape__Length` as published by the service, for cross-check only. */
  sourceLengthM: number | null;
  forwardAllowed: boolean;
  reverseAllowed: boolean;
  modeVehicle: boolean;
  modeVehicleHeavy: boolean;
  modeEmergency: boolean;
  modeFerry: boolean;
  lifeLineRoute: boolean;
  sharedInfrastructure: boolean;
  detourAvailableFlag: boolean;
  /** Speed used for the time metric, and where it came from. */
  speedKph: number | null;
  speedSource: SpeedSource;
  qualityFlags: string[];
  sourceNode: number;
  targetNode: number;
}

export type SpeedSource =
  | 'none'
  /** Fallback from modelAssetType / surfaceType / owning authority only. */
  | 'estimated_asset_type'
  /** Derived from the AMDS UrbanRural table - better grounded, still an estimate. */
  | 'estimated_urban_rural'
  /** Actual posted limit from the National Speed Limit Register. Not yet wired. */
  | 'nslr';

/** One directed traversal of a link. */
export interface ArcRecord {
  arcId: number;
  linkId: number;
  from: number;
  to: number;
  direction: Direction;
  costDistanceM: number;
  costTimeS: number;
  timeCostValid: boolean;
}

/**
 * A prohibited manoeuvre: traversing `linkSeq` in order is banned for the
 * listed modes. AMDS supplies up to 8 links per restriction.
 */
export interface TurnRestriction {
  amdsIDRestrictedTurn: string;
  /** Internal link ids, in traversal order. Length >= 2. */
  linkSeq: number[];
  restrictedVehicle: boolean;
  restrictedVehicleHeavy: boolean;
  restrictedEmergency: boolean;
}

export interface SnapshotMeta {
  snapshotId: string;
  sourceDataset: string;
  sourceVersion: string | null;
  retrievedAtUtc: string;
  sourceUrl: string;
  layerId: number;
  licence: string;
  attribution: string;
  /** SHA-256 of the concatenated raw payload pages. */
  rawSha256: string;
  processingVersion: string;
  /** Feature count reported by the service at extraction time. */
  sourceFeatureCount: number;
  /** Features actually downloaded. */
  downloadedFeatureCount: number;
  /** Links admitted to the routable graph after filtering. */
  routableLinkCount: number;
  arcCount: number;
  nodeCount: number;
  /** Extent filter applied at extraction, in EPSG:2193, or null for national. */
  extent: Bbox2193 | null;
  /**
   * Analysis area. Links outside this but inside `extent` form a network buffer
   * that keeps near-edge detours valid. Results whose route touches the buffer
   * edge are flagged BOUNDARY_AFFECTED.
   */
  analysisExtent: Bbox2193 | null;
  where: string;
  status: 'complete' | 'partial' | 'failed';
  notes: string[];
}

export interface Bbox2193 {
  xmin: number;
  ymin: number;
  xmax: number;
  ymax: number;
}

export interface DetourMetrics {
  selectedLinkLengthM: number;
  normalPathDistanceM: number | null;
  alternativeDistanceM: number | null;
  addedDistanceVsLinkM: number | null;
  networkPenaltyM: number | null;
  detourRatioVsLink: number | null;
  normalPathTimeS: number | null;
  alternativeTimeS: number | null;
  addedTimeS: number | null;
}

export interface DetourDirectionResult extends DetourMetrics {
  direction: Direction;
  status: DetourStatus;
  sourceNode: number;
  targetNode: number;
  /**
   * Arcs excluded for THIS direction. Under `physical` scope this is the whole
   * closure group; under `directed` scope it is the single arc traversing the
   * selected link in this direction.
   */
  removedArcIds: number[];
  /** Ordered arc ids of the alternative route. */
  routeArcIds: number[];
  /** Ordered link ids of the alternative route (deduplicated consecutively). */
  routeLinkIds: number[];
  qualityFlags: string[];
  errorDetail: string | null;
  runtimeMs: number;
  /** Nodes settled by the search - a proxy for query cost. */
  nodesExplored: number;
  /**
   * Corridor-level replacement path, measured between the nearest upstream and
   * downstream points at which a driver has a genuine choice. Answers "how much
   * longer is a through trip" where the endpoint measure answers "can I get
   * from this link's start back to its end". Populated when the endpoint
   * measure returns DISCONNECTED, which on one-way carriageways is routine and
   * says little about real disruption. See packages/core/src/corridor.ts.
   */
  corridor: CorridorSummary | null;
  /**
   * When no replacement path exists, how much of the network is cut off.
   * Distinguishes a stranded driveway from a stranded settlement.
   */
  isolation: IsolationSummary | null;
}

export interface IsolationSummary {
  side: 'downstream' | 'upstream' | 'none';
  pocketNodeCount: number;
  pocketLinkCount: number;
  pocketLengthM: number;
  bounded: boolean;
  exact: boolean;
}

export interface CorridorSummary {
  status: DetourStatus;
  entryNode: number;
  exitNode: number;
  hopsUpstream: number;
  hopsDownstream: number;
  corridorDistanceM: number | null;
  normalDistanceM: number | null;
  alternativeDistanceM: number | null;
  penaltyM: number | null;
  normalTimeS: number | null;
  alternativeTimeS: number | null;
  penaltyTimeS: number | null;
  truncated: boolean;
  exitReachable: boolean;
  detail: string | null;
}

export interface DetourResult {
  snapshotId: string;
  linkId: number;
  amdsId: string;
  closureGroupId: string;
  vehicleProfile: VehicleProfile;
  metric: Metric;
  closureScope: ClosureScope;
  /** Every arc removed for this closure. */
  removedArcIds: number[];
  removedLinkIds: number[];
  removedAmdsIds: string[];
  forward: DetourDirectionResult | null;
  reverse: DetourDirectionResult | null;
  algorithm: string;
  algorithmVersion: string;
  calculatedAtUtc: string;
}

/** Algorithm identity. Bump when routing semantics change - this invalidates caches. */
export const ALGORITHM = 'astar-arc-expanded';
export const ALGORITHM_VERSION = '1.0.0';
export const PROCESSING_VERSION = '1.0.0';
