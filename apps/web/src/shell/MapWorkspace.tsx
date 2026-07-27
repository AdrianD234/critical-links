/*
 * The map workspace: the map canvas plus its furniture.
 *
 * Everything here is absolutely positioned inside a relatively positioned
 * container, so the legend, scale bar and attribution cannot be displaced by
 * content elsewhere in the grid. The concept renders caught exactly that
 * failure — an unconstrained grid row let the map grow past the viewport and
 * pushed all three off-screen.
 *
 * Attribution is not optional and is not collapsible. The LINZ basemap licence
 * and the NZTA AMDS source both require it to be visible.
 */

import type { ReactNode } from 'react';

export interface ScaleReading {
  label: string;
  widthPx: number;
}

export default function MapWorkspace({
  children,
  closureBadge,
  legend,
  scale,
  attribution,
  basemapAttribution,
}: {
  children: ReactNode;
  closureBadge: ReactNode;
  legend: { colour: string; label: string; dashed?: boolean }[];
  scale: ScaleReading | null;
  /** The data source credit, from the API. */
  attribution: string;
  /** The basemap credit. Empty when no basemap is configured. */
  basemapAttribution: string;
}) {
  return (
    <main className="workspace" id="map-workspace">
      {children}

      {closureBadge}

      {/*
        Scale and legend stack in one column rather than being positioned
        independently from the bottom edge. Independently placed, the scale bar
        sat behind the legend whenever the legend grew — and it grows with the
        result, gaining rows for the comparison route and stranded links.
      */}
      <div className="map-corner">
        {scale && (
          <div className="map-scale" aria-hidden="true">
            <div>{scale.label}</div>
            <div className="bar" style={{ width: `${scale.widthPx}px` }} />
          </div>
        )}

        {legend.length > 0 && (
          <div className="map-legend" aria-hidden="true">
            {legend.map((l) => (
              <div className="row" key={l.label}>
                <span
                  className="swatch"
                  style={{
                    borderTopColor: l.colour,
                    borderTopStyle: l.dashed ? 'dashed' : 'solid',
                  }}
                />
                <span>{l.label}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/*
        Both credits, always. The API's attribution string covers the AMDS
        source only; the LINZ basemap licence (CC BY 4.0) requires its own
        visible credit, and it is not optional or collapsible.
      */}
      <div className="map-attrib">
        {basemapAttribution && <span>{basemapAttribution} · </span>}
        {attribution}
      </div>
    </main>
  );
}
