/*
 * The V2 closure-analysis preview.
 *
 * Deliberately NOT a second inspector. It reports what V2 returns — the
 * headline, the closure and the isolation — in the plainest form that is still
 * honest, and leaves the hero, the direction tabs, the corridor and the route
 * breakdown to the V1 view. A preview that looked like the product would invite
 * a reader to treat a `-dev` algorithm version as a published figure.
 *
 * The stability string comes from the response and is shown verbatim. It is the
 * engine's own statement about how settled it is, and paraphrasing it here
 * would put this file in the business of deciding how much to trust the
 * backend.
 */

import ScenarioControls from './ScenarioControls.js';
import LinkHeader from './LinkHeader.js';
import { count, distance, inlineMetres } from '../lib/format.js';
import { closureLabel, closureScopeFromWireV2, type Scenario } from '../api/scenario.js';
import type {
  NetworkMetadata,
  V2Capabilities,
  V2ClosureAnalysis,
} from '../api/types.js';

export default function V2Preview({
  analysis,
  capabilities,
  meta,
  pendingName,
  loading,
  error,
  scenario,
  onScenarioChange,
  onClear,
  onRetry,
}: {
  analysis: V2ClosureAnalysis | null;
  capabilities: V2Capabilities | null;
  meta: NetworkMetadata | null;
  pendingName: string | null;
  loading: boolean;
  error: Error | null;
  scenario: Scenario;
  onScenarioChange: (s: Scenario) => void;
  onClear: () => void;
  onRetry: () => void;
}) {
  const stability =
    analysis?.stability ?? capabilities?.stability ?? 'development preview';

  return (
    <>
      <div className="notice notice--info" role="status">
        <div className="notice-title">V2 closure analysis — {stability}</div>
        <p>
          These figures come from the second closure engine, not from the one
          the rest of the application uses. The two answer different questions
          and are kept side by side so each can check the other. Nothing here
          is a published figure.
        </p>
      </div>

      <LinkHeader
        link={analysis?.selectedLink ?? null}
        pendingName={pendingName}
        onClear={onClear}
      />

      {error ? (
        <div className="inspector-empty">
          <h2>Analysis failed</h2>
          <p>{error.message}</p>
          <button type="button" className="pbtn" onClick={onRetry}>
            Try again
          </button>
        </div>
      ) : loading || !analysis ? (
        <div className="status-row">
          <span className="status-pill" data-kind="ok">
            Calculating&hellip;
          </span>
        </div>
      ) : (
        <Findings analysis={analysis} />
      )}

      {/* The scope control belongs with the V2 figures: segment scope is the
        * whole reason this engine exists and it cannot be reached from the V1
        * controls, which the V1 backend disables it in. */}
      <ScenarioControls
        id="v2-scenario"
        scenario={scenario}
        onChange={onScenarioChange}
        meta={meta}
        v2Capabilities={capabilities}
      />
    </>
  );
}

function Findings({ analysis }: { analysis: V2ClosureAnalysis }) {
  const { closure, isolation, headline, isolationStatement } = analysis;
  const scope = closureScopeFromWireV2(closure.scope);
  const separated = distance(isolation.separatedLengthM);
  const total = distance(closure.totalClosureLengthM);
  const selected = distance(closure.selectedSegmentLengthM);

  return (
    <>
      <div className="headline">
        <div className="lab">{headline}</div>
        <p className="sub" style={{ marginTop: 8 }}>
          {isolation.detail}
        </p>
      </div>

      {/*
        The warning is the whole point of surfacing source-feature scope at
        all: the figures below describe a closure larger than the one the
        reader selected, and they have to be told that before they read them,
        not after.
      */}
      {closure.warning && (
        <div className="notice notice--warn" role="status">
          <div className="notice-title">{closure.warning.headline}</div>
          <p>{closure.warning.detail}</p>
        </div>
      )}

      <div className="disclosures">
        <details className="disclose" open>
          <summary>Closure</summary>
          <div className="body">
            <dl className="kv">
              <dt>Scope</dt>
              <dd>{closureLabel(scope)}</dd>
              <dt>Selected segment</dt>
              <dd>{selected ? `${selected.value} ${selected.unit}` : '—'}</dd>
              <dt>Total removed</dt>
              <dd>{total ? `${total.value} ${total.unit}` : '—'}</dd>
              {closure.excessLengthM > 0 && (
                <>
                  <dt>Beyond the selection</dt>
                  <dd>{inlineMetres(closure.excessLengthM)}</dd>
                </>
              )}
              <dt>Segments removed</dt>
              <dd>
                {count(closure.removedLinkCount)} links,{' '}
                {count(closure.removedArcCount)} directed arcs
              </dd>
              <dt>Shape</dt>
              <dd>{closure.shapeDetail || closure.shape}</dd>
            </dl>
          </div>
        </details>

        <details className="disclose" open>
          <summary>Physical isolation</summary>
          <div className="body">
            <dl className="kv">
              <dt>Finding</dt>
              <dd>{isolationStatement}</dd>
              <dt>Method</dt>
              <dd>
                {isolation.method}
                {/* An exact answer and an estimated one must never read the
                  * same. V1's isolation claimed exactness it did not have. */}
                {isolation.exact ? ' (exact)' : ' (not exact)'}
              </dd>
              <dt>Closure is a bridge</dt>
              <dd>{isolation.closureIsBridge ? 'yes' : 'no'}</dd>
              <dt>Separated</dt>
              <dd>
                {count(isolation.separatedLinkCount)} links
                {separated ? `, ${separated.value} ${separated.unit}` : ''}
              </dd>
              <dt>Components</dt>
              <dd>
                {count(isolation.componentCount)}
                {isolation.componentsTruncated ? ' (list truncated)' : ''}
              </dd>
            </dl>

            {isolation.components
              .filter((c) => !c.retainsPrincipalConnection)
              .slice(0, 5)
              .map((c, i) => (
                <div className="b-row" key={`${c.linkCount}-${i}`}>
                  <span>
                    Separated part {i + 1} — {count(c.linkCount)} links
                    {c.stateHighwayLinkCount > 0
                      ? `, ${count(c.stateHighwayLinkCount)} state highway`
                      : ''}
                  </span>
                  <span className="n">{inlineMetres(c.roadLengthM)}</span>
                </div>
              ))}
          </div>
        </details>

        <details className="disclose">
          <summary>Directed access</summary>
          <div className="body">
            <dl className="kv">
              <dt>Forward</dt>
              <dd>{analysis.directedAccess.forward_status}</dd>
              <dt>Reverse</dt>
              <dd>{analysis.directedAccess.reverse_status}</dd>
              <dt>Asymmetric</dt>
              <dd>{analysis.directedAccess.asymmetric ? 'yes' : 'no'}</dd>
            </dl>
            <p className="note">{analysis.directedAccess.detail}</p>
          </div>
        </details>

        <details className="disclose">
          <summary>Engine</summary>
          <div className="body">
            <dl className="kv">
              <dt>Algorithm</dt>
              <dd>
                {analysis.algorithm} {analysis.algorithmVersion}
              </dd>
              <dt>Derivation</dt>
              <dd>{analysis.derivationVersion}</dd>
              <dt>Snapshot</dt>
              <dd>{analysis.snapshotId}</dd>
              <dt>Comparable to V1</dt>
              <dd>
                {analysis.comparableToV1
                  ? 'yes — same closure scope'
                  : 'no — V1 cannot close this scope'}
              </dd>
            </dl>
          </div>
        </details>
      </div>
    </>
  );
}
