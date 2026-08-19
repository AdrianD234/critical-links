/**
 * Runtime client for the two-point outage span endpoints.
 *
 * Its own module, mirroring `nzcl.api_outage` on the server: this is a
 * feature-flagged draft and must not add surface to the client every other
 * screen depends on.
 *
 * Every call takes an `AbortSignal`, on the same reasoning the main client
 * states — a request the user has moved on from is cancelled rather than left
 * to land late and overwrite a newer result. That matters more here than
 * anywhere else in the application, because dragging a handle produces a
 * stream of requests whose answers arrive out of order by design.
 */

import { ApiError } from './client.js';
import type { Metric, Vehicle } from './scenario.js';

const BASE = import.meta.env.VITE_API_BASE_URL ?? '';

/** Which way traffic is stopped. Handle order is what orients it. */
export type DirectionMode = 'both' | 'a_to_b' | 'b_to_a';

/** Which end of the span. */
export type HandleId = 'a' | 'b';

/**
 * A handle, as the server describes it.
 *
 * A LINEAR REFERENCE — a position along a centreline — not a click and not a
 * link. `fraction` is what the geometry is cut at; `distanceAlongM` is what a
 * person reads.
 */
export interface SnapHandle {
  linkId: number;
  amdsId: string;
  closureGroupId: string;
  roadName: string | null;
  roadNumber: string | null;
  distanceAlongM: number;
  fraction: number;
  linkLengthM: number;
  x: number;
  y: number;
  lon: number;
  lat: number;
  offsetM: number;
  forwardAllowed: boolean;
  reverseAllowed: boolean;
  oneway: number | null;
  stableKey: string;
}

/**
 * What a click resolved to.
 *
 * The two kinds of rival are kept apart deliberately, and the difference is
 * the whole reason this shape is not a flat list.
 *
 * `equivalentHosts` sit at the SAME coordinate on different links — a
 * crossroads. There is nothing to ask the user, because the point is the
 * point, but every one of them must be handed back with the corridor request:
 * which link the handle belongs to decides which road the outage runs along.
 *
 * `alternatives` are somewhere ELSE, and are the only thing `ambiguous` is
 * computed from — two carriageways of a divided road, where choosing the
 * nearer by a metre chooses by pointing noise.
 */
export interface SnapResult {
  snapshotId: string;
  found: boolean;
  handle: SnapHandle | null;
  candidates: SnapHandle[];
  equivalentHosts: SnapHandle[];
  hostLinkIds: number[];
  alternatives: SnapHandle[];
  ambiguous: boolean;
  ambiguityReason: string | null;
  snapModelVersion: string;
}

export interface SpanStep {
  linkId: number;
  amdsId: string;
  roadName: string | null;
  routeDesignation: string | null;
  traversal: 'forward' | 'reverse';
  fromFraction: number;
  toFraction: number;
  coveredM: number;
}

export interface CorridorEvidence {
  routeDesignationContinuous: boolean;
  roadNameContinuous: boolean;
  roadChanges: number;
  codes: string[];
}

/** One corridor the outage could be, with the evidence for it. */
export interface SpanCandidate {
  candidateId: string;
  origin: string;
  lengthM: number;
  roads: string;
  linkIds: number[];
  steps: SpanStep[];
  evidence: CorridorEvidence;
}

export interface CorridorResult {
  snapshotId: string;
  found: boolean;
  corridor: SpanCandidate | null;
  candidates: SpanCandidate[];
  ambiguous: boolean;
  ambiguityReason: string | null;
  corridorModelVersion: string;
  attribution: string | null;
  limitations: string[];
}

export interface DirectedMeasure {
  direction: 'a_to_b' | 'b_to_a';
  status: string;
  resolved: boolean;
  replacementDistanceM: number | null;
  replacementTimeS: number | null;
  addedDistanceM: number | null;
  ratio: number | null;
  detail: string | null;
  runtimeMs: number;
}

export interface PermalinkState {
  snapshotId: string;
  aLinkId: number;
  aFraction: number;
  bLinkId: number;
  bFraction: number;
  corridorId: string;
  directionMode: DirectionMode;
  profile: Vehicle;
  metric: Metric;
}

export interface GeoJson {
  type: 'FeatureCollection';
  features: unknown[];
  measuredLengthM?: number;
  arithmeticLengthM?: number;
}

export interface OutageAnalysis {
  snapshotId: string;
  engine: string;
  algorithm: string;
  algorithmVersion: string;
  stability: string;
  processingVersion: string;
  codeProcessingVersion: string;
  comparableToV1: false;
  comparableToV1Detail: string;
  handleA: SnapHandle;
  handleB: SnapHandle;
  corridor: SpanCandidate;
  corridorCandidates: SpanCandidate[];
  corridorAmbiguous: boolean;
  corridorAmbiguityReason: string | null;
  closedLengthM: number;
  measures: DirectedMeasure[];
  headline: string;
  /** Always null in this foundation. The reason is carried beside it. */
  isolation: null;
  isolationUnavailableReason: string;
  /** Always null in this foundation. NEVER a claim of robustness. */
  sensitivity: null;
  sensitivityUnavailableReason: string;
  isSeparateFromCanonical: true;
  canonicalRouteSlot: string;
  qualityFlags: string[];
  fingerprint: string;
  runtimeMs: number;
  measurementCaveat: string;
  permalink: PermalinkState;
  closureGeometry?: GeoJson;
  replacementGeometry?: Record<string, GeoJson>;
  attribution: string | null;
  limitations: string[];
}

async function get<T>(path: string, signal: AbortSignal): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    signal,
    headers: { accept: 'application/json' },
  });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body?.detail) detail = body.detail;
    } catch {
      /* A non-JSON error body is not itself an error worth reporting over the
       * status it came with. */
    }
    throw new ApiError(detail, res.status);
  }
  return (await res.json()) as T;
}

/** Resolve a click, in WGS84, onto the nearest usable centreline. */
export function snap(
  lon: number,
  lat: number,
  vehicle: Vehicle,
  signal: AbortSignal,
): Promise<SnapResult> {
  const p = new URLSearchParams({
    lon: String(lon),
    lat: String(lat),
    vehicle,
  });
  return get<SnapResult>(`/api/v2/outage/snap?${p}`, signal);
}

/**
 * `linkId:fraction` for each equivalent host beyond the chosen one.
 *
 * The chosen handle is sent as `aLink`/`aFraction`; its equivalents ride along
 * so corridor selection can consider every link legitimately occupying that
 * coordinate rather than having had one picked for it by a floating-point
 * margin.
 */
function alternates(handle: SpanHandleRef): string[] {
  return handle.equivalentHosts.map((h) => `${h.linkId}:${h.fraction}`);
}

/** A handle plus the other links that legitimately host the same point. */
export interface SpanHandleRef {
  linkId: number;
  fraction: number;
  equivalentHosts: { linkId: number; fraction: number }[];
}

function spanParams(a: SpanHandleRef, b: SpanHandleRef): URLSearchParams {
  const p = new URLSearchParams();
  p.set('aLink', String(a.linkId));
  p.set('aFraction', String(a.fraction));
  p.set('bLink', String(b.linkId));
  p.set('bFraction', String(b.fraction));
  for (const alt of alternates(a)) p.append('aAlt', alt);
  for (const alt of alternates(b)) p.append('bAlt', alt);
  return p;
}

/** Which roads the outage between these two handles could run along. */
export function corridor(
  a: SpanHandleRef,
  b: SpanHandleRef,
  vehicle: Vehicle,
  signal: AbortSignal,
): Promise<CorridorResult> {
  const p = spanParams(a, b);
  p.set('vehicle', vehicle);
  return get<CorridorResult>(`/api/v2/outage/corridor?${p}`, signal);
}

/**
 * Close the span and measure the way round.
 *
 * `corridorId` pins which corridor to use and is what a permalink carries. The
 * server answers 409 for an id that is no longer among the candidates rather
 * than substituting one, so a restored span either reproduces what was shared
 * or says it cannot.
 */
export function analysis(
  a: SpanHandleRef,
  b: SpanHandleRef,
  opts: {
    vehicle: Vehicle;
    metric: Metric;
    direction: DirectionMode;
    corridorId?: string | null;
    geometry?: boolean;
  },
  signal: AbortSignal,
): Promise<OutageAnalysis> {
  const p = spanParams(a, b);
  p.set('vehicle', opts.vehicle);
  p.set('metric', opts.metric);
  p.set('direction', opts.direction);
  if (opts.corridorId) p.set('corridorId', opts.corridorId);
  if (opts.geometry) p.set('geometry', 'true');
  return get<OutageAnalysis>(`/api/v2/outage/analysis?${p}`, signal);
}

/** True when the editor is switched on for this build. */
export function editorEnabled(): boolean {
  return import.meta.env.VITE_ENABLE_OUTAGE_SPAN_EDITOR === '1';
}
