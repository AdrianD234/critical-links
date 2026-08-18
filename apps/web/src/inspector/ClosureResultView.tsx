/*
 * The inspector. The product's answer to "what happens if this closes".
 *
 * It reports the BOUNDARY-MOVEMENT measure: which trips genuinely crossed the
 * closure, and what each has to do instead. That is the question a reader is
 * asking when they click a road, and it is not the question the retired engine
 * answered — that one measured between the closed feature's own two endpoints,
 * which on a one-way carriageway is undefined and on a long feature is measured
 * from the wrong two places.
 *
 * WHAT THIS FILE MAY NOT DO
 *
 * Promotion changed which engine answers. It did not change what the engine
 * knows, and every hedge below is load-bearing:
 *
 *   - routes are through the REPRESENTED network, not the road network;
 *   - the topology is INFERRED, and its confidence is reported, not filtered;
 *   - times are ESTIMATED, because AMDS publishes no speed attribute;
 *   - turn restrictions are POST-VALIDATED, so a route that survives the check
 *     is not thereby road-legal;
 *   - a bounded search that did not evaluate every candidate says so, and no
 *     definitive headline is drawn from it.
 *
 * A reader who takes a figure from here and drops the caveats is doing
 * something this file cannot prevent. A reader who never met the caveats is
 * this file's fault.
 */

import { useId } from 'react';

import LinkAttributes from './LinkAttributes.js';
import LinkHeader from './LinkHeader.js';
import QualityFlags from './QualityFlags.js';
import ScenarioControls from './ScenarioControls.js';
import ScenarioSummary from './ScenarioSummary.js';
import SourceMethodology from './SourceMethodology.js';
import HeroMetric, { HeroSkeleton } from './HeroMetric.js';
import { RouteGeometryGap, SnapshotMismatch } from './ResultNotices.js';
import { count, distance, duration, inlineMetres, ratio, signedKm } from '../lib/format.js';
import {
  closureLabel,
  scopeOfResponse,
  summariseScenario,
  type ClosureScope,
  type DirectionKey,
  type Scenario,
} from '../api/scenario.js';
import type { LegacyMigration } from '../state/url.js';
import type { GeometryWarning } from '../map/NetworkMap.js';
import type {
  NetworkMetadata,
  V2BoundaryAnalysis,
  V2Capabilities,
  V2Corridor,
  V2CorridorPort,
  V2Isolation,
  V2ReplacementPath,
  V2RouteGeometry,
} from '../api/types.js';

/** A formatted magnitude, or the em dash the panel uses for one that is absent. */
function km(m: number | null | undefined): string {
  const d = distance(m);
  return d === null ? '—' : `${d.value} ${d.unit}`;
}

export interface ClosureResultViewProps {
  analysis: V2BoundaryAnalysis | null;
  capabilities: V2Capabilities | null;
  meta: NetworkMetadata | null;
  /** The road name is known from the click before the result arrives. */
  pendingName: string | null;
  loading: boolean;
  /** Set while the result on screen predates the current scenario. */
  stale: boolean;
  error: Error | null;
  onRetry: () => void;
  scenario: Scenario;
  onScenarioChange: (s: Scenario) => void;
  scenarioOpen: boolean;
  onScenarioToggle: () => void;
  /** Which traversal `direction` scope withdraws. Ignored under other scopes. */
  direction: DirectionKey;
  onDirectionChange: (d: DirectionKey) => void;
  onClear: () => void;
  /** Set when the permalink names a snapshot other than the active one. */
  snapshotMismatch: { requested: string; active: string } | null;
  /** Set when the map could not draw the route as a continuous path. */
  geometryWarning: GeometryWarning | null;
  /** Set once, when a pre-promotion link was migrated. */
  migration: LegacyMigration | null;
  onDismissMigration: () => void;
  onRestoreScope: (scope: ClosureScope) => void;
}

export default function ClosureResultView({
  analysis,
  capabilities,
  meta,
  pendingName,
  loading,
  stale,
  error,
  onRetry,
  scenario,
  onScenarioChange,
  scenarioOpen,
  onScenarioToggle,
  direction,
  onDirectionChange,
  onClear,
  snapshotMismatch,
  geometryWarning,
  migration,
  onDismissMigration,
  onRestoreScope,
}: ClosureResultViewProps) {
  const controlsId = useId();

  const summary = summariseScenario(scenario, meta, capabilities);
  const showSkeleton = loading || !analysis;

  /* The reveal replays whenever the underlying figures change, and only then. */
  const revealKey = analysis
    ? `${analysis.snapshotId}:${analysis.linkId}:${scenario.metric}:` +
      `${scenario.vehicle}:${scenario.closureScope}`
    : 'pending';

  /*
   * A wrapper, not a fragment.
   *
   * Several blocks here — the measures list, the movement context, the
   * isolation heading — sit at the top level of the panel rather than inside a
   * disclosure, which is the only place the stylesheet had ever put them. With
   * no container to hang a rule on they inherited no horizontal padding and
   * ran out past the panel's edge, over the map.
   *
   * A plain div with no overflow or transform, so it does not become a
   * containing block and the sticky scenario summary still resolves against
   * the scroll container above it.
   */
  return (
    <div className="closure-panel">
      <ScenarioSummary
        summary={summary}
        open={scenarioOpen}
        onToggle={onScenarioToggle}
        controlsId={controlsId}
        dirty={stale}
      />

      <LinkHeader
        link={analysis?.selectedLink ?? null}
        pendingName={pendingName}
        onClear={onClear}
      />

      {migration && (
        <LegacyLinkNotice
          migration={migration}
          onDismiss={onDismissMigration}
          onRestoreScope={onRestoreScope}
        />
      )}

      {snapshotMismatch && (
        <SnapshotMismatch
          requested={snapshotMismatch.requested}
          active={snapshotMismatch.active}
        />
      )}

      {/* aria-live so a screen-reader user is told the outcome without having
       * to go looking for it. `polite` because it must not interrupt. */}
      <div aria-live="polite" aria-atomic="true">
        {error ? (
          <div className="status-row">
            <span className="status-pill" data-kind="fault">
              Analysis unresolved
            </span>
          </div>
        ) : showSkeleton ? (
          <div className="status-row">
            <span className="status-pill" data-kind="ok">
              Calculating&hellip;
            </span>
          </div>
        ) : (
          <div className="status-row">
            <span className="status-pill" data-kind={pillKind(analysis)}>
              {analysis.headline}
            </span>
          </div>
        )}
      </div>

      {error ? (
        /*
         * A failure is reported as a failure, and no figure is offered beside
         * it. There is deliberately no second engine to fall back to: the
         * retired one answers under different closure and movement semantics,
         * so a number from it here would be an answer to a question nobody
         * asked, printed where this question's answer belongs.
         */
        <div className="headline">
          <div className="lab">Analysis unresolved</div>
          <p className="sub" style={{ marginTop: 8 }}>
            {error.message}
          </p>
          <button
            type="button"
            className="pbtn"
            style={{ marginTop: 12 }}
            onClick={onRetry}
          >
            Try again
          </button>
        </div>
      ) : showSkeleton ? (
        <HeroSkeleton />
      ) : (
        <>
          {/*
            BEFORE the result, both of these, and deliberately.

            What was removed, and how sure the graph is, are the two things
            that decide what the figures below mean. A reader who meets either
            after the number has already read the number as the answer, and
            neither is recoverable by re-reading.
          */}
          <ClosureCost analysis={analysis} />
          <TopologyConfidence isolation={analysis.isolation} />
          <Hero analysis={analysis} revealKey={revealKey} />
        </>
      )}

      {analysis && !error && (
        <>
          <Measures analysis={analysis} metric={scenario.metric} />
          <MovementContext analysis={analysis} />
          <Exhaustiveness analysis={analysis} />
          <TurnCheck path={analysis.principal} />
          <Isolation isolation={analysis.isolation} />
          <Corridor corridor={analysis.corridor} />
          <RouteGeometryNote geometry={analysis.geometry?.replacement} />
        </>
      )}

      {geometryWarning && (
        <RouteGeometryGap
          partCount={geometryWarning.partCount}
          skippedArcs={geometryWarning.skippedArcs}
        />
      )}

      {scenarioOpen && (
        <ScenarioControls
          id={controlsId}
          scenario={scenario}
          onChange={onScenarioChange}
          meta={meta}
          v2Capabilities={capabilities}
          direction={direction}
          onDirectionChange={onDirectionChange}
        />
      )}

      {analysis && !error && (
        <div className="disclosures">
          <details className="disclose">
            <summary>Closure</summary>
            <div className="body">
              <ClosureDetail analysis={analysis} />
            </div>
          </details>

          <details className="disclose">
            <summary>Movement detail</summary>
            <div className="body">
              <MovementDetail analysis={analysis} />
            </div>
          </details>

          <details className="disclose">
            <summary>Link attributes</summary>
            <div className="body">
              <LinkAttributes link={analysis.selectedLink} />
            </div>
          </details>

          <details className="disclose">
            <summary>
              Quality flags
              {allFlags(analysis).length > 0 && (
                <span className="flag" style={{ marginLeft: 4 }}>
                  {allFlags(analysis).length}
                </span>
              )}
            </summary>
            <div className="body">
              <QualityFlags flags={allFlags(analysis)} />
            </div>
          </details>

          <details className="disclose">
            <summary>Source &amp; methodology</summary>
            <div className="body">
              <SourceMethodology
                provenance={{
                  selectedLink: analysis.selectedLink,
                  closureGroupId: analysis.closure.closureGroupId,
                  snapshotId: analysis.snapshotId,
                  sourceDataset: meta?.sourceDataset,
                  retrievedAtUtc: meta?.retrievedAtUtc,
                  algorithm: analysis.algorithm,
                  algorithmVersion: analysis.algorithmVersion,
                  stability: analysis.stability,
                  licence: meta?.licence,
                  limitations: analysis.limitations ?? [],
                  attribution: analysis.attribution ?? meta?.attribution,
                }}
                meta={meta}
              />
            </div>
          </details>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------- what is being removed */

/**
 * The cost of the closure, before any result.
 *
 * Source-feature scope is the advanced choice, and its cost is that it removes
 * an AMDS source record's every graph child rather than the stretch of road
 * that was clicked. That cost has to be legible at the point of use — the
 * kilometres AND the number of segments, in the panel, above the figures they
 * qualify. In a tooltip it is something a reader discovers after acting.
 *
 * Both numbers, not one. A reader shown "1.4 km" cannot tell whether that is
 * one road or thirteen fragments of one maintenance record, and the second
 * reading is the one that explains why the figures below are not about the
 * road they pointed at.
 */
function ClosureCost({ analysis }: { analysis: V2BoundaryAnalysis }) {
  const { closure } = analysis;
  const scope = scopeOfResponse(closure.scope);
  if (scope !== 'amds-feature') return null;

  return (
    <div className="notice notice--warn" role="status">
      <div className="notice-title">
        This closes {km(closure.totalClosureLengthM)} across{' '}
        {count(closure.removedLinkCount)} graph segment
        {closure.removedLinkCount === 1 ? '' : 's'}
      </div>
      <p>
        {closure.warning?.detail ??
          'An AMDS source feature is a data-maintenance unit. Closing one ' +
            'removes every graph segment derived from that record, which may ' +
            'end where an authority’s responsibility ends rather than where ' +
            'the road does.'}
      </p>
      <p>
        The selected stretch of road is{' '}
        {km(closure.selectedSegmentLengthM)}
        {closure.excessLengthM > 0 && (
          <> — {inlineMetres(closure.excessLengthM)} beyond it is also removed</>
        )}
        . Every figure below describes the larger closure.
      </p>
    </div>
  );
}

/**
 * Low topology confidence, with its reason, above the figures.
 *
 * A label alone is not a caveat. "Topology confidence: low" tells a reader
 * there is a scale and that this is the bad end of it, and nothing about what
 * to do with that; the REASON is the part that says the connectivity these
 * figures rest on may be an artefact of an ingest tolerance rather than a
 * property of the roads, so the result can change without any road changing.
 */
function TopologyConfidence({ isolation }: { isolation: V2Isolation | null }) {
  if (!isolation || isolation.topologyConfidence !== 'low') return null;
  return (
    <div className="notice notice--warn" role="status">
      <div className="notice-title">
        Topology confidence low — treat the real-world reading with care
      </div>
      <p>{isolation.topologyConfidenceReason}</p>
      <p>
        Unresolved near-miss endpoints sit close to this closure. Whether the
        two sides join at all may follow from the tolerance used when the
        network was ingested rather than from the roads, so this result can
        change without any road changing. What follows describes the
        represented network; the confidence that it describes the road network
        here is lower than usual.
      </p>
    </div>
  );
}

/* --------------------------------------------------------------- headline */

/**
 * Which of the four pill colours a headline gets.
 *
 * `Partial analysis` and `Analysis unresolved` are NOT warnings about the road.
 * They are statements that the search did not settle, and colouring them like
 * an adverse finding would turn a bounded computation into a fact about a
 * place.
 */
function pillKind(a: V2BoundaryAnalysis): 'ok' | 'warn' | 'fault' {
  switch (a.headline) {
    case 'Analysis unresolved':
      return 'fault';
    case 'Partial analysis':
      return 'warn';
    case 'Through movement has no represented replacement':
      return 'warn';
    default:
      return 'ok';
  }
}

/**
 * The answer, sized as the answer.
 *
 * There are five headlines and only one of them has a number that belongs at
 * this size. The others get a sentence: inventing a zero, a dash or an infinity
 * so that the layout always has a big figure in it would be saying something
 * false about what was found.
 */
function Hero({
  analysis,
  revealKey,
}: {
  analysis: V2BoundaryAnalysis;
  revealKey: string;
}) {
  const { headline, principal, isolation } = analysis;

  if (headline === 'Through movement diverts' && principal?.networkPenaltyM != null) {
    const pen = signedKm(principal.networkPenaltyM);
    return (
      <HeroMetric
        label="Added distance — through movement"
        value={pen?.value ?? '—'}
        unit={pen?.unit ?? 'km'}
        revealKey={revealKey}
        detail={
          <>
            With this modelled closure, the shortest represented-network route
            for a trip that crossed here is{' '}
            {km(principal.replacementDistanceM)}, against{' '}
            {km(principal.intactDistanceM)} with the road in place.
          </>
        }
      />
    );
  }

  if (headline === 'Through movement has no represented replacement') {
    /*
     * TWO DIFFERENT FINDINGS SHARE THIS HEADLINE, and telling them apart is
     * the whole job here.
     *
     * One modelled crossing having no replacement is a statement about that
     * crossing. Links losing access altogether is a statement about a place:
     * it is computed on the undirected graph, it does not depend on the
     * routing, and where it holds it is the stronger finding and the headline.
     *
     * Where it does NOT hold, nothing here may read as "traffic cannot get
     * past". Review found five closures in this state — a highway pair, a
     * roundabout arm, a one-way carriageway, a city street and a motorway
     * connector — where a single crossing loses its route and the surrounding
     * network is entirely intact. "Road cut off" on any of those would be
     * false about the place while being true about the crossing, and it is the
     * place a reader acts on.
     */
    if (isolation?.physicallyIsolates && isolation.separatedLinkCount > 0) {
      const len = distance(isolation.separatedLengthM);
      return (
        <HeroMetric
          label="Road cut off"
          value={len?.value ?? '—'}
          unit={len?.unit ?? 'km'}
          revealKey={revealKey}
          detail={
            <>
              {count(isolation.separatedLinkCount)} link
              {isolation.separatedLinkCount === 1 ? '' : 's'} lose access in the
              represented physical-access graph. That graph is inferred from
              AMDS geometry, so this describes the network as represented, not
              as surveyed.
            </>
          }
        />
      );
    }
    return (
      <div className="headline">
        <div className="lab">
          One modelled through movement has no represented replacement
        </div>
        <p className="sub" style={{ marginTop: 8 }}>
          No physical isolation is identified in the represented graph. This is
          a statement about that one crossing, not about access to the
          surrounding area: the network around this closure is not separated,
          and other crossings of it may well have replacements.
        </p>
      </div>
    );
  }

  if (headline === 'No through movement identified') {
    return (
      <div className="headline">
        <div className="lab">No through movement identified</div>
        <p className="sub" style={{ marginTop: 8 }}>
          {analysis.movements.detail} No figure describes a trip here, because
          none was identified.
        </p>
      </div>
    );
  }

  if (headline === 'Partial analysis') {
    return (
      <div className="headline">
        <div className="lab">Partial analysis</div>
        <p className="sub" style={{ marginTop: 8 }}>
          The search was bounded and did not evaluate every crossing of this
          closure. What it did establish is below. What is withheld is any
          sentence that would imply it had looked at everything — an unevaluated
          crossing could hold a longer diversion, or the only one with no route
          at all.
        </p>
      </div>
    );
  }

  return (
    <div className="headline">
      <div className="lab">Analysis unresolved</div>
      <p className="sub" style={{ marginTop: 8 }}>
        {analysis.movements.detail ||
          'The analysis did not settle for this closure.'}{' '}
        A search that did not finish is not a finding about the road: it is not
        evidence that a route exists, and it is not evidence that none does.
      </p>
    </div>
  );
}

/* --------------------------------------------------------------- measures */

/**
 * The figures under the hero.
 *
 * Rows for a measure the request did not ask for are omitted rather than
 * dashed. An empty "Added time" row under a distance request reads as a
 * measurement that failed, when it is one that was never taken.
 */
function Measures({
  analysis,
  metric,
}: {
  analysis: V2BoundaryAnalysis;
  metric: string;
}) {
  const p = analysis.principal;
  if (!p) return null;

  /*
   * A path that did not resolve has no figures, and it has them in the
   * response.
   *
   * The distance, penalty and ratio are computed before the path is
   * adjudicated, so they are still populated on a route the engine afterwards
   * refused — a route using a prohibited turn is the case that matters. Reading
   * them off the response would print "350 m further" beside a headline saying
   * nothing was established, which is the fail-open this panel exists to avoid.
   * The route was found; it is not one this engine will offer.
   */
  if (!p.resolved) {
    return (
      <dl className="kv">
        <dt>With the road in place</dt>
        <dd>{km(p.intactDistanceM)}</dd>
        <dt>Replacement route</dt>
        <dd>not established ({p.status})</dd>
        <dt>Network penalty</dt>
        <dd>not established</dd>
      </dl>
    );
  }

  const r = ratio(p.ratio);
  return (
    <dl className="kv">
      <dt>With the road in place</dt>
      <dd>{km(p.intactDistanceM)}</dd>
      <dt>Replacement route</dt>
      <dd>
        {p.status === 'DISCONNECTED'
          ? 'none in the represented network'
          : km(p.replacementDistanceM)}
      </dd>
      <dt>Network penalty</dt>
      <dd>
        {p.networkPenaltyM === null
          ? '—'
          : `${inlineMetres(p.networkPenaltyM)} further`}
        {r !== null && ` (${r}×)`}
      </dd>
      {metric === 'time' && (
        <>
          {/* AMDS publishes no speed attribute. Naming the figure "estimated"
            * in the label is the only place a reader reliably meets that,
            * because the number itself looks like a measurement. */}
          <dt>Added time (estimated)</dt>
          <dd>
            {p.addedTimeS === null
              ? '—'
              : (() => {
                  const d = duration(p.addedTimeS);
                  return d ? `${d.value} ${d.unit}` : '—';
                })()}
          </dd>
        </>
      )}
    </dl>
  );
}

/* ------------------------------------------------ which movement, of how many */

/** A road name, or an honest statement that the link has none. */
function roadOf(name: string | null | undefined, node: number): string {
  return name ?? `an unnamed road (node ${node})`;
}

/**
 * WHICH crossing the figures describe, and how many there were to choose from.
 *
 * The engine picks one movement out of everything crossing the closure and
 * reports it. An independent oracle confirms the route conclusion FOR THAT
 * MOVEMENT; it does not establish that it was the movement a reader cares
 * about. A closure on an ordinary urban street can have a hundred and thirty
 * included crossings, and "added distance 340 m" with no subject is a number
 * about an unnamed one of them.
 *
 * So the subject is stated in the panel, not in a disclosure: which roads the
 * crossing enters and leaves by, how many were identified, and — because the
 * choice is the engine's and a reader may disagree with it — the alternatives,
 * listed, with the intact distance that ranked them.
 */
function MovementContext({ analysis }: { analysis: V2BoundaryAnalysis }) {
  const m = analysis.principal?.movement ?? null;
  if (!m) return null;

  const others = analysis.movements.movements.filter(
    (x) => x.included && x.movementId !== m.movementId,
  );

  return (
    <>
      <p className="note">
        Measured on one crossing of {count(analysis.movements.includedCount)}{' '}
        identified here: in from {roadOf(m.entryRoadName, m.entryNode)}, out to{' '}
        {roadOf(m.exitRoadName, m.exitNode)}. It was chosen as the crossing this
        closure affects most, not as the busiest — nothing here knows traffic
        volumes.
      </p>

      {others.length > 0 && (
        <details className="disclose">
          <summary>
            The other {count(others.length)} crossing
            {others.length === 1 ? '' : 's'} identified
          </summary>
          <div className="body">
            <p className="note">
              Every crossing the search included, with the distance it took
              before the closure. A reader who cares about a different one can
              see it here rather than take the engine&rsquo;s choice on trust.
            </p>
            {others.slice(0, 40).map((o) => (
              <div className="b-row" key={o.movementId}>
                <span>
                  {roadOf(o.entryRoadName, o.entryNode)} &rarr;{' '}
                  {roadOf(o.exitRoadName, o.exitNode)}
                  {o.confidence !== 'high' && ` (${o.confidence} confidence)`}
                </span>
                <span className="n">{km(o.intactDistanceM)}</span>
              </div>
            ))}
            {others.length > 40 && (
              <p className="note">
                {count(others.length - 40)} more not listed. The count above is
                exact; this list is capped so the panel stays readable.
              </p>
            )}
          </div>
        </details>
      )}
    </>
  );
}

/* --------------------------------------------------------- exhaustiveness */

/**
 * Whether every crossing was actually evaluated.
 *
 * A caveat LINE rather than a buried field. The figures above describe what was
 * evaluated; a reader who does not know the search was bounded will read them
 * as describing the closure.
 */
function Exhaustiveness({ analysis }: { analysis: V2BoundaryAnalysis }) {
  const m = analysis.movements;
  if (m.exhaustive) return null;
  return (
    <div className="notice notice--warn" role="status">
      <div className="notice-title">
        This search did not evaluate every crossing
      </div>
      <p>
        {count(m.omittedPairCount)} candidate crossing
        {m.omittedPairCount === 1 ? ' was' : 's were'} left unevaluated
        {m.closureComponents > 1 &&
          `, including every crossing spanning the ${count(
            m.closureComponents,
          )} disconnected pieces of this closure`}
        . The figures above describe what WAS evaluated. An unevaluated crossing
        could hold a longer diversion, or the only one with no replacement at
        all, so nothing here is offered as the whole picture.
      </p>
    </div>
  );
}

/* ------------------------------------------------------- turn restrictions */

/**
 * Banned manoeuvres.
 *
 * The route searches run on the plain arc graph, which knows nothing about
 * turns, so this is a check made AFTER a route is found rather than a
 * constraint on finding it. It fails closed — a route that uses a published
 * applicable restriction is withheld rather than offered — and the panel says
 * so either way.
 *
 * The "no banned manoeuvre" case still gets a sentence, because the sentence a
 * reader would otherwise supply for themselves ("so this route is legal") is
 * exactly wrong: AMDS publishes 60 restricted turns for the whole country and
 * one of them restricts any modelled vehicle class. Passing this check is
 * almost no evidence at all.
 */
function TurnCheck({ path }: { path: V2ReplacementPath | null }) {
  const tc = path?.turnCheck;
  if (!tc || !tc.checked) return null;
  if (tc.ok) {
    return (
      <p className="note">
        No banned manoeuvre on this route ({count(tc.applicableRestrictions)}{' '}
        published restriction
        {tc.applicableRestrictions === 1 ? '' : 's'} apply to this vehicle
        nationally). Restrictions are checked after the route is found, and
        published coverage is negligible, so this is not a claim that the route
        is road-legal.
      </p>
    );
  }
  return (
    <div className="notice notice--warn" role="status">
      <div className="notice-title">A prohibited turn was found on this route</div>
      <p>
        {count(tc.violationCount)} banned manoeuvre
        {tc.violationCount === 1 ? '' : 's'} on the shortest represented route,
        so it is withheld and is not drawn. That is not the same as there being
        no way round: this engine did not establish one that clears the
        published restrictions.
      </p>
    </div>
  );
}

/* -------------------------------------------------------------- isolation */

/**
 * Physical isolation, in its own block, in its own words.
 *
 * This is the strongest claim the system makes. It is computed on the
 * undirected graph, it does not depend on any route search, and a timeout in
 * the routing cannot change it. Keeping it separate is what stops a routing
 * failure producing a headline about a road losing access.
 */
function Isolation({ isolation }: { isolation: V2Isolation | null }) {
  if (!isolation) return null;
  const iso = isolation;
  return (
    <>
      {/* A low topology confidence is raised ABOVE the figures, by
        * `TopologyConfidence`, not here. It says the connectivity these
        * figures describe may be an artefact of how the network was assembled,
        * and a reader who meets that after the numbers has already believed
        * them. It is not repeated here; the flag itself is in the table below. */}
      <h4>Physical isolation — a separate question</h4>
      <p>
        {iso.physicallyIsolates && iso.separatedLinkCount > 0
          ? `${count(iso.separatedLinkCount)} link${
              iso.separatedLinkCount === 1 ? '' : 's'
            } (${km(iso.separatedLengthM)}) lose access in the represented physical-access graph.`
          : 'Nothing loses access in the represented physical-access graph.'}
        {iso.separatedTruncated && ' The id list is capped; the counts are exact.'}
      </p>
      {/*
        Deliberately does NOT use the phrase "cut off" to explain itself.

        The five reviewed closures that lose one crossing while the network
        stays intact must show that phrase nowhere at all, and an explanatory
        sentence sitting under a result is exactly what survives into a
        screenshot with its qualifier lost. The word belongs to the hero, in
        the one branch entitled to it, and nowhere else.
      */}
      <p className="note">
        Undirected, and independent of the routing above: one crossing having
        no replacement is not the same as a road losing access, and this is the
        block that settles whether anything lost access.
      </p>

      <dl className="kv">
        <dt>Method</dt>
        <dd>{iso.method}</dd>
        {/*
          Three claims, kept apart. A single "exact" merged them, and the only
          reading it invited was the strongest one: that the answer holds of the
          road network. It does not. The partition is exact; the graph it
          partitions is inferred.
        */}
        <dt>Calculation</dt>
        <dd>
          {iso.calculationExact
            ? 'exact — no search bound was involved'
            : 'not exact'}
        </dd>
        <dt>Partition</dt>
        <dd>{iso.partitionExact ? 'exact' : 'not exact'}</dd>
        <dt>Graph</dt>
        <dd>
          {iso.graphExact
            ? 'models the road network'
            : 'inferred topology — not an exact model of the road network'}
        </dd>
        <dt>Topology confidence</dt>
        <dd>{iso.topologyConfidence}</dd>
        <dt>Closure is a bridge</dt>
        <dd>{iso.closureIsBridge ? 'yes' : 'no'}</dd>
        <dt>Components</dt>
        <dd>
          {count(iso.componentCount)}
          {iso.componentsTruncated ? ' (list truncated)' : ''}
        </dd>
        <dt>Principal side</dt>
        <dd>
          {iso.principalSideRule} ({iso.principalSideConfidence} confidence)
        </dd>
      </dl>

      {iso.topologyConfidence !== 'low' && iso.topologyConfidenceReason && (
        <p className="note">{iso.topologyConfidenceReason}</p>
      )}

      {/*
        Which side lost access is a policy, not a theorem. Where the policy
        cannot decide, saying so is the finding — naming a side anyway would
        assert a direction the data does not carry.
      */}
      {iso.principalSideAmbiguous && (
        <p className="note">
          Neither side was named as the one that lost access: neither carries a
          decisive anchor, so what is supported is that the network splits into
          two represented components, not that one of them lost its connection.
        </p>
      )}

      {iso.components
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
    </>
  );
}

/* --------------------------------------------------------------- corridor */

function portLabel(p: V2CorridorPort | undefined): string {
  if (!p) return 'an unidentified point';
  const name = p.roadName ?? p.routeDesignation ?? `node ${p.node}`;
  return `${name} (${inlineMetres(p.outwardDistanceM)} out${
    p.isDecisionPoint ? ', a junction' : ', a through point'
  })`;
}

function Corridor({ corridor }: { corridor: V2Corridor | null }) {
  if (!corridor || !corridor.chosenPair) return null;
  const up = corridor.upstreamCandidates.find(
    (c) => c.candidateId === corridor.chosenPair!.upstreamId,
  );
  const down = corridor.downstreamCandidates.find(
    (c) => c.candidateId === corridor.chosenPair!.downstreamId,
  );
  return (
    <>
      <h4>Where the diversion runs</h4>
      <dl className="kv">
        <dt>Leaves the route at</dt>
        <dd>{portLabel(up)}</dd>
        <dt>Rejoins at</dt>
        <dd>{portLabel(down)}</dd>
        <dt>Between them</dt>
        <dd>{km(corridor.chosenPair.replacementCostM)}</dd>
      </dl>
      {/* An incompletely evaluated candidate set cannot support a confident
        * "the diversion starts here". */}
      {corridor.evaluationTruncated && (
        <div className="notice notice--warn" role="status">
          <div className="notice-title">This starting point is provisional</div>
          <p>
            The corridor search generated{' '}
            {count(
              corridor.candidatesGeneratedUpstream +
                corridor.candidatesGeneratedDownstream,
            )}{' '}
            candidate places and evaluated{' '}
            {count(
              corridor.candidatesEvaluatedUpstream +
                corridor.candidatesEvaluatedDownstream,
            )}
            . A candidate it did not evaluate could have made a better pair, so
            where the diversion is said to start is provisional. The movement
            figures above are unaffected — they do not depend on the corridor.
          </p>
        </div>
      )}
      {/* The rule that chose it, in the engine's own sentence. A panel that
        * summarised it would be a second implementation of the rule. */}
      <p className="note">{corridor.explanation}</p>
    </>
  );
}

/* --------------------------------------------------------------- geometry */

/**
 * What may be drawn, and what may not.
 *
 * A gapped route is reported as gapped. The map draws the contiguous pieces and
 * nothing between them, and the reveal is disabled, because an animation that
 * sweeps along a line asserts the line is unbroken.
 */
function RouteGeometryNote({ geometry }: { geometry?: V2RouteGeometry }) {
  if (!geometry || geometry.continuous) return null;
  return (
    <div className="notice notice--warn" role="status">
      <div className="notice-title">The drawn route has gaps</div>
      <p>
        The replacement route is drawn as {count(geometry.pieceCount)} separate
        pieces with {count(geometry.gapCount)} gap
        {geometry.gapCount === 1 ? '' : 's'} between them
        {geometry.gaps.length > 0 &&
          ` (widest ${inlineMetres(
            Math.max(...geometry.gaps.map((g) => g.distanceM)),
          )})`}
        . Nothing is drawn across a gap and the route reveal is turned off: the
        pieces are real, the joins between them are not in the data.
      </p>
      {geometry.missingArcIds.length > 0 && (
        <p>
          {count(geometry.missingArcIds.length)} section
          {geometry.missingArcIds.length === 1 ? ' has' : 's have'} no geometry
          at all.
        </p>
      )}
    </div>
  );
}

/* -------------------------------------------------------------- disclosure */

function ClosureDetail({ analysis }: { analysis: V2BoundaryAnalysis }) {
  const { closure, boundary } = analysis;
  const scope = scopeOfResponse(closure.scope);
  return (
    <>
      {/*
        The warning is the whole point of surfacing source-feature scope at
        all: the figures describe a closure larger than the one the reader
        selected, and they have to be told before they read them, not after.
      */}
      {closure.warning && (
        <div className="notice notice--warn" role="status">
          <div className="notice-title">{closure.warning.headline}</div>
          <p>{closure.warning.detail}</p>
        </div>
      )}
      <dl className="kv">
        <dt>Scope</dt>
        <dd>{closureLabel(scope)}</dd>
        <dt>Selected segment</dt>
        <dd>{km(closure.selectedSegmentLengthM)}</dd>
        <dt>Total removed</dt>
        <dd>{km(closure.totalClosureLengthM)}</dd>
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
        <dt>Closure boundary</dt>
        <dd>
          {count(boundary.entryPortCount)} way
          {boundary.entryPortCount === 1 ? '' : 's'} in,{' '}
          {count(boundary.exitPortCount)} way
          {boundary.exitPortCount === 1 ? '' : 's'} out
        </dd>
      </dl>
      <p className="note">{boundary.detail}</p>
    </>
  );
}

function MovementDetail({ analysis }: { analysis: V2BoundaryAnalysis }) {
  const m = analysis.principal?.movement ?? null;
  if (!m || !analysis.principal) {
    return <p className="note">{analysis.movements.detail}</p>;
  }
  return (
    <>
      <p className="note">
        {count(analysis.movements.includedCount)} crossing
        {analysis.movements.includedCount === 1 ? '' : 's'} identified from{' '}
        {count(analysis.movements.candidatePairs)} considered. The one measured
        enters at node {m.entryNode} and leaves at node {m.exitNode}, crossing
        from {m.fromNode} to {m.toNode}.
      </p>
      <dl className="kv">
        <dt>Movement confidence</dt>
        <dd>
          {m.confidence}
          {m.evidence.length > 0 && ` — ${m.evidence.join(', ')}`}
        </dd>
        <dt>Replacement status</dt>
        <dd>{analysis.principal.status}</dd>
        <dt>Topology confidence</dt>
        <dd>{analysis.principal.topologyConfidence}</dd>
      </dl>
      <p className="note">{analysis.principal.detail}</p>
      <details>
        <summary>Runtime by stage</summary>
        <dl className="kv">
          {Object.entries(analysis.stageMs).map(([k, v]) => (
            <div key={k} style={{ display: 'contents' }}>
              <dt>{k.replace(/_/g, ' ')}</dt>
              <dd>{v} ms</dd>
            </div>
          ))}
        </dl>
      </details>
    </>
  );
}

function allFlags(a: V2BoundaryAnalysis): string[] {
  return [
    ...new Set([
      ...(a.selectedLink.qualityFlags ?? []),
      ...a.qualityFlags,
      ...(a.principal?.qualityFlags ?? []),
    ]),
  ];
}

/* ----------------------------------------------------------- legacy links */

/**
 * A link made before the promotion, and what was done with it.
 *
 * Shown once, dismissible, and it names both scopes. The alternative — quietly
 * re-reading the old scope as the new default — would change what was analysed
 * while the link still looked unchanged, which is the failure mode this notice
 * exists to make impossible.
 *
 * It also offers the original scope back. A reader who deliberately shared a
 * source-feature analysis is not helped by being told their link was changed
 * and left to reconstruct it from the controls.
 */
function LegacyLinkNotice({
  migration,
  onDismiss,
  onRestoreScope,
}: {
  migration: LegacyMigration;
  onDismiss: () => void;
  onRestoreScope: (scope: ClosureScope) => void;
}) {
  return (
    <div className="notice notice--info" role="status">
      <div className="notice-title">This link was made before the current engine</div>
      <p>
        It asked for {closureLabel(migration.requestedScope).toLowerCase()}. It is
        showing {closureLabel(migration.appliedScope).toLowerCase()} — the
        stretch of road the link points at — measured across the closure
        boundary rather than between the closed feature&rsquo;s own two
        endpoints. Both what was closed and how it was measured differ from the
        figures the link was made against, so treat them as a new analysis
        rather than the saved one.
      </p>
      <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
        <button
          type="button"
          className="pbtn"
          onClick={() => onRestoreScope(migration.requestedScope)}
        >
          Close the {closureLabel(migration.requestedScope).toLowerCase()} instead
        </button>
        <button type="button" className="pbtn" onClick={onDismiss}>
          Dismiss
        </button>
      </div>
    </div>
  );
}
