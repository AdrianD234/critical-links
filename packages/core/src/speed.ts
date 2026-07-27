/**
 * Speed assignment for the time metric.
 *
 * IMPORTANT: the AMDS Network Model carries NO speed attribute. Discovery of
 * layer 1 returned 31 fields and none of them is a speed limit or an observed
 * travel speed (see docs/SOURCE_DISCOVERY.md for the full field list). Every
 * speed below is therefore an ASSUMPTION derived from asset type, surface and
 * owning authority, and every link built from this table is stamped
 * `speedSource: 'estimated_asset_type'`.
 *
 * Consequences, which the API and UI both surface:
 *   - the DISTANCE metric is the defensible one for the MVP;
 *   - time results are labelled TIME_ESTIMATED and must not be presented as
 *     observed or even as posted travel time;
 *   - enriching from the National Speed Limit Register replaces these values
 *     and sets speedSource: 'nslr'. That is the documented next step in
 *     docs/VALIDATION_PLAN.md.
 */

import type { SpeedSource } from './types.js';

/** AMDS assetOwnerOrganisation code for NZTA (state highway network). */
export const OWNER_NZTA = 1;

export interface SpeedAssignment {
  speedKph: number;
  speedSource: SpeedSource;
}

export interface SpeedInputs {
  modelAssetType: number | null;
  surfaceType: number | null;
  assetOwnerOrganisation: number | null;
}

/**
 * Fallback speed table. Deliberately coarse - a fine-grained guess would imply
 * precision the source does not support.
 */
export function assignSpeed(i: SpeedInputs): SpeedAssignment {
  const src: SpeedSource = 'estimated_asset_type';

  // Connectors are short stitching geometries at intersections and car parks.
  if (i.modelAssetType === 6) return { speedKph: 20, speedSource: src };

  // Unsurfaced / metalled carriageway.
  if (i.surfaceType === 2 || i.surfaceType === 3) {
    return { speedKph: 40, speedSource: src };
  }

  // State highways are NZTA-owned and predominantly open road.
  if (i.assetOwnerOrganisation === OWNER_NZTA) {
    return { speedKph: 90, speedSource: src };
  }

  // Everything else sealed: territorial-authority road, urban default.
  return { speedKph: 50, speedSource: src };
}
