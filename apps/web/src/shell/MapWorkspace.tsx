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
}: {
  children: ReactNode;
  closureBadge: ReactNode;
  legend: { colour: string; label: string; dashed?: boolean }[];
  scale: ScaleReading | null;
  attribution: string;
}) {
  return (
    <main className="workspace" id="map-workspace">
      {children}

      {closureBadge}

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

      {scale && (
        <div className="map-scale" aria-hidden="true">
          <div>{scale.label}</div>
          <div className="bar" style={{ width: `${scale.widthPx}px` }} />
        </div>
      )}

      <div className="map-attrib">
        {attribution || 'Basemap © LINZ, CC BY 4.0'}
      </div>
    </main>
  );
}
