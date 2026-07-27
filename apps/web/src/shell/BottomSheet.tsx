/*
 * The mobile inspector: a draggable bottom sheet over the map.
 *
 * On a phone the map is still the main surface, so the inspector cannot take a
 * fixed column. Three snap stops, each chosen around what is legible at that
 * height rather than round percentages:
 *
 *   collapsed  road name, status and the hero result — the answer, nothing else
 *   medium     plus the four measures and the direction tabs
 *   expanded   plus scenario controls, comparison and methodology
 *
 * The map is told the sheet's height so route framing stays in the *visible*
 * map, exactly as the desktop inspector's width is used for right padding.
 *
 * Dragging is pointer-events based so it works with touch, mouse and pen from
 * one code path. The handle is also a real button: tapping it advances to the
 * next stop, which is the whole interaction for anyone who cannot drag.
 */

import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';

export type SheetStop = 'collapsed' | 'medium' | 'expanded';

const FRACTIONS: Record<SheetStop, number> = {
  collapsed: 0.26,
  medium: 0.55,
  expanded: 0.9,
};

const ORDER: SheetStop[] = ['collapsed', 'medium', 'expanded'];

export function sheetHeight(stop: SheetStop, viewportH: number): number {
  return Math.round(FRACTIONS[stop] * viewportH);
}

export default function BottomSheet({
  stop,
  onStopChange,
  onHeightChange,
  children,
  footer,
  label,
}: {
  stop: SheetStop;
  onStopChange: (s: SheetStop) => void;
  onHeightChange: (px: number) => void;
  children: ReactNode;
  footer?: ReactNode;
  label: string;
}) {
  const [dragPx, setDragPx] = useState<number | null>(null);
  const dragging = useRef(false);
  const viewportH = useRef(window.innerHeight);

  const settled = sheetHeight(stop, viewportH.current);
  const height = dragPx ?? settled;

  useEffect(() => {
    onHeightChange(height);
  }, [height, onHeightChange]);

  useEffect(() => {
    const onResize = () => {
      viewportH.current = window.innerHeight;
      onHeightChange(sheetHeight(stop, viewportH.current));
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [stop, onHeightChange]);

  /** Release: snap to whichever stop the sheet ended up nearest. */
  const settle = useCallback(
    (px: number) => {
      let best: SheetStop = 'collapsed';
      let bestD = Infinity;
      for (const s of ORDER) {
        const d = Math.abs(sheetHeight(s, viewportH.current) - px);
        if (d < bestD) {
          bestD = d;
          best = s;
        }
      }
      onStopChange(best);
      setDragPx(null);
    },
    [onStopChange],
  );

  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      if (!dragging.current) return;
      e.preventDefault();
      const px = Math.max(
        90,
        Math.min(viewportH.current * 0.94, viewportH.current - e.clientY),
      );
      setDragPx(px);
    };
    const onUp = () => {
      if (!dragging.current) return;
      dragging.current = false;
      setDragPx((px) => {
        if (px !== null) settle(px);
        return px;
      });
    };
    window.addEventListener('pointermove', onMove, { passive: false });
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onUp);
    return () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onUp);
    };
  }, [settle]);

  function cycle() {
    const i = ORDER.indexOf(stop);
    onStopChange(ORDER[(i + 1) % ORDER.length]!);
  }

  return (
    <aside
      className="sheet paper"
      style={{
        height: `${height}px`,
        transition: dragPx === null ? undefined : 'none',
      }}
      aria-label={label}
    >
      <button
        type="button"
        className="sheet-handle"
        aria-label={`Result panel, ${stop}. Activate to expand, or drag to resize.`}
        aria-expanded={stop !== 'collapsed'}
        onClick={cycle}
        onPointerDown={(e) => {
          dragging.current = true;
          (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
        }}
      >
        <span className="grip" aria-hidden="true" />
      </button>

      {/* Focusable so it can be scrolled from the keyboard — see the note in
        * ContextInspector. */}
      <div className="sheet-scroll" tabIndex={0}>
        {children}
      </div>

      {footer && <div className="inspector-foot">{footer}</div>}
    </aside>
  );
}
