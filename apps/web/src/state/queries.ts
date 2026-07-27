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
import type { Scenario } from '../api/scenario.js';
import type { DetourResponse, NetworkMetadata, SearchResponse } from '../api/types.js';

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

export function useDetour(parts: DetourKeyParts) {
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
    enabled: link !== null && parts.version !== 'unknown',
  });
}
