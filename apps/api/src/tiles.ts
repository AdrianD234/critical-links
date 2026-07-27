/**
 * Mapbox Vector Tile service for the routable network.
 *
 * The national network is far too large to hand a browser as one GeoJSON
 * document, so the map consumes real vector tiles. `geojson-vt` builds the tile
 * pyramid in memory once at start-up and `vt-pbf` encodes each tile on request.
 *
 * `linkId` is carried through as the feature id so MapLibre feature-state can
 * highlight the selected link and its closure group without a round trip.
 */

import geojsonvt from 'geojson-vt';
import vtpbf from 'vt-pbf';

import { nztmToLatLon, type LinkRecord, type RoadGraph } from '@nzcl/core';

export interface TileIndex {
  getTile(z: number, x: number, y: number): Buffer | null;
  featureCount: number;
  maxZoom: number;
}

export function buildTileIndex(
  graph: RoadGraph,
  links: LinkRecord[],
  coreLink: Uint8Array | null,
  maxZoom = 15,
): TileIndex {
  const features = links.map((l) => {
    const c = graph.linkCoords(l.linkId);
    const coordinates: [number, number][] = [];
    for (let i = 0; i < c.length; i += 2) {
      const { lat, lon } = nztmToLatLon(c[i], c[i + 1]);
      coordinates.push([
        Math.round(lon * 1e6) / 1e6,
        Math.round(lat * 1e6) / 1e6,
      ]);
    }
    return {
      type: 'Feature' as const,
      // MapLibre feature-state needs a numeric id.
      id: l.linkId,
      geometry: { type: 'LineString' as const, coordinates },
      properties: {
        linkId: l.linkId,
        amdsId: l.amdsId,
        roadName: l.roadName ?? '',
        oneway: l.oneway === 1 ? 1 : 0,
        stateHighway: l.assetOwnerOrganisation === 1 ? 1 : 0,
        lifeline: l.lifeLineRoute ? 1 : 0,
        core: coreLink ? coreLink[l.linkId] : 1,
        lengthM: Math.round(l.lengthM),
      },
    };
  });

  const index = geojsonvt(
    { type: 'FeatureCollection', features },
    {
      maxZoom,
      indexMaxZoom: 8,
      tolerance: 3,
      extent: 4096,
      buffer: 64,
      // Keep every link: dropping features would silently hide roads from the
      // very analysis this tool exists to perform.
      maxPointsPerTile: 200_000,
      promoteId: 'linkId',
    } as any,
  );

  return {
    featureCount: features.length,
    maxZoom,
    getTile(z, x, y) {
      const tile = index.getTile(z, x, y);
      if (!tile || tile.features.length === 0) return null;
      const buf = vtpbf.fromGeojsonVt({ network: tile }, { version: 2 });
      return Buffer.from(buf);
    },
  };
}
