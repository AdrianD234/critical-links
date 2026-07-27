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
  InfoIcon,
  LayersIcon,
  WarningIcon,
} from './icons.js';

export interface MapLayerState {
  network: boolean;
  basemap: boolean;
  labels: boolean;
  quality: boolean;
}

const TOGGLES: {
  id: keyof MapLayerState;
  label: string;
  Icon: (p: { size?: number }) => JSX.Element;
}[] = [
  { id: 'network', label: 'Road network', Icon: LayersIcon },
  { id: 'basemap', label: 'Basemap imagery', Icon: BasemapIcon },
  { id: 'labels', label: 'Map labels', Icon: FlagIcon },
  { id: 'quality', label: 'Data-quality flags', Icon: WarningIcon },
];

export default function LayerRail({
  layers,
  onToggle,
  onAbout,
}: {
  layers: MapLayerState;
  onToggle: (id: keyof MapLayerState) => void;
  onAbout: () => void;
}) {
  return (
    <div className="rail" role="toolbar" aria-orientation="vertical" aria-label="Map layers">
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
