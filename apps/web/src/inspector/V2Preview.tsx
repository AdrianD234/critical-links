/*
 * The V2 closure-analysis preview.
 *
 * Deliberately NOT a second inspector. It reports what V2 returns — the
 * headline, the closure, the isolation and the endpoint-route figures — in the
 * plainest form that is still honest, and leaves the hero, the direction tabs,
 * the corridor and the route breakdown to the V1 view. A preview that looked
 * like the product would invite a reader to treat a `-dev` algorithm version as
 * a published figure.
 *
 * The endpoint-route figures are last and collapsed. They are computed, so
 * hiding them made the engine look as though it had no routing answer, but
 * their endpoints are the closed segment's own, which is not where a real
 * replacement route starts and ends. The section says so in its own words
 * rather than relying on its position to imply it.
 *
 * The stability string comes from the response and is shown verbatim. It is the
 * engine's own statement about how settled it is, and paraphrasing it here
 * would put this file in the business of deciding how much to trust the
 * backend.
 */

import ScenarioControls from './ScenarioControls.js';
import LinkHeader from './LinkHeader.js';
import { count, distance, duration, inlineMetres, ratio } from '../lib/format.js';
import { closureLabel, closureScopeFromWireV2, type Scenario } from '../api/scenario.js';
import {
  MUTUAL_REACHABILITY_UNKNOWN_REASON,
  mutualReachability,
} from '../v2Wording.js';
import TopologySensitivity from './TopologySensitivity.js';
import V2BoundaryFindings from './V2BoundaryFindings.js';
import type {
  NetworkMetadata,
  V2BoundaryAnalysis,
  V2Capabilities,
  V2ClosureAnalysis,
  V2DirectionResult,
  V2TopologySensitivity,
} from '../api/types.js';

export default function V2Preview({
  analysis,
  boundary,
  boundaryLoading,
  boundaryError,
  onBoundaryRetry,
  sensitivity,
  sensitivityLoading,
  sensitivityError,
  sensitivityToken,
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
  /** The boundary-movement measure. A third question, not a better answer. */
  boundary: V2BoundaryAnalysis | null;
  boundaryLoading: boolean;
  /* Topology sensitivity arrives on its OWN request, after the canonical
   * answer is already on screen. `sensitivityToken` is the selection this
   * panel is showing; a response carrying any other token is discarded. */
  sensitivity?: V2TopologySensitivity;
  sensitivityLoading?: boolean;
  sensitivityError?: Error | null;
  sensitivityToken?: string;
  boundaryError: Error | null;
  onBoundaryRetry: () => void;
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

      {/* The boundary measure comes AFTER the endpoint one, deliberately.
        * The endpoint block above is what PR 1 shipped and what the shadow
        * comparison is built on; this is the new question. Leading with it
        * would imply the block above had been superseded, and it has not -
        * they measure different things and both are reported. */}
      <V2BoundaryFindings
        analysis={boundary}
        loading={boundaryLoading}
        error={boundaryError}
        onRetry={onBoundaryRetry}
      />

      {/* AFTER the boundary result, and only once it exists. Sensitivity is a
        * qualification of the canonical answer, so the canonical answer has to
        * be on screen first - and it is a second request precisely so the user
        * is not made to wait for it. */}
      {sensitivityToken ? (
        <TopologySensitivity
          data={sensitivity}
          loading={Boolean(sensitivityLoading)}
          error={sensitivityError ?? null}
          token={sensitivityToken}
        />
      ) : null}

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
            {/*
              A low topology confidence goes above the figures, not below
              them. It says the connectivity the figures describe may be an
              artefact of how the network was assembled, and a reader who
              meets that after the numbers has already believed them.
            */}
            {isolation.topologyConfidence === 'low' && (
              <div className="notice notice--warn" role="status">
                <div className="notice-title">Topology confidence low</div>
                <p>{isolation.topologyConfidenceReason}</p>
                <p>
                  Unresolved near-miss endpoints sit close to this closure.
                  Whether the two sides join at all may follow from the
                  tolerance used when the network was ingested rather than from
                  the roads, so this result can change without any road
                  changing.
                </p>
              </div>
            )}

            <dl className="kv">
              <dt>Finding</dt>
              <dd>{isolationStatement}</dd>
              <dt>Method</dt>
              <dd>{isolation.method}</dd>
              {/*
                Three claims, kept apart. A single "exact" merged them, and
                the only reading it invited was the strongest one: that the
                answer holds of the road network. It does not. The partition
                is exact; the graph it partitions is inferred.
              */}
              <dt>Calculation</dt>
              <dd>
                {isolation.calculationExact
                  ? 'exact — no search bound was involved'
                  : 'not exact'}
              </dd>
              <dt>Partition</dt>
              <dd>{isolation.partitionExact ? 'exact' : 'not exact'}</dd>
              <dt>Graph</dt>
              <dd>
                {isolation.graphExact
                  ? 'models the road network'
                  : 'inferred topology — not an exact model of the road network'}
              </dd>
              <dt>Topology confidence</dt>
              <dd>{isolation.topologyConfidence}</dd>
              <dt>Closure is a bridge</dt>
              <dd>{isolation.closureIsBridge ? 'yes' : 'no'}</dd>
              <dt>Separated</dt>
              <dd>
                {count(isolation.separatedLinkCount)} links
                {separated ? `, ${separated.value} ${separated.unit}` : ''}
                {/* The counts stay exact when the id list is capped, so the
                  * two have to be distinguished rather than both doubted. */}
                {isolation.separatedTruncated
                  ? ' (id list capped; the counts are exact)'
                  : ''}
              </dd>
              <dt>Components</dt>
              <dd>
                {count(isolation.componentCount)}
                {isolation.componentsTruncated ? ' (list truncated)' : ''}
              </dd>
              <dt>Principal side</dt>
              <dd>
                {isolation.principalSideRule} ({isolation.principalSideConfidence}{' '}
                confidence)
              </dd>
            </dl>

            {isolation.topologyConfidence !== 'low' &&
              isolation.topologyConfidenceReason && (
                <p className="note">{isolation.topologyConfidenceReason}</p>
              )}

            {/*
              Which side is "cut off" is a policy, not a theorem. Where the
              policy cannot decide, saying so is the finding — naming a side
              anyway would assert a direction the data does not carry.
            */}
            {isolation.principalSideAmbiguous && (
              <p className="note">
                No side was named as cut off. Neither side carries a decisive
                anchor, so what is supported is that the network splits into two
                represented components, not that one of them lost its
                connection.
              </p>
            )}

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
              <dt>Endpoints after closure</dt>
              {/* Three states, not two. See ../v2Wording.ts for why null may
                * not be rendered as a negative answer. */}
              <dd>
                {mutualReachability(
                  analysis.directedAccess.same_scc_after_closure,
                )}
              </dd>
              <dt>Asymmetric</dt>
              <dd>{analysis.directedAccess.asymmetric ? 'yes' : 'no'}</dd>
            </dl>
            <p className="note">{analysis.directedAccess.detail}</p>
            {analysis.directedAccess.same_scc_after_closure === null && (
              <p className="note">{MUTUAL_REACHABILITY_UNKNOWN_REASON}</p>
            )}
          </div>
        </details>

        <EndpointRoutes analysis={analysis} />

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

/** A formatted magnitude, or the em dash the rest of the panel uses. */
function amount(v: { value: string; unit: string } | null): string {
  return v ? `${v.value} ${v.unit}` : '—';
}

/**
 * The routing figures V2 already computes, said out loud.
 *
 * The panel was proving the closure and isolation fix and holding these back,
 * which left the engine looking as though it had no routing answer at all. It
 * has one; what it does not yet have is the method the answer will finally be
 * computed with.
 *
 * Collapsed by default, and it stays collapsed. The section above it reports
 * an exact partition of a graph, and these numbers are a first pass whose
 * endpoints are known to be the wrong ones for a real replacement route.
 * Opening them alongside would present the two as equally settled.
 */
function EndpointRoutes({ analysis }: { analysis: V2ClosureAnalysis }) {
  const directions = [analysis.forward, analysis.reverse].filter(
    (d): d is V2DirectionResult => d !== null,
  );

  return (
    <details className="disclose">
      <summary>Provisional endpoint-route result</summary>
      <div className="body">
        <p className="note">
          These are endpoint-to-endpoint measures. Each one compares the route
          between the closed segment&rsquo;s own two endpoints with the segment
          in place against the same route with it removed. That is not a
          corridor result: where a replacement route in practice leaves and
          rejoins the network well beyond the closure, it is being measured
          from the wrong two points.
        </p>
        <p className="note">
          Boundary ports and deterministic corridor selection land in PR 2, and
          the method is expected to change when they do. Every figure below is
          provisional and none of them is a published figure.
        </p>

        {directions.length === 0 ? (
          <p className="note">No direction was computed for this closure.</p>
        ) : (
          directions.map((d) => (
            <DirectionFigures
              key={d.direction}
              d={d}
              metric={analysis.request.metric}
            />
          ))
        )}
      </div>
    </details>
  );
}

function DirectionFigures({
  d,
  metric,
}: {
  d: V2DirectionResult;
  metric: string;
}) {
  const detour = ratio(d.detourRatioVsSegment);

  return (
    <dl className="kv" style={{ marginTop: 12 }}>
      <dt>Direction</dt>
      <dd>{d.direction}</dd>
      <dt>Status</dt>
      <dd>{d.status}</dd>
      <dt>Finding</dt>
      <dd>{d.headline}</dd>
      <dt>Replacement path</dt>
      <dd>{amount(distance(d.alternativeDistanceM))}</dd>
      <dt>Path with the segment in place</dt>
      <dd>{amount(distance(d.normalPathDistanceM))}</dd>
      <dt>Added against the closed segment</dt>
      <dd>{amount(distance(d.addedVsSegmentM))}</dd>
      {/* Against the open network rather than against the segment: the two
        * differ whenever the segment was not itself the shortest path, and
        * quoting one for the other overstates or understates the cost. */}
      <dt>Network penalty</dt>
      <dd>{amount(distance(d.networkPenaltyM))}</dd>
      <dt>Detour ratio against the segment</dt>
      <dd>{detour ? `${detour}×` : '—'}</dd>
      {/* Times exist only when the request asked for them, and showing an
        * empty row for the metric that was not run reads as a missing
        * measurement rather than one that was never asked for. */}
      {metric === 'time' && (
        <>
          <dt>Time with the segment in place</dt>
          <dd>{amount(duration(d.normalPathTimeS))}</dd>
          <dt>Replacement time</dt>
          <dd>{amount(duration(d.alternativeTimeS))}</dd>
          <dt>Added time</dt>
          <dd>{amount(duration(d.addedTimeS))}</dd>
        </>
      )}
      {d.errorDetail && (
        <>
          <dt>Detail</dt>
          <dd>{d.errorDetail}</dd>
        </>
      )}
    </dl>
  );
}
