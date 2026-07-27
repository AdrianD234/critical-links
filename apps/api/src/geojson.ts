/**
 * GeoJSON emission.
 *
 * Analysis happens in EPSG:2193; GeoJSON is WGS84 by specification, so this is
 * the only place coordinates are reprojected. Nothing downstream measures
 * distance on these coordinates.
 */

import { nztmToLatLon, type RoadGraph } from '@nzcl/core';
import type { LinkRecord } from '@nzcl/core';

export interface Feature {
  type: 'Feature';
  geometry: { type: 'LineString'; coordinates: [number, number][] };
  properties: Record<string, unknown>;
}

export interface FeatureCollection {
  type: 'FeatureCollection';
  features: Feature[];
}

const ROUND = 1e7; // ~1 cm at NZ latitudes; keeps payloads small

export function linkFeature(
  graph: RoadGraph,
  link: LinkRecord,
  properties: Record<string, unknown> = {},
): Feature {
  const coords = graph.linkCoords(link.linkId);
  const out: [number, number][] = [];
  for (let i = 0; i < coords.length; i += 2) {
    const { lat, lon } = nztmToLatLon(coords[i], coords[i + 1]);
    out.push([Math.round(lon * ROUND) / ROUND, Math.round(lat * ROUND) / ROUND]);
  }
  return {
    type: 'Feature',
    geometry: { type: 'LineString', coordinates: out },
    properties: { linkId: link.linkId, amdsId: link.amdsId, ...properties },
  };
}

/**
 * A route as one feature per traversed arc, so the map can draw direction and
 * so a click on the route resolves to a specific link.
 */
export function routeFeatures(
  graph: RoadGraph,
  links: LinkRecord[],
  arcIds: number[],
): Feature[] {
  return arcIds.map((arc, order) => {
    const linkId = graph.arcLink[arc];
    const link = links[linkId];
    const coords = graph.linkCoords(linkId);
    const pts: [number, number][] = [];
    for (let i = 0; i < coords.length; i += 2) {
      const { lat, lon } = nztmToLatLon(coords[i], coords[i + 1]);
      pts.push([Math.round(lon * ROUND) / ROUND, Math.round(lat * ROUND) / ROUND]);
    }
    // Arcs traversed in reverse are drawn in travel order.
    if (graph.arcDirection[arc] === 1) pts.reverse();
    return {
      type: 'Feature' as const,
      geometry: { type: 'LineString' as const, coordinates: pts },
      properties: {
        order,
        arcId: arc,
        linkId,
        amdsId: link.amdsId,
        roadName: link.roadName,
        lengthM: Math.round(link.lengthM * 10) / 10,
        direction: graph.arcDirection[arc] === 0 ? 'forward' : 'reverse',
      },
    };
  });
}

export function bboxOf(features: Feature[]): [number, number, number, number] | null {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const f of features) {
    for (const [x, y] of f.geometry.coordinates) {
      if (x < minX) minX = x;
      if (y < minY) minY = y;
      if (x > maxX) maxX = x;
      if (y > maxY) maxY = y;
    }
  }
  return Number.isFinite(minX) ? [minX, minY, maxX, maxY] : null;
}
