/*
 * The cool-paper contextual inspector: the frame, not the content.
 *
 * Owns the scroll region, the sticky footer, the desktop resize grip and the
 * empty state. What goes inside is passed in, so Stage 4's result components
 * and this frame can change independently.
 *
 * The panel is a landmark region with an accessible name so a screen-reader
 * user can jump straight to the result rather than tabbing through the map.
 */

import { useCallback, useEffect, useRef, type ReactNode } from 'react';

import type { Coverage } from '../api/coverage.js';

export const INSPECTOR_MIN = 340;
export const INSPECTOR_MAX = 560;

export default function ContextInspector({
  children,
  footer,
  width,
  onWidthChange,
  resizable,
}: {
  children: ReactNode;
  footer?: ReactNode;
  width: number;
  onWidthChange: (w: number) => void;
  resizable: boolean;
}) {
  const dragging = useRef(false);

  const clamp = (w: number) =>
    Math.min(INSPECTOR_MAX, Math.max(INSPECTOR_MIN, w));

  useEffect(() => {
    if (!resizable) return;

    const onMove = (e: PointerEvent) => {
      if (!dragging.current) return;
      /* The grip is on the panel's left edge, so the width is the distance
       * from the pointer to the right edge of the window. */
      onWidthChange(clamp(window.innerWidth - e.clientX));
    };
    const onUp = () => {
      dragging.current = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };

    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    return () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
  }, [resizable, onWidthChange]);

  const onGripKey = useCallback(
    (e: React.KeyboardEvent) => {
      const step = e.shiftKey ? 48 : 16;
      if (e.key === 'ArrowLeft') {
        e.preventDefault();
        onWidthChange(clamp(width + step));
      } else if (e.key === 'ArrowRight') {
        e.preventDefault();
        onWidthChange(clamp(width - step));
      } else if (e.key === 'Home') {
        e.preventDefault();
        onWidthChange(INSPECTOR_MIN);
      } else if (e.key === 'End') {
        e.preventDefault();
        onWidthChange(INSPECTOR_MAX);
      }
    },
    [width, onWidthChange],
  );

  return (
    <aside
      className="inspector paper"
      style={{ width: `${width}px` }}
      aria-label="Closure result"
    >
      {resizable && (
        <div
          className="inspector-grip"
          role="separator"
          tabIndex={0}
          aria-label="Resize the result panel"
          aria-orientation="vertical"
          aria-valuenow={width}
          aria-valuemin={INSPECTOR_MIN}
          aria-valuemax={INSPECTOR_MAX}
          onKeyDown={onGripKey}
          onPointerDown={(e) => {
            e.preventDefault();
            dragging.current = true;
            document.body.style.cursor = 'col-resize';
            document.body.style.userSelect = 'none';
          }}
        />
      )}

      {/*
        `tabIndex={0}` because this scrolls. Without a tab stop a keyboard-only
        user can reach the controls inside the panel but cannot scroll the panel
        itself, so anything between two focusable elements — the confidence
        notes, the comparison table — is unreachable. Flagged by the
        accessibility scan as `scrollable-region-focusable`.
      */}
      <div className="inspector-scroll" tabIndex={0}>
        {children}
      </div>

      {footer && <div className="inspector-foot">{footer}</div>}
    </aside>
  );
}

/**
 * Shown before any road is selected.
 *
 * The coverage note is written from what the backend reports, not from a
 * `clipped` boolean that a previous version turned into the words "Wellington
 * extract" regardless of where the extract actually was.
 */
export function InspectorEmpty({ coverage }: { coverage: Coverage }) {
  return (
    <div className="inspector-empty">
      <h2>Select a road to close</h2>
      <p>
        Click any road on the map, or search for one above. The road you select
        is closed — as a model, not on the ground — and the network is re-routed
        around it.
      </p>
      <p>
        The result is the shortest replacement path available in the represented
        network: a structural measure, not a traffic forecast.
      </p>
      <div className="hint">
        {coverage.isNational ? (
          <>
            <b>{coverage.name}.</b> Replacement paths are computed over the
            full national vehicle-road network, so a detour may leave the
            region entirely.
          </>
        ) : (
          <>
            <b>{coverage.name}.</b> This is a clipped extract. Roads outside it
            are absent from the graph, so a replacement path that would leave
            the extract cannot be found and a link may look more critical than
            it is.
          </>
        )}
      </div>
    </div>
  );
}
