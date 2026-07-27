/*
 * Metric / Vehicle / Closure, as bordered form cells.
 *
 * Revealed by the sticky ScenarioSummary rather than always occupying the first
 * viewport, so at 1280x800 the result stays readable and repeated scenario
 * experimentation does not require scrolling past the answer each time.
 *
 * An option the backend cannot honour is rendered disabled with the reason
 * attached, not omitted. A control that quietly lacks the option the user is
 * looking for is worse than one that says "not yet, and here is why" — and
 * `segment` scope is exactly that case today.
 */

import {
  closureScopes,
  metrics,
  vehicles,
  type ClosureScope,
  type Metric,
  type OptionDescriptor,
  type Scenario,
  type Vehicle,
} from '../api/scenario.js';
import type { NetworkMetadata } from '../api/types.js';

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
            title={o.supported ? o.hint : o.unavailableReason}
            onClick={() => onChange(o.value)}
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}

export default function ScenarioControls({
  id,
  scenario,
  onChange,
  meta,
}: {
  id: string;
  scenario: Scenario;
  onChange: (next: Scenario) => void;
  meta: NetworkMetadata | null;
}) {
  const scopes = closureScopes(meta);
  const activeScope = scopes.find((s) => s.value === scenario.closureScope);

  return (
    <div className="ctls" id={id}>
      <Row<ClosureScope>
        label="Closure"
        options={scopes}
        value={scenario.closureScope}
        onChange={(closureScope) => onChange({ ...scenario, closureScope })}
      />
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
