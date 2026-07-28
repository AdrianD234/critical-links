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

export interface MapLayerState {
  network: boolean;
  basemap: boolean;
  labels: boolean;
}

const TOGGLES: {
  id: keyof MapLayerState;
  label: string;
  Icon: (p: { size?: number }) => JSX.Element;
}[] = [
  { id: 'network', label: 'Road network', Icon: LayersIcon },
  /* "Basemap context", not "Basemap imagery": the basemap is styled vector
   * water, landcover and buildings, not photography. */
  { id: 'basemap', label: 'Basemap context', Icon: BasemapIcon },
  { id: 'labels', label: 'Map labels', Icon: FlagIcon },
];

export default function LayerRail({
  layers,
  onToggle,
  onAbout,
  onHome,
  homeLabel,
}: {
  layers: MapLayerState;
  onToggle: (id: keyof MapLayerState) => void;
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

      {TOGGLES.map(({ id, label, Icon }) => (
        <button
          key={id}
          type="button"
          aria-pressed={layers[id]}
          aria-label={label}
          title={label}
          onClick={() => onToggle(id)}
        >
          <Icon />
        </button>
      ))}

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
