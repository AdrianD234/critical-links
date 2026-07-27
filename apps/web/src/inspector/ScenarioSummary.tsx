/*
 * The compact sticky scenario summary — "Car · Distance · AMDS feature".
 *
 * WHY IT EXISTS
 *
 * At 1280×800 the full Metric / Vehicle / Closure controls do not fit above the
 * fold alongside a readable result. The wrong fix is to compress the result to
 * make room, because the result is the reason the panel exists. Instead the
 * current scenario is stated in one line near the top, and selecting it reveals
 * the complete controls in place.
 *
 * That way repeated scenario experimentation — the thing an analyst actually
 * does over and over — never requires scrolling past the result to reach the
 * controls and then scrolling back to read the answer.
 *
 * It is a disclosure button, not a tab or a menu: `aria-expanded` plus
 * `aria-controls` pointing at the panel it opens.
 */

import { ChevronIcon } from '../shell/icons.js';

export default function ScenarioSummary({
  summary,
  open,
  onToggle,
  controlsId,
  dirty,
}: {
  summary: string;
  open: boolean;
  onToggle: () => void;
  controlsId: string;
  /** True while the displayed result does not yet reflect these settings. */
  dirty: boolean;
}) {
  return (
    <div className="scenario-summary">
      <button
        type="button"
        className="scenario-summary-btn"
        aria-expanded={open}
        aria-controls={controlsId}
        onClick={onToggle}
      >
        <span className="k">Scenario</span>
        <span className="v">{summary}</span>
        {dirty && (
          <span className="recalc" title="Recalculating for these settings">
            recalculating
          </span>
        )}
        <span className={`chev${open ? ' open' : ''}`} aria-hidden="true">
          <ChevronIcon size={12} />
        </span>
      </button>
    </div>
  );
}
