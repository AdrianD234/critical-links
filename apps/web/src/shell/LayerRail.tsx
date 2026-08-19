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
  FlagIcon,
  HomeExtentIcon,
  InfoIcon,
  LayersIcon,
  WarningIcon,
} from './icons.js';
import MapViewControl from './MapViewControl.js';
import type { MapViewMode } from '../state/mapView.js';

/* The map view is a presentation MODE, not a boolean — the modes and their
 * meanings are documented where they live, in state/mapView.ts. */
export interface MapLayerState {
  network: boolean;
  basemap: MapViewMode;
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

export default function LayerRail({
  layers,
  onToggle,
  onMapView,
  linzAvailable = true,
  onAbout,
  onHome,
  homeLabel,
}: {
  layers: MapLayerState;
  onToggle: (id: 'network' | 'labels') => void;
  /** Set the map-view presentation mode. */
  onMapView: (mode: MapViewMode) => void;
  /**
   * False when no LINZ key is configured. The selector stays — Analysis and
   * Off need no key — and the modes that cannot exist disable individually,
   * each saying why.
   */
  linzAvailable?: boolean;
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

      <MapViewControl
        value={layers.basemap}
        onChange={onMapView}
        linzAvailable={linzAvailable}
      />

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
