/*
 * The inspector's content, in the approved order:
 *
 *   1  road name
 *   2  direction and represented-network status
 *   3  added distance
 *   4  replacement-path distance
 *   5  detour multiplier
 *   6  estimated added time
 *   7  forward/reverse comparison
 *   8  scenario settings and exact closure scope
 *   9  applicable confidence and limitations
 *  10  route detail and source methodology, under disclosure
 *
 * The sticky scenario summary sits above the head so it stays reachable while
 * the panel scrolls; the full controls open in place beneath the comparison,
 * where changing them does not push the answer off screen.
 */

import { useId } from 'react';

import ConfidenceNotice from './ConfidenceNotice.js';
import CorridorPanel from './CorridorPanel.js';
import DirectionComparison from './DirectionComparison.js';
import DirectionTabs, { type DirectionView } from './DirectionTabs.js';
import HeroMetric, { HeroSkeleton, detourDetail } from './HeroMetric.js';
import IsolationPanel from './IsolationPanel.js';
import LinkAttributes from './LinkAttributes.js';
import LinkHeader from './LinkHeader.js';
import QualityFlags from './QualityFlags.js';
import ResultStatus, { statusKindOf } from './ResultStatus.js';
import RouteBreakdown from './RouteBreakdown.js';
import ScenarioControls from './ScenarioControls.js';
import ScenarioSummary from './ScenarioSummary.js';
import SecondaryMeasures, { measureRows } from './SecondaryMeasures.js';
import SourceMethodology from './SourceMethodology.js';
import { RouteGeometryGap, SnapshotMismatch } from './ResultNotices.js';
import { distance, signedKm } from '../lib/format.js';
import {
  closureLabel,
  scopeOfResponse,
  summariseScenario,
  type DirectionKey,
  type Scenario,
} from '../api/scenario.js';
import type { GeometryWarning } from '../map/NetworkMap.js';
import type { DetourResponse, NetworkMetadata } from '../api/types.js';

export interface ResultViewProps {
  detour: DetourResponse | null;
  meta: NetworkMetadata | null;
  /** The road name is known from the click before the result arrives. */
  pendingName: string | null;
  loading: boolean;
  /** Set while the result on screen predates the current scenario. */
  stale: boolean;
  scenario: Scenario;
  onScenarioChange: (s: Scenario) => void;
  scenarioOpen: boolean;
  onScenarioToggle: () => void;
  view: DirectionView;
  onViewChange: (v: DirectionView) => void;
  onClear: () => void;
  /** Set when the permalink names a snapshot other than the active one. */
  snapshotMismatch: { requested: string; active: string } | null;
  /** Set when the map could not draw the route as a continuous path. */
  geometryWarning: GeometryWarning | null;
  /** Announced when the focused direction was moved automatically. */
  directionNotice: string | null;
}

export default function ResultView({
  detour,
  meta,
  pendingName,
  loading,
  stale,
  scenario,
  onScenarioChange,
  scenarioOpen,
  onScenarioToggle,
  view,
  onViewChange,
  onClear,
  snapshotMismatch,
  geometryWarning,
  directionNotice,
}: ResultViewProps) {
  const controlsId = useId();
  const panelId = useId();

  const focus: DirectionKey = view === 'compare' ? 'reverse' : view;
  const focused = detour ? detour[focus] : null;
  const other = detour ? detour[focus === 'reverse' ? 'forward' : 'reverse'] : null;

  const summary = summariseScenario(scenario, meta);
  const showSkeleton = loading || !detour;

  /* The reveal replays whenever the underlying figures change, and only then.
   * Switching direction is a switch between two already-computed results, so
   * it cross-fades rather than re-revealing. */
  const revealKey = detour
    ? `${detour.snapshotId}:${detour.selectedLink.linkId}:${scenario.metric}:${scenario.vehicle}:${scenario.closureScope}:${focus}`
    : 'pending';

  const available = {
    forward: Boolean(detour?.forward),
    reverse: Boolean(detour?.reverse),
  };

  return (
    <>
      <ScenarioSummary
        summary={summary}
        open={scenarioOpen}
        onToggle={onScenarioToggle}
        controlsId={controlsId}
        dirty={stale}
      />

      <LinkHeader
        link={detour?.selectedLink ?? null}
        pendingName={pendingName}
        onClear={onClear}
      />

      {snapshotMismatch && (
        <SnapshotMismatch
          requested={snapshotMismatch.requested}
          active={snapshotMismatch.active}
        />
      )}

      {/* aria-live so a screen-reader user is told the outcome without having
       * to go looking for it. `polite` because it must not interrupt. */}
      <div aria-live="polite" aria-atomic="true">
        {/* An automatic direction change is announced here rather than left
         * for the user to notice: the tab they asked for is not the tab they
         * got, and silently swapping it is disorienting. */}
        {directionNotice && <p className="sr-only">{directionNotice}</p>}
        {showSkeleton ? (
          <div className="status-row">
            <span className="dir-tag">{focus}</span>
            <span className="status-pill" data-kind="ok">
              Calculating&hellip;
            </span>
          </div>
        ) : (
          focused && (
            <ResultStatus
              direction={focus}
              status={focused.status}
              meaning={focused.statusMeaning}
            />
          )
        )}
      </div>

      {showSkeleton ? (
        <HeroSkeleton />
      ) : (
        focused && <Hero result={focused} detour={detour!} revealKey={revealKey} />
      )}

      {directionNotice && (
        <div className="notice notice--info">
          <p>{directionNotice}</p>
        </div>
      )}

      <SecondaryMeasures
        rows={measureRows(focused?.metrics ?? null, focused?.status)}
        loading={showSkeleton}
        revealKey={revealKey}
      />

      {geometryWarning && (
        <RouteGeometryGap
          partCount={geometryWarning.partCount}
          skippedArcs={geometryWarning.skippedArcs}
        />
      )}

      {/* Only when something is genuinely stranded. A pocket of zero links is
        * not an isolation finding, and showing it as one turns a routine
        * one-way artefact into an alarming panel about severed access. */}
      {focused?.status === 'DISCONNECTED' &&
        focused.isolation &&
        (focused.isolation.pocketLinkCount > 0 ||
          focused.isolation.pocketLengthM > 0) && (
          <IsolationPanel isolation={focused.isolation} />
        )}

      {focused?.status === 'DISCONNECTED' && focused.corridor?.status === 'OK' && (
        <CorridorPanel corridor={focused.corridor} />
      )}

      <DirectionTabs
        view={view}
        onChange={onViewChange}
        available={available}
        panelId={panelId}
      />

      <div id={panelId} role="tabpanel">
        {detour && (available.forward || available.reverse) && (
          <DirectionComparison
            forward={detour.forward}
            reverse={detour.reverse}
            focus={focus}
          />
        )}
      </div>

      {scenarioOpen && (
        <ScenarioControls
          id={controlsId}
          scenario={scenario}
          onChange={onScenarioChange}
          meta={meta}
        />
      )}

      {detour && <ConfidenceNotice detour={detour} meta={meta} />}

      {detour && (
        <div className="disclosures">
          <details className="disclose">
            <summary>Route detail</summary>
            <div className="body">
              <RouteBreakdown result={focused} />
            </div>
          </details>

          <details className="disclose">
            <summary>Link attributes</summary>
            <div className="body">
              <LinkAttributes link={detour.selectedLink} />
            </div>
          </details>

          <details className="disclose">
            <summary>
              Quality flags
              {allFlags(detour, focused?.qualityFlags).length > 0 && (
                <span className="flag" style={{ marginLeft: 4 }}>
                  {allFlags(detour, focused?.qualityFlags).length}
                </span>
              )}
            </summary>
            <div className="body">
              <QualityFlags flags={allFlags(detour, focused?.qualityFlags)} />
            </div>
          </details>

          <details className="disclose">
            <summary>Source &amp; methodology</summary>
            <div className="body">
              <SourceMethodology
                provenance={{
                  selectedLink: detour.selectedLink,
                  closureGroupId: detour.closure.closureGroupId,
                  snapshotId: detour.snapshotId,
                  sourceDataset: detour.sourceDataset,
                  retrievedAtUtc: detour.retrievedAtUtc,
                  calculatedAtUtc: detour.calculatedAtUtc,
                  algorithm: detour.algorithm,
                  algorithmVersion: detour.algorithmVersion,
                  licence: detour.licence,
                  limitations: detour.limitations,
                  attribution: detour.attribution,
                }}
                meta={meta}
              />
            </div>
          </details>
        </div>
      )}
    </>
  );
}

function allFlags(
  detour: DetourResponse,
  resultFlags: string[] | undefined,
): string[] {
  return [
    ...new Set([...(detour.selectedLink.qualityFlags ?? []), ...(resultFlags ?? [])]),
  ];
}

/**
 * The hero switches meaning when there is no replacement path.
 *
 * "Added distance" has no answer for a DISCONNECTED result, and inventing one —
 * an infinity, a zero, a dash — would be worse than answering the question the
 * result does settle, which is how much is cut off.
 */
function Hero({
  result,
  detour,
  revealKey,
}: {
  result: NonNullable<DetourResponse['forward']>;
  detour: DetourResponse;
  revealKey: string;
}) {
  const kind = statusKindOf(result.status);

  if (kind === 'fault') {
    return (
      <div className="headline">
        <div className="lab">Analysis unresolved</div>
        <p className="sub" style={{ marginTop: 8 }}>
          {result.errorDetail ||
            'The analysis did not produce a result for this direction. The ' +
              'status code above says why.'}
        </p>
      </div>
    );
  }

  if (result.status === 'DISCONNECTED') {
    /*
     * DISCONNECTED does not mean "cut off". On a one-way carriageway it is
     * routine — the API says so in `statusMeaning` — because the endpoint
     * measure asks for a path from a link's end back to its start, which a
     * one-way link does not have and never did.
     *
     * An earlier version headlined every DISCONNECTED as "Road cut off", and on
     * a one-way state-highway segment it read "Road cut off: 0 m": the pocket
     * was one node, zero links, zero metres, and traffic in fact got past with
     * +2.46 km. That is the exact misreading the backend warns against.
     *
     * So the headline follows what was actually found, in order of what the
     * reader most needs to know.
     */
    const corridor = result.corridor;
    const stranded = result.isolation;
    const hasPocket =
      (stranded?.pocketLinkCount ?? 0) > 0 || (stranded?.pocketLengthM ?? 0) > 0;

    /* 1. A through-trip comparison exists: that is the useful measure, and the
     *    honest one. Traffic gets past; the endpoint question was ill-posed. */
    if (corridor?.status === 'OK' && corridor.penaltyM !== null) {
      const pen = signedKm(corridor.penaltyM);
      return (
        <HeroMetric
          label="Added distance — through trip"
          value={pen?.value ?? '—'}
          unit={pen?.unit ?? 'km'}
          revealKey={revealKey}
          detail={
            <>
              This is a one-way carriageway, so there is no path from the
              link&rsquo;s end back to its start and the endpoint measure is
              undefined. Measured instead between the nearest points upstream
              and downstream at which a driver has a choice.
            </>
          }
        />
      );
    }

    /* 2. The through-trip search did not settle — it timed out, or the service
     *    failed. Nothing below may run: "Road cut off" and "no replacement
     *    path" both assert that traffic cannot get past, and the one search
     *    that could have shown otherwise never finished. The endpoint measure
     *    being DISCONNECTED does not settle it either; on a one-way
     *    carriageway that result is routine and traffic gets past anyway. */
    if (corridor && statusKindOf(corridor.status) === 'fault') {
      return (
        <div className="headline">
          <div className="lab">Analysis unresolved</div>
          <p className="sub" style={{ marginTop: 8 }}>
            The endpoint measure found no path from the link&rsquo;s start back
            to its own end, which is routine on a one-way carriageway. The
            through-trip comparison that would say whether traffic still gets
            past did not finish, so whether it does is unknown. This is not a
            finding that the road is cut off.
          </p>
        </div>
      );
    }

    /* 3. Something really is stranded. */
    if (hasPocket) {
      const len = distance(stranded!.pocketLengthM);
      return (
        <HeroMetric
          label="Road cut off"
          value={len?.value ?? '—'}
          unit={len?.unit ?? 'km'}
          revealKey={revealKey}
          detail={
            <>
              With this modelled closure, no replacement path exists in the
              represented network. It strands {stranded!.pocketLinkCount} links.
            </>
          }
        />
      );
    }

    /* 4. None of those. There is no number to give, and inventing a zero would
     *    say something false about what was found. */
    return (
      <div className="headline">
        <div className="lab">No replacement path</div>
        <p className="sub" style={{ marginTop: 8 }}>
          With this modelled closure, no path exists between the selected
          link&rsquo;s own endpoints, and no through-trip comparison could be
          computed. Nothing is stranded — this measures the link&rsquo;s
          endpoints, not the surrounding area.
        </p>
      </div>
    );
  }

  const added = signedKm(result.metrics.addedDistanceVsLinkM);
  return (
    <HeroMetric
      label="Added distance"
      value={added?.value ?? '—'}
      unit={added?.unit ?? 'km'}
      revealKey={revealKey}
      detail={detourDetail({
        alternativeM: result.metrics.alternativeDistanceM,
      })}
    />
  );
}
