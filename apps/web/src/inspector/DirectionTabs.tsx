/*
 * Forward / Reverse / Compare.
 *
 * A real tab list: `role="tablist"` with roving tabindex, arrow-key navigation
 * and Home/End, so it behaves the way a screen-reader user expects rather than
 * being three buttons that happen to sit in a row.
 *
 * A direction the snapshot does not represent — a one-way carriageway has only
 * one — is disabled with the reason on the control, not hidden. Hiding it would
 * make a one-way link look identical to a two-way one whose reverse result
 * failed.
 */

import { useRef } from 'react';

import type { DirectionKey } from '../api/scenario.js';

export type DirectionView = DirectionKey | 'compare';

export default function DirectionTabs({
  view,
  onChange,
  available,
  panelId,
}: {
  view: DirectionView;
  onChange: (v: DirectionView) => void;
  available: { forward: boolean; reverse: boolean };
  panelId: string;
}) {
  const ref = useRef<HTMLDivElement>(null);

  const tabs: { id: DirectionView; label: string; enabled: boolean; why?: string }[] = [
    {
      id: 'forward',
      label: 'Forward',
      enabled: available.forward,
      why: available.forward ? undefined : 'Not represented for this link',
    },
    {
      id: 'reverse',
      label: 'Reverse',
      enabled: available.reverse,
      why: available.reverse ? undefined : 'Not represented for this link',
    },
    {
      id: 'compare',
      label: 'Compare',
      enabled: available.forward && available.reverse,
      why:
        available.forward && available.reverse
          ? undefined
          : 'Both directions are needed to compare',
    },
  ];

  function onKeyDown(e: React.KeyboardEvent) {
    const usable = tabs.filter((t) => t.enabled);
    const i = usable.findIndex((t) => t.id === view);
    if (i < 0) return;

    let next = -1;
    if (e.key === 'ArrowRight') next = (i + 1) % usable.length;
    else if (e.key === 'ArrowLeft') next = (i - 1 + usable.length) % usable.length;
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = usable.length - 1;
    if (next < 0) return;

    e.preventDefault();
    const id = usable[next]!.id;
    onChange(id);
    ref.current
      ?.querySelector<HTMLButtonElement>(`[data-tab="${id}"]`)
      ?.focus();
  }

  return (
    <div
      className="dirtabs"
      role="tablist"
      aria-label="Direction of travel"
      ref={ref}
      onKeyDown={onKeyDown}
    >
      {tabs.map((t) => (
        <button
          key={t.id}
          type="button"
          role="tab"
          data-tab={t.id}
          aria-selected={view === t.id}
          aria-controls={panelId}
          tabIndex={view === t.id ? 0 : -1}
          disabled={!t.enabled}
          title={t.why}
          onClick={() => onChange(t.id)}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
