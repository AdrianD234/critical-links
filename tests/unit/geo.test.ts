/**
 * NZTM2000 projection accuracy.
 *
 * The reference pairs in tests/fixtures/projection-ground-truth.json were
 * produced by asking the NZTA ArcGIS service for the SAME features twice, once
 * with outSR=2193 and once with outSR=4326. That makes Esri's projection
 * engine the independent check on ours - the test cannot pass by agreeing with
 * itself.
 *
 * Regenerate with: npm run discover -- --refresh-projection-truth
 */

import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

import { latLonToNztm, nztmToLatLon, polylineLength } from '../../packages/core/src/geo.js';

interface RefPoint {
  region: string;
  amdsId: string;
  nztm: [number, number];
  wgs84: [number, number];
}

const refs: RefPoint[] = JSON.parse(
  readFileSync(
    path.join(process.cwd(), 'tests/fixtures/projection-ground-truth.json'),
    'utf8',
  ),
);

describe('NZTM2000 <-> WGS84 against Esri-reprojected ground truth', () => {
  it('has reference points spanning multiple regions', () => {
    expect(refs.length).toBeGreaterThanOrEqual(8);
    expect(new Set(refs.map((r) => r.region)).size).toBeGreaterThanOrEqual(3);
  });

  it('converts NZTM -> WGS84 to better than 1 mm', () => {
    let worst = 0;
    for (const r of refs) {
      const got = nztmToLatLon(r.nztm[0], r.nztm[1]);
      // 1e-8 degrees of latitude is about 1.1 mm.
      const dLat = Math.abs(got.lat - r.wgs84[1]);
      const dLon = Math.abs(got.lon - r.wgs84[0]);
      worst = Math.max(worst, dLat, dLon);
    }
    expect(worst).toBeLessThan(1e-8);
  });

  it('converts WGS84 -> NZTM to better than 1 mm', () => {
    let worst = 0;
    for (const r of refs) {
      const got = latLonToNztm(r.wgs84[1], r.wgs84[0]);
      worst = Math.max(worst, Math.hypot(got.x - r.nztm[0], got.y - r.nztm[1]));
    }
    expect(worst).toBeLessThan(0.001);
  });

  /**
   * Measured worst case on the current fixture set (24 points, 4 regions):
   *   inverse vs Esri    0.11 mm
   *   forward vs Esri    0.19 mm
   *   round-trip         0.30 mm
   * The residual is Redfearn series truncation. It is five orders of magnitude
   * below the positional accuracy of the source centrelines and WGS84 is only
   * ever used for display, so this is not a material error - but the bound is
   * asserted rather than assumed.
   */
  it('round-trips to better than 1 mm', () => {
    let worst = 0;
    for (const r of refs) {
      const ll = nztmToLatLon(r.nztm[0], r.nztm[1]);
      const back = latLonToNztm(ll.lat, ll.lon);
      worst = Math.max(worst, Math.hypot(back.x - r.nztm[0], back.y - r.nztm[1]));
    }
    expect(worst).toBeLessThan(0.001);
  });
});

describe('polyline length', () => {
  it('sums segment lengths in projected metres', () => {
    expect(polylineLength([0, 0, 3, 4])).toBeCloseTo(5, 12);
    expect(polylineLength([0, 0, 100, 0, 100, 100])).toBeCloseTo(200, 12);
  });

  it('is zero for a degenerate single-vertex run', () => {
    expect(polylineLength([5, 5])).toBe(0);
  });

  it('honours the start/end window used by the CSR geometry store', () => {
    const coords = [0, 0, 10, 0, /* next link */ 50, 50, 50, 80];
    expect(polylineLength(coords, 0, 4)).toBeCloseTo(10, 12);
    expect(polylineLength(coords, 4, 8)).toBeCloseTo(30, 12);
  });
});
