/*
 * The dark layer/tool rail.
 *
 * Icon-only controls, each with a real accessible name and a tooltip. These
 * toggle map state rather than navigate, so they are `aria-pressed` buttons
 * rather than links or tabs.
 *
 * The rail is intentionally short. It holds the controls that change what the
 * map shows; everything that changes what is *calculated* lives in the
 * inspector next to the numbers it affects.
 */

import {
  BasemapIcon,
  FlagIcon,
  HomeExtentIcon,
  InfoIcon,
  LayersIcon,
  WarningIcon,
} from './icons.js';

/**
 * The basemap is a presentation MODE, not a boolean.
 *
 *   analysis  the quiet Graphite view - water, landcover, buildings only, so
 *             the analytical network is the only thing that reads as a road.
 *             The default.
 *   topo      LINZ streets and their names as well, for orientation. Context
 *             only: routing and closure analysis still use the AMDS
 *             represented network, and LINZ roads are not clickable.
 *   off       analytical network on the plain graphite ground.
 */
export type BasemapMode = 'analysis' | 'topo' | 'off';

export const BASEMAP_MODES: BasemapMode[] = ['analysis', 'topo', 'off'];

export interface MapLayerState {
  network: boolean;
  basemap: BasemapMode;
  labels: boolean;
}

const BOOLEAN_TOGGLES: {
  id: 'network' | 'labels';
  label: string;
  Icon: (p: { size?: number }) => JSX.Element;
}[] = [
  { id: 'network', label: 'Road network', Icon: LayersIcon },
  { id: 'labels', label: 'Map labels', Icon: FlagIcon },
];

const BASEMAP_TITLES: Record<BasemapMode, string> = {
  analysis:
    'Basemap: analysis — quiet context. Click for topographic streets. ' +
    'Routing and closure analysis use the AMDS represented network.',
  topo:
    'Basemap: topographic — LINZ streets and names, context only. Routing and ' +
    'closure analysis use the AMDS represented network. Click to hide the basemap.',
  off: 'Basemap: off. Click for the quiet analysis basemap.',
};

export default function LayerRail({
  layers,
  onToggle,
  onBasemapMode,
  basemapAvailable = true,
  onAbout,
  onHome,
  homeLabel,
}: {
  layers: MapLayerState;
  onToggle: (id: 'network' | 'labels') => void;
  /** Advance the basemap presentation mode. */
  onBasemapMode: () => void;
  /**
   * False when no LINZ key is configured. The button is then disabled and says
   * why, rather than appearing to work and doing nothing - without the key
   * there is no basemap source at all, in any mode.
   */
  basemapAvailable?: boolean;
  onAbout: () => void;
  onHome: () => void;
  /** What Home fits — "New Zealand", "Wellington pilot". */
  homeLabel: string;
}) {
  return (
    <div className="rail" role="toolbar" aria-orientation="vertical" aria-label="Map layers">
      {/* Named for what it actually fits, so on a regional snapshot it does
        * not promise to show the country. */}
      <button
        type="button"
        aria-label={`Zoom to full extent — ${homeLabel}`}
        title={`Full extent — ${homeLabel}`}
        onClick={onHome}
      >
        <HomeExtentIcon />
      </button>

      <button
        type="button"
        aria-pressed={layers.network}
        aria-label="Road network"
        title="Road network"
        onClick={() => onToggle('network')}
      >
        <LayersIcon />
      </button>

      {/* Cycles analysis -> topo -> off. `aria-pressed` reflects "any basemap
        * showing", and the full mode is in the accessible name. */}
      <button
        type="button"
        aria-pressed={layers.basemap !== 'off'}
        aria-label={
          basemapAvailable
            ? BASEMAP_TITLES[layers.basemap]
            : 'Basemap unavailable — LINZ Basemaps key not configured'
        }
        title={
          basemapAvailable
            ? BASEMAP_TITLES[layers.basemap]
            : 'LINZ Basemaps key not configured'
        }
        disabled={!basemapAvailable}
        onClick={onBasemapMode}
      >
        <BasemapIcon />
      </button>

      <button
        type="button"
        aria-pressed={layers.labels}
        aria-label="Map labels"
        title="Map labels"
        onClick={() => onToggle('labels')}
      >
        <FlagIcon />
      </button>

      {/*
        Data-quality flags is disabled, not hidden, because the layer is
        planned and its absence is worth stating. It used to toggle its own
        pressed state while NetworkMap consumed nothing — a control that looks
        like it did something and did not.
      */}
      <button
        type="button"
        disabled
        aria-label="Data-quality flags — not available in this snapshot"
        title="Data-quality flags — layer not yet available"
        onClick={undefined}
      >
        <WarningIcon />
      </button>

      <button
        type="button"
        className="rail-spacer"
        aria-label="About this analysis and its limitations"
        title="About this analysis"
        onClick={onAbout}
      >
        <InfoIcon />
      </button>
    </div>
  );
}
