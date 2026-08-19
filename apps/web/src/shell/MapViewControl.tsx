/**
 * The explicit map-view selector.
 *
 * This replaces a cycle button. Three modes could survive as a cycle whose
 * tooltip named the next state; with four, guessing what one more click shows
 * stops being tolerable, and the choices deserve to be visible. The trigger
 * stays a rail icon; the choices open beside it as a real radio group, so the
 * current mode is visibly checked rather than living in a tooltip.
 *
 * Native radio inputs on purpose: they give the ordinary keyboard model —
 * arrow keys move and select, disabled options are skipped — without
 * reimplementing focus management. Escape closes and returns focus to the
 * trigger; selection announces itself the way any radio does.
 *
 * Keyless builds keep the control. Streets and Aerial disable individually
 * with the reason attached, because a whole control that vanishes when LINZ
 * is unconfigured would also take away Off — which needs no key at all.
 */

import { useEffect, useRef, useState } from 'react';

import {
  MAP_VIEW_LABELS,
  MAP_VIEW_MODES,
  requiresLinz,
  type MapViewMode,
} from '../state/mapView.js';
import { BasemapIcon } from './icons.js';

export default function MapViewControl({
  value,
  onChange,
  linzAvailable,
}: {
  value: MapViewMode;
  onChange: (mode: MapViewMode) => void;
  /** False when no LINZ Basemaps key is configured in this build. */
  linzAvailable: boolean;
}) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);

  /* A press anywhere outside dismisses, as any popover. */
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', onDown);
    return () => document.removeEventListener('pointerdown', onDown);
  }, [open]);

  /* Opening puts focus on the current choice, so arrow keys work at once.
   * The checked radio can be disabled — a keyless build restoring nothing —
   * so the first operable option is the fallback. */
  useEffect(() => {
    if (!open) return;
    const radio =
      wrap.current?.querySelector<HTMLInputElement>(
        'input[type="radio"]:checked:enabled',
      ) ??
      wrap.current?.querySelector<HTMLInputElement>(
        'input[type="radio"]:enabled',
      );
    radio?.focus();
  }, [open]);

  return (
    <div
      className="mapview"
      ref={wrap}
      onKeyDown={(e) => {
        if (e.key === 'Escape' && open) {
          e.stopPropagation();
          setOpen(false);
          trigger.current?.focus();
        }
      }}
      onBlur={(e) => {
        /* Tabbing out closes without stealing focus back. */
        if (open && !wrap.current?.contains(e.relatedTarget as Node)) {
          setOpen(false);
        }
      }}
    >
      <button
        ref={trigger}
        type="button"
        aria-haspopup="true"
        aria-expanded={open}
        aria-label={`Map view: ${MAP_VIEW_LABELS[value]}`}
        title={`Map view — ${MAP_VIEW_LABELS[value]}`}
        onClick={() => setOpen((o) => !o)}
      >
        <BasemapIcon />
      </button>

      {open && (
        <fieldset className="mapview-pop">
          <legend>Map view</legend>
          {MAP_VIEW_MODES.map((mode) => {
            const disabled = !linzAvailable && requiresLinz(mode);
            return (
              <label
                key={mode}
                className={disabled ? 'mapview-opt is-disabled' : 'mapview-opt'}
                title={disabled ? 'LINZ Basemaps key not configured.' : undefined}
              >
                <input
                  type="radio"
                  name="map-view"
                  value={mode}
                  checked={value === mode}
                  disabled={disabled}
                  onChange={() => onChange(mode)}
                />
                <span>{MAP_VIEW_LABELS[mode]}</span>
              </label>
            );
          })}
          {!linzAvailable && (
            <p className="mapview-note">LINZ Basemaps key not configured.</p>
          )}
          <p className="mapview-note">
            LINZ context only. Routing and closure analysis use the AMDS
            represented network.
          </p>
        </fieldset>
      )}
    </div>
  );
}
