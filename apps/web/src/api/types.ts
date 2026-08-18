/**
 * Wire types for the detour API.
 *
 * Pure types only — the runtime client lives in ./client.ts. Keeping them apart
 * means a component can import a shape without pulling `fetch` into its module
 * graph, and it makes the client the single place request behaviour is defined.
 */

/** How a road came to be called what it is called — or why it is not. */
export type NameStatus =
  | 'amds_named'
  | 'route_designation_only'
  | 'externally_enriched'
  | 'officially_unnamed'
  | 'ambiguous_conflict'
  | 'unresolved';

export interface Naming {
  status: NameStatus;
  /** What to put in the name position when there is no name. */
  label: string | null;
  explanation: string | null;
  source: string | null;
  confidence: string | null;
  /** "State Highway 3". Shown alongside a street name, never instead of one. */
  routeDesignation: string | null;
  alternates: string[];
  conflict: boolean;
  /**
   * Set when a name IS known but is not shown, because that source's licence
   * has not been confirmed. Distinct from having no name at all, and the
   * distinction is the point.
   */
  withheldSource: string | null;
}

/**
 * Which line of the label ladder produced `displayLabel`.
 *
 * `conflict` is what the backend emits for `ambiguous_conflict`; it is listed
 * here because it is what arrives on the wire, not because it mirrors a
 * `NameStatus` value.
 */
export type DisplayLabelKind =
  | 'road_name'
  | 'route_designation'
  | 'officially_unnamed'
  | 'conflict'
  | 'withheld'
  | 'contextual'
  | 'identifier';

/**
 * The authoritative label, and enough provenance to render it honestly.
 *
 * Carried by every link summary, V1 and V2 alike. The client renders
 * `displayLabel` and never rebuilds it; the separate fields exist so a
 * provenance panel can show what is known without re-deriving the decision.
 *
 * Optional throughout, because the app must keep working against a backend
 * that predates them — see ../naming.ts for the fallback ladder.
 */
export interface DisplayLabelFields {
  /** Always non-empty when present. Never "No name". */
  displayLabel?: string;
  displayLabelKind?: DisplayLabelKind;
  /** Plain-English reason this label was chosen. */
  displayLabelBasis?: string;
  /** A short stable id such as "1073a927#12". Context, never the headline. */
  displayLabelSecondary?: string | null;
  /**
   * LINZ's left and right locality for the road section — the two sides of the
   * road, not a value and a fallback. Where they differ the backend composes
   * both into `displayLabel` ("State-highway section between Kinleith and
   * Tokoroa"), so anything showing them apart has to label them as two sides
   * rather than demote the second.
   */
  locality?: string | null;
  localityAlt?: string | null;
  territorialAuthority?: string | null;
}

export interface LinkSummary extends DisplayLabelFields {
  linkId: number;
  amdsId: string;
  sourceObjectId: number;
  closureGroupId: string;
  roadName: string | null;
  naming?: Naming;
  modelAssetTypeName: string | null;
  surfaceTypeName: string | null;
  assetOwnerOrganisation: number | null;
  rca: string | null;
  /** Route number, e.g. "SH 1". Distinguishes roads sharing a name. */
  roadNumber?: string | null;
  urbanRural?: string | null;
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
   * What this snapshot covers, recorded at ingest.
   *
   * The client used to work coverage out from `clippedExtract` and call
   * anything clipped "Wellington pilot" — which would have announced an
   * Auckland extract as Wellington, and could not distinguish a national
   * snapshot from a very large regional one. Optional so the app still runs
   * against a backend that predates it.
   */
  coverage?: {
    kind: 'national' | 'regional' | 'synthetic' | 'unknown';
    name: string;
    /** Where the map sits with nothing selected. Null for national. */
    displayExtentWgs84: {
      southWest: { lat: number; lon: number };
      northEast: { lat: number; lon: number };
    } | null;
    isNational: boolean;
  };
  /** Why the backend is serving this snapshot. Diagnostic, shown on hover. */
  selectionReason?: string | null;
  /**
   * Naming coverage for the whole snapshot.
   *
   * Reported so the application can state how much of the network it can name,
   * rather than leaving a reader to infer it from how many labels they happen
   * to see. `withheldTotal` counts links that HAVE a name which is not being
   * displayed because that source's licence is unconfirmed — a different fact
   * from having no name, and the more actionable of the two.
   */
  naming?: {
    graphLinks: number;
    namedLinks: number;
    byStatus: Record<string, number>;
    withheldBySource: Record<string, number>;
    withheldTotal: number;
  };
  /** Attribution for every non-AMDS source whose names are displayed. */
  nameAttributions?: { source: string; licence: string; attribution: string }[];
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

/* ------------------------------------------------------------------ V2 */

/**
 * The V2 closure-impact engine.
 *
 * A separate set of types rather than additions to the V1 shapes above. V2 is
 * not a superset of V1: it answers a different default question (one segment,
 * not one source feature), reports isolation as an exact undirected property
 * rather than a bounded directed walk, and separates that from the directed
 * "can I still get there" question V1 conflated with it. Merging the two would
 * make it possible to read a V2 figure as though V1 had produced it.
 *
 * V1 remains the default and its types above are untouched.
 */

/** The wire vocabulary. See ./scenario.ts for the product's own. */
export type V2ClosureScope = 'segment' | 'direction' | 'source_feature';

/**
 * The closed set of findings the engine may report.
 *
 * Spelled out as a union rather than `string` so a headline that does not
 * exist cannot compile. The wording is the backend's and is shown verbatim;
 * nothing in the client composes one.
 *
 * "No physical isolation" is deliberately absent. It made a claim about roads,
 * when the only thing the analysis can support is a claim about the graph it
 * was run on — Gu is inferred topology, so the honest statement is "No
 * isolation in the represented physical-access graph".
 *
 * "Network split into two represented components" is the case where a closure
 * does separate the graph but no side carries a decisive anchor. Calling
 * either side "cut off" would assert a direction the data does not support.
 *
 * "Partial analysis" is the case where some requested directions resolved and
 * others did not. Reporting the surviving half as the whole answer is how a
 * timeout turns into a finding about a road.
 */
export type V2Headline =
  | 'Road cut off'
  | 'Network split into two represented components'
  | 'Through route found'
  | 'No endpoint route'
  | 'Directional access loss'
  | 'No isolation in the represented physical-access graph'
  | 'Partial analysis'
  | 'Analysis unresolved';

/**
 * The isolation question alone, with the routing question stripped out.
 *
 * Written as a selection from `V2Headline` rather than as its own list of
 * strings, because it is the same closed vocabulary: two words for one finding
 * would leave a reader reconciling them.
 */
export type V2IsolationStatement = Extract<
  V2Headline,
  | 'Road cut off'
  | 'Network split into two represented components'
  | 'No isolation in the represented physical-access graph'
  | 'Analysis unresolved'
>;

export interface V2Capabilities {
  snapshotId: string;
  engine: 'v2';
  algorithm: string;
  algorithmVersion: string;
  /** How settled this engine is. Shown verbatim; never paraphrased. */
  stability: string;
  derivationVersion: string;
  closureScopes: V2ClosureScope[];
  defaultClosureScope: V2ClosureScope;
  metrics: string[];
  vehicles: string[];
  headlines: V2Headline[];
  /**
   * Profiles whose exact-isolation precompute exists for this snapshot. A
   * profile that is absent is still answered, by building the derivation on
   * demand — correct, but slow enough that the client should say so rather
   * than let it look like a stall.
   */
  physicalAccessReady: string[];
  physicalAccess: {
    profile: string;
    nodeCount: number;
    linkCount: number;
    componentCount: number;
    bridgeCount: number;
    articulationCount: number;
    principalComponentId: number | null;
    principalRule: string | null;
    buildMs: number | null;
    builtAtUtc: string | null;
  }[];
}

/**
 * Raised when the requested scope removed more than the selected segment.
 *
 * Only `source_feature` produces it. An AMDS source feature is a
 * data-maintenance unit, so the extra length is not a rounding difference —
 * it is a different closure from the one the user pointed at, and the figures
 * describe the larger one.
 */
export interface V2ClosureWarning {
  code: string;
  severity: string;
  headline: string;
  detail: string;
  selectedSegmentLengthM: number;
  totalClosureLengthM: number;
  removedLinkCount: number;
}

export interface V2Closure {
  selectedLinkId: number;
  selectedAmdsId: string;
  closureGroupId: string;
  scope: V2ClosureScope;
  direction: 'forward' | 'reverse' | null;
  removedLinkIds: number[];
  removedArcIds: number[];
  removedAmdsIds: string[];
  removedLinkCount: number;
  removedArcCount: number;
  selectedSegmentLengthM: number;
  totalClosureLengthM: number;
  /** How much more than the selected segment this scope removes. */
  excessLengthM: number;
  boundaryNodes: number[];
  closureNodes: number[];
  shape: string;
  shapeDetail: string;
  /** Identifies the exact arc set removed, for cache and audit. */
  fingerprint: string;
  warning: V2ClosureWarning | null;
}

export interface V2IsolationComponent {
  nodeCount: number;
  linkCount: number;
  roadLengthM: number;
  stateHighwayLinkCount: number;
  retainsPrincipalConnection: boolean;
  /** Empty for the principal side: it is the rest of the country. */
  linkIds: number[];
}

/**
 * How well the undirected graph is believed to model the roads it stands for.
 *
 * Never `high` while endpoints are matched with an ingest tolerance: two ends
 * that were merged because they were close enough are a connection the road
 * may not have. `low` says such a near-miss sits at the closure itself, which
 * is the one place it can change the answer.
 */
export type V2TopologyConfidence = 'medium' | 'low';

/** Whether a side could be named principal on more than a tie-break. */
export type V2PrincipalSideConfidence = 'high' | 'low';

/** How the partition was reached. Reported so the cost of the answer is legible. */
export type V2IsolationMethod =
  | 'precomputed-not-a-bridge'
  | 'bridge-subtree-and-subtraction'
  | 'restricted-bfs'
  | 'empty-closure';

export interface V2Isolation {
  /**
   * The partition was computed exactly, with no search bound involved.
   *
   * Says nothing about whether the graph is right. A single `exact` flag used
   * to conflate the two, and it could only be read as the stronger claim.
   */
  calculationExact: boolean;
  /**
   * Whether the graph models the real road network. Always false: Gu is
   * inferred topology, so an exact result on it is still a result about a
   * model.
   */
  graphExact: boolean;
  /** The partition itself, independent of which side is called principal. */
  partitionExact: boolean;
  topologyConfidence: V2TopologyConfidence;
  /** Plain-English reason for the confidence above. Shown verbatim. */
  topologyConfidenceReason: string;
  /**
   * Which side was called principal, and on what basis — for example "most
   * state-highway links, then most nodes". A bridge yields two components and
   * mathematics alone does not privilege either, so this is a stated policy
   * rather than a derived fact.
   */
  principalSideRule: string;
  principalSideConfidence: V2PrincipalSideConfidence;
  /** True when no side carries a decisive anchor, so none is named cut off. */
  principalSideAmbiguous: boolean;
  physicallyIsolates: boolean;
  method: V2IsolationMethod;
  closureIsBridge: boolean;
  separatedLinkCount: number;
  separatedLengthM: number;
  separatedLinkIds: number[];
  /** True when the id list above was capped. The counts stay exact. */
  separatedTruncated: boolean;
  componentCount: number;
  componentsTruncated: boolean;
  components: V2IsolationComponent[];
  detail: string;
  /** Only when the request asked for geometry and the set is small enough. */
  separatedGeoJson?: GeoJSON.FeatureCollection | null;
}

export interface V2DirectedAccess {
  forward_status: string;
  reverse_status: string;
  forward_distance_m: number | null;
  reverse_distance_m: number | null;
  /**
   * Mutual reachability for this one node pair, decided by running the search
   * both ways. Not a lookup into a precomputed partition of the graph.
   *
   * Null when no conclusion was reached — either the link carries only one
   * direction, so there was no return traversal to test, or a search did not
   * resolve. False would assert that a return path was tested and failed, and
   * that is a different fact about the road.
   */
  same_scc_after_closure: boolean | null;
  asymmetric: boolean;
  detail: string;
}

export interface V2DirectionResult {
  direction: 'forward' | 'reverse';
  status: string;
  headline: string;
  sourceNode: number;
  targetNode: number;
  selectedSegmentLengthM: number;
  normalPathDistanceM: number | null;
  alternativeDistanceM: number | null;
  networkPenaltyM: number | null;
  addedVsSegmentM: number | null;
  detourRatioVsSegment: number | null;
  normalPathTimeS: number | null;
  alternativeTimeS: number | null;
  addedTimeS: number | null;
  routeArcIds: number[];
  qualityFlags: string[];
  errorDetail: string | null;
  runtimeMs: number;
}

export interface V2ClosureAnalysis {
  snapshotId: string;
  linkId: number;
  algorithm: string;
  algorithmVersion: string;
  derivationVersion: string;
  engine: 'v2';
  stability: string;
  request: { scope: V2ClosureScope; metric: string; vehicle: string };
  headline: V2Headline;
  isolationStatement: V2IsolationStatement;
  closure: V2Closure;
  isolation: V2Isolation;
  directedAccess: V2DirectedAccess;
  forward: V2DirectionResult | null;
  reverse: V2DirectionResult | null;
  runtimeMs: number;
  cached: boolean;
  calculatedAtUtc: string;
  /** True only for `source_feature`: the sole scope V1 also answers. */
  comparableToV1: boolean;
  selectedLink: LinkSummary;
  attribution?: string;
  limitations?: string[];
}

/* ------------------------------------------------------------------------
 * V2 boundary-movement analysis.
 *
 * A THIRD measure, and the distinction matters more than the type does.
 * `V2ClosureAnalysis` above measures between the closed segment's own two
 * nodes. This measures trips ACROSS the closure boundary — which crossings
 * genuinely went through here, and what each has to do instead.
 *
 * They are not two versions of one number. A client that showed them side by
 * side under one label would be presenting a disagreement that does not exist,
 * so `comparableToV1` is `false` here by construction and the response carries
 * its own sentence saying why.
 */

/** Every headline the boundary engine may report. Exhaustive. */
export type V2BoundaryHeadline =
  | 'Through movement has no represented replacement'
  | 'Through movement diverts'
  | 'No through movement identified'
  /**
   * The search was BOUNDED and did not evaluate every candidate pair. The
   * sub-results it did establish stay visible; what is withheld is any
   * sentence that would imply it had looked at everything.
   */
  | 'Partial analysis'
  | 'Analysis unresolved';

/**
 * Ordered geometry, split at every discontinuity.
 *
 * `geometry` is a MultiLineString even when there is one piece, so a client
 * cannot flatten it into a single coordinate list and thereby draw a straight
 * line across a gap. `animationSafe` is false whenever that would matter.
 */
export interface V2RouteGeometry {
  geometry: GeoJSON.MultiLineString | null;
  /**
   * `route` is an ordered path, where two pieces that do not meet are a defect
   * worth warning about. `collection` is a set of links - the closure, the
   * selected segment - with no order at all, where the space between two of
   * them is not a gap in anything. A collection always reports `hasGaps:
   * false`, and is never animation-safe: sweeping along it would animate an
   * order that means nothing.
   */
  kind: "route" | "collection";
  pieceCount: number;
  continuous: boolean;
  hasGaps: boolean;
  /** False for a gapped route: a reveal animation implies one unbroken line. */
  animationSafe: boolean;
  gapCount: number;
  gaps: {
    afterArcId: number;
    beforeArcId: number;
    atNode: number;
    distanceM: number;
    fromLonLat: [number, number];
    toLonLat: [number, number];
  }[];
  missingArcIds: number[];
  totalDrawnLengthM: number;
  qualityFlags: string[];
  gapToleranceM: number;
}

export interface V2Movement {
  movementId: string;
  entryPortId: string;
  exitPortId: string;
  fromNode: number;
  toNode: number;
  entryNode: number;
  exitNode: number;
  entryLinkId: number;
  exitLinkId: number;
  entryDirection: string;
  exitDirection: string;
  included: boolean;
  reasonCode: string;
  reason: string;
  intactDistanceM: number | null;
  intactTimeS: number | null;
  removedArcIdsUsed: number[];
  staysWithinClosure: boolean;
  evidence: string[];
  confidence: 'high' | 'medium' | 'low';
}

export interface V2ReplacementPath {
  movementId: string;
  status: string;
  resolved: boolean;
  detail: string;
  intactDistanceM: number | null;
  replacementDistanceM: number | null;
  networkPenaltyM: number | null;
  addedVsSegmentM: number | null;
  ratio: number | null;
  addedTimeS: number | null;
  arcIds: number[];
  linkIds: number[];
  /** True would be a stop condition: a replacement using the road it replaces. */
  traversesOwnClosure: boolean;
  topologyConfidence: string;
  /**
   * The route checked against the restricted-turn table. `ok: false` means the
   * route uses a prohibited turn and is NOT offered as the replacement.
   */
  turnCheck: {
    checked: boolean;
    ok: boolean;
    applicableRestrictions: number;
    violationCount: number;
    violations: number[][];
    detail: string;
  } | null;
  qualityFlags: string[];
  movement?: V2Movement | null;
}

export interface V2CorridorPort {
  candidateId: string;
  side: 'upstream' | 'downstream';
  node: number;
  outwardDistanceM: number;
  hops: number;
  stableKey: string;
  continuityRank: number[];
  evidence: string[];
  roadName: string | null;
  routeDesignation: string | null;
  isStateHighway: boolean;
  nodeDegree: number;
  /** Three or more open links meet here, so a driver has a choice to make. */
  isDecisionPoint: boolean;
  /** Further from the closure than the walk expands. Only a seed can be. */
  beyondSearchBound: boolean;
  included: boolean;
  reasonCode: string;
  reason: string;
}

export interface V2CorridorPair {
  pairId: string;
  upstreamId: string;
  downstreamId: string;
  upstreamNode: number;
  downstreamNode: number;
  upstreamOutwardM: number;
  downstreamOutwardM: number;
  maxOutwardM: number;
  combinedOutwardM: number;
  replacementCostM: number | null;
  bothDecisionPoints: boolean;
  valid: boolean;
  reasonCode: string;
  reason: string;
}

export interface V2Corridor {
  corridorModelVersion: string;
  status: string;
  resolved: boolean;
  detail: string;
  searchBounds: Record<string, number>;
  /**
   * A SEARCH BOUND acted — the beam pruned, the hop limit ended the walk. On a
   * real network this is nearly always true, because that is what a bounded
   * search is; the bounds themselves are declared in `searchBounds`.
   */
  truncated: boolean;
  /**
   * The sharper claim: candidates were GENERATED and then never evaluated, so
   * an unexamined candidate could have made a better pair. By coordinator
   * adjudication this does NOT gate the top-level headline — the headline's
   * claims are movement-level and no corridor candidate can change them — but
   * it lowers this block's `confidence` to `low` and MUST render as a visible
   * caveat wherever the corridor's start point is shown.
   */
  evaluationTruncated: boolean;
  /** The arithmetic behind the flag: check a subtraction, not a boolean. */
  candidatesGeneratedUpstream: number;
  candidatesGeneratedDownstream: number;
  candidatesEvaluatedUpstream: number;
  candidatesEvaluatedDownstream: number;
  truncationDetail: string;
  upstreamCandidates: V2CorridorPort[];
  downstreamCandidates: V2CorridorPort[];
  candidatePairs: V2CorridorPair[];
  candidatePairCount: number;
  validPairCount: number;
  chosenPair: V2CorridorPair | null;
  /** `decision_points` or `all_candidates` — which tier the choice came from. */
  admissibilityLevel: string;
  /**
   * `low` when the chosen pair reaches further from the closure than the walk
   * was allowed to expand. The corridor is real; it is just not the near,
   * recognisable place the rule aims for.
   */
  confidence: string;
  seedBeyondSearchBound: boolean;
  /**
   * The INTACT trip the chosen pair is built on. Without it a pair can have a
   * good post-closure route while the cheapest intact route between those two
   * nodes never used the closure — a diversion nobody needs to make.
   */
  witness: {
    arcIds: number[];
    fromNode: number;
    toNode: number;
    continuous: boolean;
    connectsChosenNodes: boolean;
    traversesClosure: boolean;
    closureArcsUsed: number[];
    valid: boolean;
    detail: string;
  } | null;
  witnessRejections: { pairId: string; detail: string }[];
  explanation: string;
  continuityEvidenceUsed: string[];
  continuityEvidenceExcluded: { evidence: string; reason: string }[];
}

export interface V2BoundaryAnalysis {
  snapshotId: string;
  linkId: number;
  engine: 'v2-boundary';
  algorithm: string;
  algorithmVersion: string;
  stability: string;
  request: { scope: V2ClosureScope; metric: string; vehicle: string };
  headline: V2BoundaryHeadline;
  qualityFlags: string[];
  closure: V2Closure;
  boundary: {
    portModelVersion: string;
    shape: string;
    closureNodes: number[];
    interiorNodes: number[];
    boundaryNodes: number[];
    entryPortCount: number;
    exitPortCount: number;
    /** True when the port measure and the endpoint measure coincide. */
    reducesToEndpoints: boolean;
    selectedComponentId: number;
    closureComponentCount: number;
    closureIsDisjoint: boolean;
    detail: string;
  };
  movements: {
    status: string;
    resolved: boolean;
    detail: string;
    candidatePairs: number;
    candidateBound: number;
    truncated: boolean;
    /**
     * True only when every candidate pair was actually evaluated. When false,
     * no headline may imply the search looked at everything.
     */
    exhaustive: boolean;
    /** Disconnected pieces of the closure. Pairs form WITHIN a piece. */
    closureComponents: number;
    componentsConsidered: number;
    omittedPairCount: number;
    omittedEntryPorts: number;
    omittedExitPorts: number;
    crossComponentPairCount: number;
    /** Bounded worked examples, never the whole omitted cross-product. */
    omittedPairSampleLimit: number;
    omittedPairSample: {
      entryStableKey: string;
      exitStableKey: string;
      entryComponent: number;
      exitComponent: number;
      reason: string;
    }[];
    includedCount: number;
    movements: V2Movement[];
  };
  replacements: {
    algorithm: string;
    canonicalAnswer: string;
    status: string;
    resolved: boolean;
    detail: string;
    pathCount: number;
    resolvedCount: number;
    disconnectedCount: number;
    unresolvedCount: number;
    paths: V2ReplacementPath[];
  };
  principal: V2ReplacementPath | null;
  corridor: V2Corridor | null;
  /** Kept in its own block. Never folded into the routing result. */
  isolation: V2Isolation | null;
  geometry: {
    selectedSegment?: V2RouteGeometry;
    closure?: V2RouteGeometry;
    intactMovement?: V2RouteGeometry;
    replacement?: V2RouteGeometry;
  } | null;
  stageMs: Record<string, number>;
  runtimeMs: number;
  /** Always false: this asks a different question from V1, not a better one. */
  comparableToV1: boolean;
  comparableToV1Detail: string;
  selectedLink: LinkSummary;
  attribution?: string;
  limitations?: string[];
}

/*
 * Topology sensitivity: a SEPARATE request, and a separate answer.
 *
 * The canonical replacement path comes from `/boundary-analysis` and is the
 * product answer. Everything here is an assumption - "if this unresolved
 * crossing were a junction, the answer would be X" - and the types keep the
 * two apart so a component cannot render one as the other by accident.
 *
 * Measured: canonical is 1.27 s and fits an interactive budget; three
 * counterfactuals take 6.7 s and do not. That is why it is a second request.
 */

/** The resolved states. `checking` is the client's own in-flight state. */
export type V2SensitivityState =
  | 'TOPOLOGY_SENSITIVE'
  | 'NO_CHANGE_FOUND'
  | 'SENSITIVITY_UNAVAILABLE'
  | 'SENSITIVITY_INCOMPLETE';

export interface V2AssumedJunction {
  crossingId: number;
  label: string | null;
  classifierDisposition: string | null;
  classifierReason: string | null;
}

export interface V2Counterfactual {
  /** Always false. A counterfactual is never the canonical answer. */
  isCanonical: false;
  assumedJunctionCrossingIds: number[];
  assumedJunctions: V2AssumedJunction[];
  status: string;
  distanceM: number | null;
  tested: boolean;
  untestedReason: string | null;
  /** `null` when the candidate was not tested - never `false`. */
  individuallyChangesAnswer: boolean | null;
  whatChanged: string[];
  assumptionKind: string;
}

export interface V2TopologySensitivity {
  available: boolean;
  /** Echoed back untouched, so a stale response can be discarded. */
  token: string | null;
  state: V2SensitivityState;
  message: string;
  headline?: string;
  topologySensitive?: boolean;
  canonicalAnswer?: {
    isCanonical: true;
    status: string;
    distanceM: number | null;
  };
  counterfactuals?: V2Counterfactual[];
  testedCandidates?: number;
  candidateCap?: number;
  capNote?: string | null;
  analysisComplete?: boolean;
  unavailableReason?: string | null;
  candidateSearch?: {
    candidateSource: string;
    candidateSourceComplete: boolean;
    candidates: number;
    truncated: boolean;
  };
  timing?: Record<string, unknown>;
}
