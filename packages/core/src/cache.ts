/**
 * Detour cache key.
 *
 * Every input that can change the answer is in the key. In particular the
 * snapshot id and the algorithm version are both present, so re-ingesting the
 * source or changing routing semantics makes every previously cached entry
 * unreachable rather than silently wrong.
 */

import { ALGORITHM, ALGORITHM_VERSION } from './types.js';
import type { ClosureScope, Direction, Metric, VehicleProfile } from './types.js';

export interface DetourCacheKeyParts {
  snapshotId: string;
  linkId: number;
  closureScope: ClosureScope;
  directions: Direction[];
  profile: VehicleProfile;
  metric: Metric;
  algorithm?: string;
  algorithmVersion?: string;
}

export function detourCacheKey(p: DetourCacheKeyParts): string {
  const dirs = [...p.directions].sort().join('+') || 'none';
  return [
    p.snapshotId,
    p.linkId,
    p.closureScope,
    dirs,
    p.profile,
    p.metric,
    p.algorithm ?? ALGORITHM,
    p.algorithmVersion ?? ALGORITHM_VERSION,
  ].join('|');
}

/** Bounded LRU. Detour results are large; unbounded growth is not acceptable. */
export class LruCache<V> {
  private map = new Map<string, V>();
  constructor(private readonly limit: number) {}

  get(k: string): V | undefined {
    const v = this.map.get(k);
    if (v !== undefined) {
      this.map.delete(k);
      this.map.set(k, v);
    }
    return v;
  }

  set(k: string, v: V): void {
    if (this.map.has(k)) this.map.delete(k);
    this.map.set(k, v);
    while (this.map.size > this.limit) {
      const oldest = this.map.keys().next().value as string | undefined;
      if (oldest === undefined) break;
      this.map.delete(oldest);
    }
  }

  get size(): number {
    return this.map.size;
  }

  clear(): void {
    this.map.clear();
  }
}
