/**
 * What the active snapshot covers, and where the map should sit for it.
 *
 * WHY THIS IS NOT DERIVED FROM `clippedExtract`
 *
 * It used to be. Anything clipped was labelled "Wellington pilot", which was
 * true only by accident: the same code would have announced an Auckland
 * extract, a Canterbury extract or a developer's custom bounding box as
 * Wellington. And a national snapshot was distinguishable from a very large
 * regional one only by a boolean that does not carry that distinction.
 *
 * Coverage is now recorded by the ingest, which knows it exactly. This module
 * reads it, and falls back conservatively when the backend does not supply it —
 * to "unknown", never to a guess with a place name in it.
 */

import type { NetworkMetadata } from './types.js';

export type CoverageKind = 'national' | 'regional' | 'synthetic' | 'unknown';

export interface Coverage {
  kind: CoverageKind;
  /** "New Zealand", "Wellington pilot", "Auckland pilot". */
  name: string;
  isNational: boolean;
  /** The bounds the map fits with nothing selected. */
  extent: [number, number, number, number];
  /** One line under the snapshot chip. Null when nothing needs saying. */
  caveat: string | null;
}

/**
 * New Zealand, as map bounds.
 *
 * Deliberately generous at the corners: it includes Rakiura/Stewart Island in
 * the south and the far north, because a "national" view that quietly omits
 * either would misrepresent the coverage in exactly the way this whole change
 * is meant to stop.
 *
 * The Chathams are excluded. They are New Zealand, but they sit 800 km east
 * and including them would zoom the initial view out far enough to make the
 * main islands unreadable. AMDS carries almost no Chatham network, so the cost
 * of the omission is small and the cost of including them is every session.
 */
export const NZ_BOUNDS: [number, number, number, number] = [
  166.3, -47.4, 178.7, -34.3,
];

export function coverageOf(meta: NetworkMetadata | null): Coverage {
  if (!meta) {
    return {
      kind: 'unknown',
      name: 'Loading',
      isNational: false,
      extent: NZ_BOUNDS,
      caveat: null,
    };
  }

  const c = meta.coverage;

  /*
   * No coverage block: an older backend. Say "unknown extract" rather than
   * inventing a place name — the previous behaviour is what produced the
   * problem this module exists to fix.
   */
  if (!c) {
    return {
      kind: 'unknown',
      name: meta.clippedExtract ? 'Clipped extract' : 'Full extract',
      isNational: !meta.clippedExtract,
      extent: extentFrom(meta) ?? NZ_BOUNDS,
      caveat: meta.clippedExtract
        ? 'Clipped extract — coverage is not reported by this backend.'
        : null,
    };
  }

  const extent = c.displayExtentWgs84
    ? ([
        c.displayExtentWgs84.southWest.lon,
        c.displayExtentWgs84.southWest.lat,
        c.displayExtentWgs84.northEast.lon,
        c.displayExtentWgs84.northEast.lat,
      ] as [number, number, number, number])
    : NZ_BOUNDS;

  return {
    kind: c.kind,
    name: c.name,
    isNational: c.isNational,
    extent: c.isNational ? NZ_BOUNDS : extent,
    caveat: caveatFor(c.kind),
  };
}

function caveatFor(kind: CoverageKind): string | null {
  switch (kind) {
    case 'national':
      /* Stated positively, and precisely: the analytical subset is the
       * current vehicle-accessible AMDS network, not every raw AMDS record. */
      return 'National vehicle-road network.';
    case 'regional':
      return 'Regional validation snapshot — not national coverage.';
    case 'synthetic':
      return 'Synthetic test fixture — not real network data.';
    case 'unknown':
      return null;
  }
}

/** Fall back to the analysis extent when no display extent is published. */
function extentFrom(
  meta: NetworkMetadata,
): [number, number, number, number] | null {
  const a = meta.analysisExtentWgs84;
  if (!a) return null;
  return [a.southWest.lon, a.southWest.lat, a.northEast.lon, a.northEast.lat];
}
