/*
 * The outage span inspector.
 *
 * Reads as the closure inspector reads, because it answers the same question
 * about a different object: how much further you have to go because this
 * stretch is shut. Same hero treatment, same neutral colour for the
 * measurement - red is the closure, not the alarm - and the same refusal to
 * turn a structural result into a traffic claim.
 *
 * WHAT THIS PANEL WILL NOT SAY
 *
 * It never says a span is topology-robust, because nobody asked the question:
 * sensitivity is unavailable for a partial closure and the reason is shown
 * rather than the field omitted. It never reports isolation, because Gu has
 * one edge per link and cannot represent half of one. And it never presents a
 * withheld route as a detour, because a route that crosses a banned manoeuvre
 * across a split link is not one the engine offers.
 */

import type { DirectionMode, SpanCandidate } from '../api/outage.js';
import {
  closedLengthM,
  corridorChoices,
  handleAmbiguity,
  isUnusuallyLong,
  LONG_SPAN_M,
  type SpanState,
} from './spanState.js';

function km(metres: number | null): string {
  if (metres === null) return '—';
  return metres >= 1000
    ? `${(metres / 1000).toFixed(2)}`
    : `${Math.round(metres)}`;
}

function unitFor(metres: number | null): string {
  return metres !== null && metres >= 1000 ? 'km' : 'm';
}

const DIRECTIONS: { id: DirectionMode; label: string; hint: string }[] = [
  { id: 'both', label: 'Both ways', hint: 'The stretch is shut in both directions.' },
  { id: 'a_to_b', label: 'A → B', hint: 'Only the A-to-B direction is shut; the other keeps running.' },
  { id: 'b_to_a', label: 'B → A', hint: 'Only the B-to-A direction is shut; the other keeps running.' },
];

export default function SpanPanel({
  state,
  onDirection,
  onChooseCorridor,
  onClear,
}: {
  state: SpanState;
  onDirection: (d: DirectionMode) => void;
  onChooseCorridor: (id: string) => void;
  onClear: () => void;
}) {
  const { analysis, status } = state;
  const length = closedLengthM(state);
  const choices = corridorChoices(state);
  const ambiguousHandles = handleAmbiguity(state);

  if (status === 'empty') {
    return (
      <section className="span-panel" aria-label="Outage span">
        <h2 className="span-title">Two-point outage</h2>
        <p className="span-empty">
          Click once to place <strong>A</strong>, again to place{' '}
          <strong>B</strong>. The road between them is closed and the way round
          is measured.
        </p>
      </section>
    );
  }

  if (status === 'placing') {
    return (
      <section className="span-panel" aria-label="Outage span">
        <h2 className="span-title">Two-point outage</h2>
        {/* A click that reached no road has to say so here. Without this the
          * only feedback was the unchanged prompt, which reads as the click
          * having been ignored rather than as having missed - and at a low
          * zoom, where one pixel covers more ground than the snap radius,
          * missing is easy. */}
        {state.error && (
          <p className="span-note span-note--fault" role="alert">
            {state.error} Zoom in and click closer to the road.
          </p>
        )}
        <p className="span-empty">
          Now place the second handle to close the road between them.
        </p>
        <button type="button" className="span-clear" onClick={onClear}>
          Start again
        </button>
      </section>
    );
  }

  const primary = analysis?.measures.find((m) => m.direction === 'a_to_b')
    ?? analysis?.measures[0]
    ?? null;
  const busy = status === 'corridor-pending' || status === 'analysis-pending';

  return (
    <section className="span-panel" aria-label="Outage span" aria-busy={busy}>
      <h2 className="span-title">Two-point outage</h2>

      {/* --- what is closed --------------------------------------------- */}
      <div className="span-closed">
        <div className="lab">Road closed</div>
        <div className="val tnum">
          {km(length)}
          <span className="unit">{unitFor(length)}</span>
        </div>
        {state.corridor?.corridor && (
          <p className="sub">
            Along {state.corridor.corridor.roads}
            {state.previewStale && ' — moving'}
          </p>
        )}
      </div>

      {isUnusuallyLong(state) && (
        <p className="span-note span-note--warn" role="status">
          That is an unusually long closure — over{' '}
          {Math.round(LONG_SPAN_M / 1000)} km of road. It is measured exactly as
          drawn; nothing has been shortened.
        </p>
      )}

      {/* --- handle ambiguity ------------------------------------------- */}
      {ambiguousHandles.map((h) => (
        <p key={h.handle.stableKey} className="span-note" role="status">
          {h.ambiguityReason}
        </p>
      ))}

      {/* --- corridor choice -------------------------------------------- */}
      {choices.length > 0 && (
        <div className="span-choice">
          <p className="span-note" role="status">
            {state.corridor?.ambiguityReason}
          </p>
          <ul className="span-candidates">
            {choices.map((c: SpanCandidate) => (
              <li key={c.candidateId}>
                <button
                  type="button"
                  className="span-candidate"
                  aria-pressed={c.candidateId === state.corridorId}
                  onClick={() => onChooseCorridor(c.candidateId)}
                >
                  <span className="span-candidate-roads">{c.roads}</span>
                  <span className="span-candidate-length tnum">
                    {km(c.lengthM)} {unitFor(c.lengthM)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* --- direction --------------------------------------------------- */}
      <fieldset className="span-direction">
        <legend className="lab">Direction</legend>
        {DIRECTIONS.map((d) => (
          <label key={d.id} className="span-radio">
            <input
              type="radio"
              name="span-direction"
              value={d.id}
              checked={state.direction === d.id}
              onChange={() => onDirection(d.id)}
            />
            <span>{d.label}</span>
          </label>
        ))}
        <p className="sub">
          {DIRECTIONS.find((d) => d.id === state.direction)?.hint}
        </p>
      </fieldset>

      {/* --- the answer -------------------------------------------------- */}
      {status === 'error' && (
        <p className="span-note span-note--fault" role="alert">
          {state.error}
        </p>
      )}

      {busy && !analysis && (
        <div className="headline">
          <div className="lab">Added distance</div>
          <div className="val" aria-hidden="true">
            <span className="skeleton" style={{ height: '0.72em', minWidth: '2.6em' }} />
          </div>
          <p className="sub">Measuring the way round&hellip;</p>
        </div>
      )}

      {analysis && (
        <>
          <div className="headline">
            <div className="lab">
              {primary?.addedDistanceM != null ? 'Added distance' : 'Result'}
            </div>
            <div className="val reveal tnum" key={analysis.fingerprint}>
              {primary?.addedDistanceM != null ? (
                <>
                  {km(primary.addedDistanceM)}
                  <span className="unit">{unitFor(primary.addedDistanceM)}</span>
                </>
              ) : (
                <span className="span-headline-text">{analysis.headline}</span>
              )}
            </div>
            <p className="sub">
              {primary?.replacementDistanceM != null ? (
                <>
                  With this modelled closure, the shortest represented-network
                  path from A to B is {km(primary.replacementDistanceM)}{' '}
                  {unitFor(primary.replacementDistanceM)}, against{' '}
                  {km(length)} {unitFor(length)} along the road itself.
                </>
              ) : (
                <>{analysis.headline}. No replacement path is offered.</>
              )}
            </p>
          </div>

          {analysis.measures.length > 1 && (
            <table className="span-measures">
              <caption className="lab">Each direction</caption>
              <tbody>
                {analysis.measures.map((m) => (
                  <tr key={m.direction}>
                    <th scope="row">
                      {m.direction === 'a_to_b' ? 'A → B' : 'B → A'}
                    </th>
                    <td className="tnum">
                      {m.replacementDistanceM != null
                        ? `${km(m.replacementDistanceM)} ${unitFor(m.replacementDistanceM)}`
                        : m.status.replace(/_/g, ' ').toLowerCase()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <details className="span-method">
            <summary>How this was measured</summary>
            <p>{analysis.measurementCaveat}</p>
            <p>
              <strong>Isolation is not reported.</strong>{' '}
              {analysis.isolationUnavailableReason}
            </p>
            <p>
              <strong>Topology sensitivity is not reported.</strong>{' '}
              {analysis.sensitivityUnavailableReason}
            </p>
            <p className="span-provenance">
              {analysis.engine} {analysis.algorithmVersion} · snapshot{' '}
              {analysis.snapshotId} (processing {analysis.processingVersion}) ·{' '}
              {analysis.stability}
            </p>
          </details>
        </>
      )}

      <button type="button" className="span-clear" onClick={onClear}>
        Clear span
      </button>
    </section>
  );
}
