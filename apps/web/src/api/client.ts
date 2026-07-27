/**
 * Runtime client for the detour API.
 *
 * Same-origin by default: Vite proxies /api, /tiles and /health to the backend
 * in development, and production serves both from one origin. An absolute base
 * is only for pointing a build at a remote deployment.
 *
 * This used to default to an absolute localhost URL, which made it possible to
 * run the app against the wrong one of the repository's two API
 * implementations without noticing.
 *
 * Every method takes an `AbortSignal`. A request the user has moved on from is
 * cancelled rather than left to land late and overwrite a newer result.
 */

import {
  closureScopeToWire,
  type ClosureScope,
  type Metric,
  type Vehicle,
} from './scenario.js';
import type {
  DetourResponse,
  NetworkMetadata,
  SearchResponse,
} from './types.js';

const BASE = import.meta.env.VITE_API_BASE_URL ?? '';

/**
 * An API failure that carries enough structure for the caller to decide
 * whether retrying could possibly help.
 *
 * `retryable` is the distinction that matters: a 503 or a dropped connection
 * is worth another attempt, a 404 for a link id that does not exist in this
 * snapshot is not, and retrying it just delays telling the user.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly retryable: boolean;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.retryable = status === 0 || status === 429 || status >= 500;
  }
}

/** A scope the current backend cannot honour must fail loudly, not degrade. */
export class UnsupportedScopeError extends Error {
  constructor(scope: ClosureScope) {
    super(
      `Closure scope "${scope}" is not supported by this snapshot's ` +
        `processing version.`,
    );
    this.name = 'UnsupportedScopeError';
  }
}

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, { signal });
  } catch (e) {
    /* AbortError is a control-flow signal, not a failure: let it through
     * untouched so the query layer can recognise it. */
    if (e instanceof DOMException && e.name === 'AbortError') throw e;
    throw new ApiError(
      'Could not reach the analysis service. It may not be running.',
      0,
    );
  }

  if (!res.ok) {
    let message = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      /* FastAPI raises HTTPException as { detail }. The TypeScript reference
       * service uses { error }. Reading only one meant real explanations were
       * replaced by a bare status code. */
      const d = body?.detail ?? body?.error;
      if (typeof d === 'string') message = d;
      else if (Array.isArray(d))
        message = d.map((e) => e?.msg ?? String(e)).join('; ');
      else if (d) message = JSON.stringify(d);
    } catch {
      /* non-JSON error body: keep the status line */
    }
    throw new ApiError(message, res.status);
  }

  return res.json() as Promise<T>;
}

export interface DetourRequest {
  link: string | number;
  metric: Metric;
  vehicle: Vehicle;
  closureScope: ClosureScope;
  direction: 'forward' | 'reverse' | 'both';
}

export const api = {
  base: BASE,

  metadata: (signal?: AbortSignal) =>
    get<NetworkMetadata>('/api/v1/network/metadata', signal),

  search: (
    params: { name?: string; amdsId?: string; limit?: number },
    signal?: AbortSignal,
  ) => {
    const q = new URLSearchParams();
    if (params.name) q.set('name', params.name);
    if (params.amdsId) q.set('amdsId', params.amdsId);
    q.set('limit', String(params.limit ?? 25));
    return get<SearchResponse>(`/api/v1/links/search?${q}`, signal);
  },

  detour: (req: DetourRequest, signal?: AbortSignal) => {
    const wireScope = closureScopeToWire(req.closureScope);
    if (wireScope === null) {
      return Promise.reject(new UnsupportedScopeError(req.closureScope));
    }
    const q = new URLSearchParams({
      metric: req.metric,
      vehicle: req.vehicle,
      closure_scope: wireScope,
      direction: req.direction,
    });
    return get<DetourResponse>(
      `/api/v1/links/${encodeURIComponent(String(req.link))}/detour?${q}`,
      signal,
    );
  },
};

/**
 * A free-text query is an AMDS id if it looks like a GUID in braces, which is
 * how AMDS writes them. Everything else is treated as a name.
 */
export function searchParamsFor(query: string): { name?: string; amdsId?: string } {
  const q = query.trim();
  return q.startsWith('{') ? { amdsId: q } : { name: q };
}
