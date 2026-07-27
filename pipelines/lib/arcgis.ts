/**
 * Minimal ArcGIS Feature Service client, built for verifiable bulk extraction.
 *
 * Extraction strategy, in the order the prompt requires:
 *   1. read service and layer metadata
 *   2. ask for the full OBJECTID list (returnIdsOnly=true)
 *   3. download features in bounded OBJECTID batches, respecting maxRecordCount
 *   4. retry with exponential backoff
 *   5. reconcile downloaded ids against the requested id list
 *
 * Batching by explicit id ranges rather than resultOffset paging is deliberate.
 * Offset paging over a service that is being edited underneath you can silently
 * skip or duplicate rows; an id list pins exactly which features are expected,
 * so a shortfall is detectable rather than invisible.
 */

import { createHash } from 'node:crypto';

export interface ArcGisError extends Error {
  httpStatus?: number;
  esriCode?: number;
}

export interface FetchJsonOptions {
  retries?: number;
  timeoutMs?: number;
  label?: string;
  /**
   * Form-encoded body. When present the request is POSTed. Batched OBJECTID
   * lists routinely exceed the ~8 KB URL limit and a GET returns HTTP 414, so
   * every query that carries an id list must POST.
   */
  body?: URLSearchParams;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function fetchJson<T = any>(
  url: string,
  opts: FetchJsonOptions = {},
): Promise<T> {
  const retries = opts.retries ?? 5;
  const timeoutMs = opts.timeoutMs ?? 120_000;
  let lastErr: unknown;

  for (let attempt = 0; attempt <= retries; attempt++) {
    if (attempt > 0) {
      const backoff = Math.min(30_000, 1000 * 2 ** (attempt - 1));
      await sleep(backoff + Math.floor(Math.random() * 250));
    }
    const ac = new AbortController();
    const timer = setTimeout(() => ac.abort(), timeoutMs);
    try {
      const res = await fetch(url, {
        signal: ac.signal,
        ...(opts.body
          ? {
              method: 'POST',
              body: opts.body,
              headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            }
          : {}),
      });
      if (!res.ok) {
        const e = new Error(
          `HTTP ${res.status} for ${opts.label ?? url}`,
        ) as ArcGisError;
        e.httpStatus = res.status;
        throw e;
      }
      const body = (await res.json()) as any;
      // ArcGIS returns HTTP 200 with an error envelope. Never treat that as data.
      if (body && body.error) {
        const e = new Error(
          `ArcGIS error ${body.error.code}: ${body.error.message}` +
            (body.error.details?.length ? ` (${body.error.details.join('; ')})` : ''),
        ) as ArcGisError;
        e.esriCode = body.error.code;
        throw e;
      }
      return body as T;
    } catch (err) {
      lastErr = err;
    } finally {
      clearTimeout(timer);
    }
  }
  throw lastErr instanceof Error ? lastErr : new Error(String(lastErr));
}

export function queryUrl(
  serviceUrl: string,
  layerId: number,
  params: Record<string, string | number | boolean | undefined>,
): string {
  const u = new URL(`${serviceUrl}/${layerId}/query`);
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined) u.searchParams.set(k, String(v));
  }
  return u.toString();
}

/** POST a query. Use this whenever parameters may be large (id lists, geometry). */
export function queryBody(
  params: Record<string, string | number | boolean | undefined>,
): URLSearchParams {
  const b = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined) b.set(k, String(v));
  }
  return b;
}

export interface LayerMeta {
  id: number;
  name: string;
  type: string;
  geometryType?: string;
  objectIdField: string;
  globalIdField?: string;
  maxRecordCount: number;
  supportsPagination: boolean;
  supportsStatistics: boolean;
  fields: { name: string; type: string; alias?: string; domain?: any }[];
  extent?: any;
  copyrightText?: string;
  description?: string;
  raw: any;
}

export async function getLayerMeta(
  serviceUrl: string,
  layerId: number,
): Promise<LayerMeta> {
  const raw = await fetchJson(`${serviceUrl}/${layerId}?f=json`, {
    label: `layer ${layerId} metadata`,
  });
  return {
    id: raw.id,
    name: raw.name,
    type: raw.type,
    geometryType: raw.geometryType,
    objectIdField: raw.objectIdField ?? 'OBJECTID',
    globalIdField: raw.globalIdField,
    maxRecordCount: raw.maxRecordCount ?? 1000,
    supportsPagination: Boolean(raw.advancedQueryCapabilities?.supportsPagination),
    supportsStatistics: Boolean(raw.advancedQueryCapabilities?.supportsStatistics),
    fields: raw.fields ?? [],
    extent: raw.extent,
    copyrightText: raw.copyrightText,
    description: raw.description,
    raw,
  };
}

export interface ExtentFilter {
  xmin: number;
  ymin: number;
  xmax: number;
  ymax: number;
  wkid: number;
}

export interface CountOptions {
  where: string;
  extent?: ExtentFilter | null;
}

export async function getCount(
  serviceUrl: string,
  layerId: number,
  o: CountOptions,
): Promise<number> {
  const res = await fetchJson<{ count: number }>(
    `${serviceUrl}/${layerId}/query`,
    {
      label: `layer ${layerId} count`,
      body: queryBody({
        where: o.where,
        returnCountOnly: true,
        f: 'json',
        ...extentParams(o.extent),
      }),
    },
  );
  return res.count;
}

export async function getObjectIds(
  serviceUrl: string,
  layerId: number,
  o: CountOptions,
): Promise<number[]> {
  const res = await fetchJson<{ objectIds: number[] | null }>(
    `${serviceUrl}/${layerId}/query`,
    {
      label: `layer ${layerId} object ids`,
      body: queryBody({
        where: o.where,
        returnIdsOnly: true,
        f: 'json',
        ...extentParams(o.extent),
      }),
    },
  );
  return res.objectIds ?? [];
}

function extentParams(
  e: ExtentFilter | null | undefined,
): Record<string, string | undefined> {
  if (!e) return {};
  return {
    geometry: JSON.stringify({
      xmin: e.xmin,
      ymin: e.ymin,
      xmax: e.xmax,
      ymax: e.ymax,
      spatialReference: { wkid: e.wkid },
    }),
    geometryType: 'esriGeometryEnvelope',
    inSR: String(e.wkid),
    spatialRel: 'esriSpatialRelIntersects',
  };
}

export interface DownloadOptions {
  serviceUrl: string;
  layerId: number;
  objectIds: number[];
  outFields: string;
  returnGeometry: boolean;
  outSR: number;
  batchSize: number;
  concurrency?: number;
  objectIdField?: string;
  onProgress?: (done: number, total: number) => void;
}

export interface DownloadResult {
  features: any[];
  /** SHA-256 over every page payload, in batch order. Reproducible. */
  sha256: string;
  batches: number;
  /** Ids that were requested but never came back. Empty on a clean extract. */
  missingIds: number[];
  duplicateIds: number[];
}

/**
 * Downloads features by explicit OBJECTID batches and reconciles the result.
 * Throws only on unrecoverable transport failure; a short extract is reported
 * through `missingIds` so the caller can mark the snapshot partial rather than
 * silently shipping a hole in the network.
 */
export async function downloadByIds(o: DownloadOptions): Promise<DownloadResult> {
  const idField = o.objectIdField ?? 'OBJECTID';
  const batchSize = Math.max(1, o.batchSize);
  const concurrency = Math.max(1, o.concurrency ?? 4);

  // Reconciliation reads the OBJECTID off each returned feature, so the id
  // field has to be requested even when the caller only wants a few columns.
  // Without this every id looks "missing" and a clean extract reports as short.
  const outFields =
    o.outFields === '*' ||
    o.outFields.split(',').some((f) => f.trim().toLowerCase() === idField.toLowerCase())
      ? o.outFields
      : `${idField},${o.outFields}`;

  const batches: number[][] = [];
  for (let i = 0; i < o.objectIds.length; i += batchSize) {
    batches.push(o.objectIds.slice(i, i + batchSize));
  }

  const pages: (any[] | undefined)[] = new Array(batches.length);
  const hashes: (string | undefined)[] = new Array(batches.length);
  let done = 0;
  let next = 0;

  const worker = async () => {
    for (;;) {
      const idx = next++;
      if (idx >= batches.length) return;
      // POST: an OBJECTID batch of 2000 ids far exceeds the GET URL limit.
      const body = await fetchJson<any>(`${o.serviceUrl}/${o.layerId}/query`, {
        label: `batch ${idx + 1}/${batches.length}`,
        body: queryBody({
          objectIds: batches[idx].join(','),
          outFields,
          returnGeometry: o.returnGeometry,
          outSR: o.outSR,
          f: 'json',
        }),
      });
      const feats = body.features ?? [];
      pages[idx] = feats;
      hashes[idx] = createHash('sha256').update(JSON.stringify(body)).digest('hex');
      done += feats.length;
      o.onProgress?.(done, o.objectIds.length);
    }
  };

  await Promise.all(Array.from({ length: concurrency }, worker));

  const features: any[] = [];
  for (const p of pages) if (p) features.push(...p);

  // --- reconcile ---
  const seen = new Map<number, number>();
  for (const f of features) {
    const oid = f.attributes?.[idField];
    seen.set(oid, (seen.get(oid) ?? 0) + 1);
  }
  const missingIds = o.objectIds.filter((id) => !seen.has(id));
  const duplicateIds = [...seen.entries()].filter(([, c]) => c > 1).map(([id]) => id);

  const roll = createHash('sha256');
  for (const h of hashes) roll.update(h ?? '');

  return {
    features,
    sha256: roll.digest('hex'),
    batches: batches.length,
    missingIds,
    duplicateIds,
  };
}
