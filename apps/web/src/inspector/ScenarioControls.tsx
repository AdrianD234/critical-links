/*
 * Metric / Vehicle / Closure, as bordered form cells.
 *
 * Revealed by the sticky ScenarioSummary rather than always occupying the first
 * viewport, so at 1280x800 the result stays readable and repeated scenario
 * experimentation does not require scrolling past the answer each time.
 *
 * An option the active engine cannot honour is rendered disabled with the
 * reason attached, not omitted. A control that quietly lacks the option the
 * user is looking for is worse than one that says "not here, and here is why"
 * — and `segment` scope is exactly that case under V1.
 *
 * An option marked advanced stays selectable but says so, because it is
 * correct and rarely what was meant: AMDS source-feature scope closes more
 * road than the reader pointed at.
 */

import {
  closureScopes,
  metrics,
  vehicles,
  type ClosureScope,
  type DirectionKey,
  type Metric,
  type OptionDescriptor,
  type Scenario,
  type Vehicle,
} from '../api/scenario.js';
import type { NetworkMetadata, V2Capabilities } from '../api/types.js';

function Row<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: OptionDescriptor<T>[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div className="ctl">
      <span className="k" id={`ctl-${label.toLowerCase()}`}>
        {label}
      </span>
      <div className="seg" role="group" aria-labelledby={`ctl-${label.toLowerCase()}`}>
        {options.map((o) => (
          <button
            key={o.value}
            type="button"
            aria-pressed={value === o.value}
            disabled={!o.supported}
            title={
              o.supported
                ? o.advanced
                  ? `Advanced. ${o.hint ?? ''}`.trim()
                  : o.hint
                : o.unavailableReason
            }
            onClick={() => onChange(o.value)}
          >
            {o.label}
            {o.advanced && <span className="opt-advanced"> · advanced</span>}
          </button>
        ))}
      </div>
    </div>
  );
}

/**
 * Which traversal `direction` scope withdraws.
 *
 * Only under that scope. A directed closure removes ONE direction of travel and
 * has to name which; under the other scopes the engine ignores it, and offering
 * the control anyway would imply a choice that changes nothing.
 *
 * This exists because the direction tabs went away with the measure that needed
 * them. Those tabs switched between two already-computed halves of an endpoint
 * result; this picks which closure to compute, which is a different control
 * with the same words on it. Without it, selecting `direction` scope leaves the
 * reader stuck on whichever traversal the URL happened to carry.
 */
const DIRECTIONS: OptionDescriptor<DirectionKey>[] = [
  {
    value: 'forward',
    label: 'Forward',
    hint: 'Withdraw travel in the link’s digitised direction',
    supported: true,
  },
  {
    value: 'reverse',
    label: 'Reverse',
    hint: 'Withdraw travel against the link’s digitised direction',
    supported: true,
  },
];

export default function ScenarioControls({
  id,
  scenario,
  onChange,
  meta,
  v2Capabilities = null,
  direction,
  onDirectionChange,
}: {
  id: string;
  scenario: Scenario;
  onChange: (next: Scenario) => void;
  meta: NetworkMetadata | null;
  /** What the closure engine reports it can do for this snapshot. */
  v2Capabilities?: V2Capabilities | null;
  /** Which traversal is withdrawn. Only meaningful under `direction` scope. */
  direction?: DirectionKey;
  onDirectionChange?: (d: DirectionKey) => void;
}) {
  const scopes = closureScopes(meta, v2Capabilities);
  const activeScope = scopes.find((s) => s.value === scenario.closureScope);

  return (
    <div className="ctls" id={id}>
      <Row<ClosureScope>
        label="Closure"
        options={scopes}
        value={scenario.closureScope}
        onChange={(closureScope) => onChange({ ...scenario, closureScope })}
      />

      {scenario.closureScope === 'direction' &&
        direction !== undefined &&
        onDirectionChange !== undefined && (
          <Row<DirectionKey>
            label="Direction"
            options={DIRECTIONS}
            value={direction}
            onChange={onDirectionChange}
          />
        )}
      <Row<Metric>
        label="Measure"
        options={metrics()}
        value={scenario.metric}
        onChange={(metric) => onChange({ ...scenario, metric })}
      />
      <Row<Vehicle>
        label="Vehicle"
        options={vehicles()}
        value={scenario.vehicle}
        onChange={(vehicle) => onChange({ ...scenario, vehicle })}
      />

      {activeScope?.hint && (
        <p
          style={{
            fontSize: 'var(--text-meta)',
            color: 'var(--panel-muted)',
            lineHeight: 'var(--leading-relaxed)',
          }}
        >
          {activeScope.hint}.
        </p>
      )}
    </div>
  );
}
