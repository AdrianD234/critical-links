/**
 * On-disk snapshot format and loader.
 *
 * A snapshot is immutable. Detour caches key on snapshotId + algorithmVersion,
 * so re-ingesting the source produces a new snapshot id and every cached result
 * becomes inapplicable automatically rather than silently stale.
 *
 * Layout:  data/processed/<snapshotId>/
 *   meta.json             SnapshotMeta - provenance, counts, licence
 *   links.ndjson          one LinkRecord per line, no geometry
 *   geometry.bin          Float64 [x,y,...] for all links, EPSG:2193
 *   geometry-offset.bin   Int32 CSR offsets into geometry.bin (in floats)
 *   restrictions.json     turn restrictions resolved to internal link ids
 */

import { createReadStream } from 'node:fs';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { createInterface } from 'node:readline';
import path from 'node:path';

import { buildGraph, type GeometryStore, RoadGraph } from './graph.js';
import type { LinkRecord, SnapshotMeta, TurnRestriction } from './types.js';

export interface LoadedSnapshot {
  meta: SnapshotMeta;
  links: LinkRecord[];
  graph: RoadGraph;
  geometry: GeometryStore;
  restrictions: TurnRestriction[];
  /** 1 when the link is inside the analysis area (not just network buffer). */
  coreLink: Uint8Array | null;
  inferredJoins: number;
}

export function snapshotDir(dataDir: string, snapshotId: string): string {
  return path.join(dataDir, 'processed', snapshotId);
}

export async function writeSnapshot(
  dataDir: string,
  meta: SnapshotMeta,
  links: LinkRecord[],
  geometry: GeometryStore,
  restrictions: TurnRestriction[],
): Promise<string> {
  const dir = snapshotDir(dataDir, meta.snapshotId);
  await mkdir(dir, { recursive: true });

  const lines = links.map((l) => JSON.stringify(l)).join('\n');
  await writeFile(path.join(dir, 'links.ndjson'), lines + '\n', 'utf8');
  await writeFile(
    path.join(dir, 'geometry.bin'),
    Buffer.from(
      geometry.coords.buffer,
      geometry.coords.byteOffset,
      geometry.coords.byteLength,
    ),
  );
  await writeFile(
    path.join(dir, 'geometry-offset.bin'),
    Buffer.from(
      geometry.offset.buffer,
      geometry.offset.byteOffset,
      geometry.offset.byteLength,
    ),
  );
  await writeFile(
    path.join(dir, 'restrictions.json'),
    JSON.stringify(restrictions),
    'utf8',
  );
  await writeFile(path.join(dir, 'meta.json'), JSON.stringify(meta, null, 2), 'utf8');
  return dir;
}

export async function loadSnapshot(
  dataDir: string,
  snapshotId: string,
): Promise<LoadedSnapshot> {
  const dir = snapshotDir(dataDir, snapshotId);
  const meta = JSON.parse(
    await readFile(path.join(dir, 'meta.json'), 'utf8'),
  ) as SnapshotMeta;

  const coordBuf = await readFile(path.join(dir, 'geometry.bin'));
  const offBuf = await readFile(path.join(dir, 'geometry-offset.bin'));
  const geometry: GeometryStore = {
    coords: new Float64Array(
      coordBuf.buffer,
      coordBuf.byteOffset,
      coordBuf.byteLength / 8,
    ),
    offset: new Int32Array(offBuf.buffer, offBuf.byteOffset, offBuf.byteLength / 4),
  };

  const links: LinkRecord[] = [];
  const rl = createInterface({
    input: createReadStream(path.join(dir, 'links.ndjson'), 'utf8'),
    crlfDelay: Infinity,
  });
  for await (const line of rl) {
    if (line.length > 0) links.push(JSON.parse(line) as LinkRecord);
  }

  const restrictions = JSON.parse(
    await readFile(path.join(dir, 'restrictions.json'), 'utf8'),
  ) as TurnRestriction[];

  const built = buildGraph(links, geometry, restrictions);

  let coreLink: Uint8Array | null = null;
  if (meta.analysisExtent) {
    const ext = meta.analysisExtent;
    coreLink = new Uint8Array(links.length);
    for (const l of links) {
      const s = geometry.offset[l.linkId];
      const e = geometry.offset[l.linkId + 1];
      let inside = false;
      for (let i = s; i < e; i += 2) {
        const x = geometry.coords[i];
        const y = geometry.coords[i + 1];
        if (x >= ext.xmin && x <= ext.xmax && y >= ext.ymin && y <= ext.ymax) {
          inside = true;
          break;
        }
      }
      coreLink[l.linkId] = inside ? 1 : 0;
    }
  }

  return {
    meta,
    links,
    graph: built.graph,
    geometry,
    restrictions,
    coreLink,
    inferredJoins: built.inferredJoins,
  };
}
