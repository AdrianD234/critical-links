/**
 * Ambient declarations for the tiling libraries used by the reference service.
 *
 * geojson-vt and vt-pbf ship no types. Without these the root build fails on
 * implicit `any`, which is how the build stayed broken unnoticed: it was listed
 * in the scripts but never actually run.
 */

declare module 'geojson-vt' {
  interface TileFeature {
    id?: number;
    type: number;
    geometry: unknown;
    tags: Record<string, unknown>;
  }
  interface Tile {
    features: TileFeature[];
    numPoints: number;
    numSimplified: number;
    numFeatures: number;
  }
  interface Options {
    maxZoom?: number;
    indexMaxZoom?: number;
    indexMaxPoints?: number;
    tolerance?: number;
    extent?: number;
    buffer?: number;
    debug?: number;
    lineMetrics?: boolean;
    promoteId?: string;
    generateId?: boolean;
    maxPointsPerTile?: number;
  }
  interface TileIndex {
    getTile(z: number, x: number, y: number): Tile | null;
    tiles: Record<string, Tile>;
  }
  export default function geojsonvt(data: unknown, options?: Options): TileIndex;
}

declare module 'vt-pbf' {
  const vtpbf: {
    (tile: unknown): Uint8Array;
    fromGeojsonVt(
      layers: Record<string, unknown>,
      options?: { version?: number; extent?: number },
    ): Uint8Array;
  };
  export default vtpbf;
}
