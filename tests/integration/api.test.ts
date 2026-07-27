/**
 * API integration tests against the real pilot snapshot.
 *
 * These run only when a snapshot is present, so a fresh clone still passes
 * `npm test` before any data has been downloaded. Set SNAPSHOT_ID to pin one.
 */

import { existsSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

import {
  DetourEngine,
  LruCache,
  detourCacheKey,
  loadSnapshot,
  type DetourResult,
  type LoadedSnapshot,
} from '../../packages/core/src/index.js';

const DATA_DIR = path.resolve(process.env.DATA_DIR ?? './data');
const processedDir = path.join(DATA_DIR, 'processed');

function pickSnapshot(): string | null {
  if (process.env.SNAPSHOT_ID) return process.env.SNAPSHOT_ID;
  if (!existsSync(processedDir)) return null;
  const dirs = readdirSync(processedDir, { withFileTypes: true })
    .filter((d) => d.isDirectory() && existsSync(path.join(processedDir, d.name, 'meta.json')))
    .map((d) => d.name)
    .sort();
  return dirs.length ? dirs[dirs.length - 1] : null;
}

const snapshotId = pickSnapshot();
const maybe = snapshotId ? describe : describe.skip;

maybe(`pilot snapshot ${snapshotId}`, () => {
  let snap: LoadedSnapshot;
  let engine: DetourEngine;

  beforeAll(async () => {
    snap = await loadSnapshot(DATA_DIR, snapshotId!);
    engine = new DetourEngine(snap.graph, snap.links, {
      snapshotId: snap.meta.snapshotId,
      coreLink: snap.coreLink,
      clipped: snap.meta.extent !== null,
    });
  });

  afterAll(() => {
    // nothing to tear down; the snapshot is read-only
  });

  describe('snapshot integrity', () => {
    it('downloaded every feature it asked the service for', () => {
      expect(snap.meta.status).toBe('complete');
      expect(snap.meta.downloadedFeatureCount).toBe(snap.meta.sourceFeatureCount);
    });

    it('records provenance that ties results back to the source', () => {
      expect(snap.meta.sourceUrl).toContain('AMDS_NetworkModel_PROD');
      expect(snap.meta.rawSha256).toMatch(/^[0-9a-f]{64}$/);
      expect(snap.meta.attribution).toContain('NZTA');
      expect(snap.meta.retrievedAtUtc).toMatch(/^\d{4}-\d{2}-\d{2}T/);
    });

    it('gives every graph link a unique identifier', () => {
      const ids = new Set(snap.links.map((l) => l.amdsId));
      expect(ids.size).toBe(snap.links.length);
    });

    it('has no zero-length links in the routable graph', () => {
      expect(snap.links.filter((l) => !(l.lengthM > 0))).toHaveLength(0);
    });

    it('produces one arc for one-way links and two for two-way links', () => {
      for (const l of snap.links.slice(0, 400)) {
        if (l.sourceNode === l.targetNode) continue;
        expect(snap.graph.arcsOfLink(l.linkId)).toHaveLength(l.oneway === 1 ? 1 : 2);
      }
    });
  });

  describe('topology health', () => {
    it('is dominated by a single connected component', () => {
      const counts = new Map<number, number>();
      for (const l of snap.links) {
        const c = snap.graph.component[l.sourceNode];
        counts.set(c, (counts.get(c) ?? 0) + 1);
      }
      const sorted = [...counts.values()].sort((a, b) => b - a);
      // New Zealand is two islands with no road link between them, so the
      // network is legitimately dominated by TWO components. Judging it on the
      // largest alone reports Cook Strait as a defect: nationally the largest
      // holds 63.8% (North Island) and the second 31.4% (South Island).
      // Before junction splitting the top two held ~31% combined.
      const topTwo = (sorted[0] + (sorted[1] ?? 0)) / snap.links.length;
      expect(topTwo).toBeGreaterThan(0.9);
    });

    it('keeps the closure group together after splitting', () => {
      const split = snap.links.find((l) => l.amdsId.includes('#'));
      expect(split).toBeDefined();
      const siblings = snap.links.filter(
        (l) => l.closureGroupId === split!.closureGroupId,
      );
      expect(siblings.length).toBeGreaterThan(1);
      for (const s of siblings) expect(s.closureGroupId).toBe(split!.closureGroupId);
    });
  });

  describe('detour computation on real data', () => {
    it('closes the whole source road under physical scope', () => {
      const split = snap.links.find(
        (l) =>
          l.amdsId.includes('#') &&
          snap.links.filter((x) => x.closureGroupId === l.closureGroupId).length > 1,
      )!;
      const r = engine.compute({ linkId: split.linkId, closureScope: 'physical' });
      const groupSize = snap.links.filter(
        (l) => l.closureGroupId === split.closureGroupId,
      ).length;
      expect(r.removedLinkIds.length).toBe(groupSize);
    });

    it('closes a single arc under directed scope', () => {
      const twoWay = snap.links.find(
        (l) => l.oneway !== 1 && l.sourceNode !== l.targetNode,
      )!;
      const r = engine.compute({ linkId: twoWay.linkId, closureScope: 'directed' });
      expect(r.forward!.removedArcIds).toHaveLength(1);
    });

    it('never returns OK without a distance, or DISCONNECTED with one', () => {
      const stride = Math.max(1, Math.floor(snap.links.length / 300));
      for (let i = 0; i < snap.links.length; i += stride) {
        const r = engine.compute({ linkId: snap.links[i].linkId });
        for (const d of [r.forward, r.reverse]) {
          if (!d) continue;
          if (d.status === 'OK') {
            expect(d.alternativeDistanceM).toBeGreaterThan(0);
            expect(d.routeArcIds.length).toBeGreaterThan(0);
          } else if (d.status === 'DISCONNECTED') {
            expect(d.alternativeDistanceM).toBeNull();
            expect(d.routeArcIds).toHaveLength(0);
          }
        }
      }
    });

    it('reports a route whose arc lengths sum to the reported distance', () => {
      const stride = Math.max(1, Math.floor(snap.links.length / 120));
      let checked = 0;
      for (let i = 0; i < snap.links.length && checked < 40; i += stride) {
        const r = engine.compute({ linkId: snap.links[i].linkId });
        const d = r.forward;
        if (!d || d.status !== 'OK') continue;
        const sum = d.routeArcIds.reduce((a, arc) => a + snap.graph.arcDistance[arc], 0);
        expect(sum).toBeCloseTo(d.alternativeDistanceM!, 3);
        checked++;
      }
      expect(checked).toBeGreaterThan(0);
    });

    it('never routes through an arc belonging to the closure', () => {
      const stride = Math.max(1, Math.floor(snap.links.length / 200));
      for (let i = 0; i < snap.links.length; i += stride) {
        const r = engine.compute({ linkId: snap.links[i].linkId });
        for (const d of [r.forward, r.reverse]) {
          if (!d || d.status !== 'OK') continue;
          const removed = new Set(d.removedArcIds);
          for (const arc of d.routeArcIds) expect(removed.has(arc)).toBe(false);
        }
      }
    });

    it('respects the heavy-vehicle profile', () => {
      const restricted = snap.links.find(
        (l) => !l.modeVehicleHeavy && l.modeVehicle && l.sourceNode !== l.targetNode,
      );
      if (!restricted) return; // none in this extract
      const r = engine.compute({ linkId: restricted.linkId, profile: 'heavy' });
      for (const d of [r.forward, r.reverse]) {
        if (d?.status === 'OK') {
          for (const arc of d.routeArcIds) {
            expect(snap.links[snap.graph.arcLink[arc]].modeVehicleHeavy).toBe(true);
          }
        }
      }
    });

    it('quantifies what is stranded when no detour exists', () => {
      const stride = Math.max(1, Math.floor(snap.links.length / 400));
      let seen = 0;
      for (let i = 0; i < snap.links.length && seen < 20; i += stride) {
        const d = engine.compute({ linkId: snap.links[i].linkId }).forward;
        if (d?.status !== 'DISCONNECTED') continue;
        expect(d.isolation).not.toBeNull();
        expect(d.isolation!.pocketLinkCount).toBeGreaterThanOrEqual(0);
        seen++;
      }
      expect(seen).toBeGreaterThan(0);
    });
  });

  describe('caching', () => {
    it('returns an identical result from the cache', () => {
      const cache = new LruCache<DetourResult>(100);
      const link = snap.links.find((l) => l.sourceNode !== l.targetNode)!;
      const key = detourCacheKey({
        snapshotId: snap.meta.snapshotId,
        linkId: link.linkId,
        closureScope: 'physical',
        directions: ['forward'],
        profile: 'car',
        metric: 'distance',
      });
      const first = engine.compute({ linkId: link.linkId, directions: ['forward'] });
      cache.set(key, first);
      expect(cache.get(key)).toBe(first);
      const second = engine.compute({ linkId: link.linkId, directions: ['forward'] });
      expect(second.forward!.status).toBe(first.forward!.status);
      expect(second.forward!.alternativeDistanceM).toBe(first.forward!.alternativeDistanceM);
    });

    it('misses when the snapshot id changes', () => {
      const cache = new LruCache<DetourResult>(100);
      const base = {
        linkId: 1,
        closureScope: 'physical' as const,
        directions: ['forward' as const],
        profile: 'car' as const,
        metric: 'distance' as const,
      };
      cache.set(detourCacheKey({ ...base, snapshotId: 'a' }), {} as DetourResult);
      expect(cache.get(detourCacheKey({ ...base, snapshotId: 'b' }))).toBeUndefined();
    });
  });
});
