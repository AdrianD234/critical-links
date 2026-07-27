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
import { distance, signedKm } from '../lib/format.js';
import { summariseScenario, type DirectionKey, type Scenario } from '../api/scenario.js';
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
        roadName={detour?.selectedLink.roadName ?? pendingName}
        onClear={onClear}
      />

      {/* aria-live so a screen-reader user is told the outcome without having
       * to go looking for it. `polite` because it must not interrupt. */}
      <div aria-live="polite" aria-atomic="true">
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

      <SecondaryMeasures
        rows={measureRows(focused?.metrics ?? null)}
        loading={showSkeleton}
        revealKey={revealKey}
      />

      {focused?.isolation && focused.status === 'DISCONNECTED' && (
        <IsolationPanel isolation={focused.isolation} />
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
              <SourceMethodology detour={detour} meta={meta} />
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
        <div className="lab">No result</div>
        <p className="sub" style={{ marginTop: 8 }}>
          {result.errorDetail ||
            'The analysis did not produce a result for this direction. The ' +
              'status code above says why.'}
        </p>
      </div>
    );
  }

  if (result.status === 'DISCONNECTED') {
    const len = distance(result.isolation?.pocketLengthM ?? null);
    return (
      <HeroMetric
        label="Road cut off"
        value={len?.value ?? '—'}
        unit={len?.unit ?? 'km'}
        revealKey={revealKey}
        detail={
          <>
            No replacement path exists in the represented network. This closure
            strands {result.isolation?.pocketLinkCount ?? 0} links.
          </>
        }
      />
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
        selectedLengthM: detour.selectedLink.lengthM,
        alternativeM: result.metrics.alternativeDistanceM,
      })}
    />
  );
}
