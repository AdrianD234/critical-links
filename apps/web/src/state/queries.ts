/**
 * The request layer.
 *
 * TanStack Query handles the things that are easy to get subtly wrong by hand:
 * cancelling a request the user has moved on from, de-duplicating identical
 * in-flight requests, and keeping "this data is stale" distinct from "this data
 * is absent".
 *
 * THE QUERY KEY IS THE CONTRACT. It contains everything the result depends on
 * — snapshot, link, scenario, direction, and the algorithm and processing
 * versions. The version components matter: when turn-restriction semantics or
 * segment scope land, the backend's algorithm version changes and every cached
 * figure computed under the old one becomes wrong. Including the version in the
 * key means those entries are simply never read again, rather than being
 * displayed under new settings.
 */

import { QueryClient, useQuery } from '@tanstack/react-query';

import { ApiError, api, searchParamsFor } from '../api/client.js';
import type { DirectionKey, Scenario } from '../api/scenario.js';
import type {
  DetourResponse,
  NetworkMetadata,
  SearchResponse,
  V2Capabilities,
  V2BoundaryAnalysis,
  V2ClosureAnalysis,
} from '../api/types.js';

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        /* A road's detour does not change unless the snapshot does, and the
         * snapshot is in the key. Refetching on window focus would recompute
         * an expensive path for no reason. */
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
        staleTime: 5 * 60_000,
        gcTime: 30 * 60_000,
        retry: (attempt, error) => {
          /* Retrying a 404 for a link id that does not exist in this snapshot
           * only delays telling the user. Retry transport and server faults,
           * nothing else. */
          if (error instanceof ApiError) return error.retryable && attempt < 2;
          return false;
        },
        retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 4000),
      },
    },
  });
}

/* ------------------------------------------------------------- metadata */

export function useMetadata() {
  return useQuery<NetworkMetadata>({
    queryKey: ['metadata'],
    queryFn: ({ signal }) => api.metadata(signal),
    staleTime: Infinity,
  });
}

/**
 * The version fingerprint that participates in every result key.
 *
 * Falls back to the snapshot id alone when the backend does not report
 * capabilities, which is correct-but-coarse: a snapshot change still
 * invalidates, an algorithm change against the same snapshot does not. The
 * backend capabilities block closes that gap.
 */
export function resultVersion(meta: NetworkMetadata | undefined): string {
  if (!meta) return 'unknown';
  const c = meta.capabilities;
  return c
    ? `${meta.snapshotId}|${c.algorithmVersion}|${c.processingVersion}`
    : meta.snapshotId;
}

/* --------------------------------------------------------------- search */

export function useRoadSearch(query: string, enabled: boolean) {
  const q = query.trim();
  return useQuery<SearchResponse>({
    queryKey: ['search', q],
    queryFn: ({ signal }) => api.search({ ...searchParamsFor(q), limit: 25 }, signal),
    enabled: enabled && q.length >= 2,
    staleTime: 60_000,
    /* A slower result for a query the user has already typed past is
     * discarded by the key changing; nothing needs to be done here. */
  });
}

/* --------------------------------------------------------------- detour */

export interface DetourKeyParts {
  link: string | number | null;
  scenario: Scenario;
  version: string;
}

export function detourQueryKey({ link, scenario, version }: DetourKeyParts) {
  return [
    'detour',
    version,
    link,
    scenario.metric,
    scenario.vehicle,
    scenario.closureScope,
  ] as const;
}

export function useDetour(parts: DetourKeyParts, enabled = true) {
  const { link, scenario } = parts;
  return useQuery<DetourResponse>({
    queryKey: detourQueryKey(parts),
    queryFn: ({ signal }) =>
      api.detour(
        {
          link: link!,
          metric: scenario.metric,
          vehicle: scenario.vehicle,
          closureScope: scenario.closureScope,
          /* Always request both directions. The inspector switches between
           * them locally, and a switch is not a new calculation — asking the
           * server again for a figure it already returned would put a
           * skeleton on screen for something already known. */
          direction: 'both',
        },
        signal,
      ),
    enabled: enabled && link !== null && parts.version !== 'unknown',
  });
}

/* ------------------------------------------------------------------- V2 */

/**
 * What the V2 engine can do, for the snapshot the backend is serving.
 *
 * Its own endpoint rather than a key on the V1 metadata, so a client that
 * wants V2 asks V2 and the V1 contract stays as published. Only fetched when
 * the V2 engine is actually selected — under V1 nothing reads it, and an
 * unconditional request would put a V2 call in every production session.
 */
export function useV2Capabilities(enabled: boolean) {
  return useQuery<V2Capabilities>({
    queryKey: ['v2-capabilities'],
    queryFn: ({ signal }) => api.v2Capabilities(signal),
    enabled,
    staleTime: Infinity,
  });
}

export interface ClosureAnalysisKeyParts {
  link: string | number | null;
  scenario: Scenario;
  /** Which direction to analyse. `direction` scope cannot take both. */
  direction: DirectionKey;
  /** Snapshot, algorithm and derivation, as one string. */
  version: string;
}

export function closureAnalysisQueryKey({
  link,
  scenario,
  direction,
  version,
}: ClosureAnalysisKeyParts) {
  return [
    'closure-analysis-v2',
    version,
    link,
    scenario.metric,
    scenario.vehicle,
    scenario.closureScope,
    direction,
  ] as const;
}

export function useClosureAnalysisV2(
  parts: ClosureAnalysisKeyParts,
  enabled: boolean,
) {
  const { link, scenario, direction } = parts;
  return useQuery<V2ClosureAnalysis>({
    queryKey: closureAnalysisQueryKey(parts),
    queryFn: ({ signal }) =>
      api.closureAnalysisV2(
        {
          link: link!,
          metric: scenario.metric,
          vehicle: scenario.vehicle,
          closureScope: scenario.closureScope,
          /* Both directions except under `direction` scope, which is a single
           * directed traversal and has to name the one it means. */
          direction: scenario.closureScope === 'direction' ? direction : 'both',
        },
        signal,
      ),
    enabled: enabled && link !== null && parts.version !== 'unknown',
  });
}

/**
 * The boundary-movement analysis.
 *
 * A SEPARATE query key from the endpoint one, not a variant of it. The two
 * measure different quantities, so one must never be served out of the other's
 * cache entry - and a key that differed only by a parameter is exactly the
 * shape that later gets "simplified" into one that does.
 */
export function boundaryAnalysisQueryKey(parts: ClosureAnalysisKeyParts) {
  const { link, scenario, direction, version } = parts;
  return [
    'boundary-analysis-v2',
    version,
    link,
    scenario.metric,
    scenario.vehicle,
    scenario.closureScope,
    direction,
  ] as const;
}

export function useBoundaryAnalysisV2(
  parts: ClosureAnalysisKeyParts,
  enabled: boolean,
) {
  const { link, scenario, direction } = parts;
  return useQuery<V2BoundaryAnalysis>({
    queryKey: boundaryAnalysisQueryKey(parts),
    queryFn: ({ signal }) =>
      api.boundaryAnalysisV2(
        {
          link: link!,
          metric: scenario.metric,
          vehicle: scenario.vehicle,
          closureScope: scenario.closureScope,
          direction: scenario.closureScope === 'direction' ? direction : 'both',
        },
        signal,
      ),
    enabled: enabled && link !== null && parts.version !== 'unknown',
  });
}

/**
 * The V2 version fingerprint.
 *
 * Built from the V2 capabilities rather than the V1 metadata block: the two
 * engines version independently, and a V2 algorithm change against an
 * unchanged snapshot must still invalidate every V2 figure in the cache.
 */
export function v2ResultVersion(caps: V2Capabilities | undefined): string {
  if (!caps) return 'unknown';
  return `${caps.snapshotId}|${caps.algorithmVersion}|${caps.derivationVersion}`;
}
