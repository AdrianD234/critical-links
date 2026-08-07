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
  closureScopeToWireV2,
  type ClosureScope,
  type Metric,
  type Vehicle,
} from './scenario.js';
import type {
  DetourResponse,
  NetworkMetadata,
  SearchResponse,
  V2Capabilities,
  V2ClosureAnalysis,
} from './types.js';

const BASE = import.meta.env.VITE_API_BASE_URL ?? '';

/**
 * Always ask V1 for the label block.
 *
 * The seven label fields are opt-in on the V1 routes so that a V1 response
 * without the flag stays byte-identical to the published contract. This client
 * is not a contract consumer that needs that guarantee — it is the interface
 * the fix was made for, and the naming fix has to hold under V1, which is the
 * default engine, and not only behind the development V2 switch.
 *
 * V2 always sends them, so no flag is passed there.
 */
const LABELS = 'true';

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

export interface ClosureAnalysisRequest {
  link: string | number;
  metric: Metric;
  vehicle: Vehicle;
  closureScope: ClosureScope;
  /**
   * V2 rejects `both` for `direction` scope: a single directed traversal has
   * to say which one. The caller passes the direction actually in focus.
   */
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
    q.set('labels', LABELS);
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
      labels: LABELS,
    });
    return get<DetourResponse>(
      `/api/v1/links/${encodeURIComponent(String(req.link))}/detour?${q}`,
      signal,
    );
  },

  /* ------------------------------------------------------------ V2 */

  v2Capabilities: (signal?: AbortSignal) =>
    get<V2Capabilities>('/api/v2/capabilities', signal),

  /**
   * V2 asks for geometry explicitly and this client does not.
   *
   * The V2 preview reports the closure and the isolation as figures; nothing
   * in it draws. Requesting geometry would ship a separated-link collection
   * that no layer consumes, on every scenario change.
   */
  closureAnalysisV2: (req: ClosureAnalysisRequest, signal?: AbortSignal) => {
    const q = new URLSearchParams({
      scope: closureScopeToWireV2(req.closureScope),
      direction: req.direction,
      metric: req.metric,
      vehicle: req.vehicle,
      geometry: 'false',
    });
    return get<V2ClosureAnalysis>(
      `/api/v2/links/${encodeURIComponent(String(req.link))}/closure-analysis?${q}`,
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
