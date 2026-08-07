/*
 * The boundary-movement result, in the plainest form that is still honest.
 *
 * This is the answer PR 2 exists to produce, so it leads. But it is a THIRD
 * measure, not a correction of the two above it, and the panel is built so a
 * reader cannot mistake it for one:
 *
 *   - the exact scope is stated before any number, because a figure for a
 *     2.5 km source feature and a figure for the 2 m segment inside it are
 *     both correct and answer different questions;
 *   - the movement being measured is named - which crossing, entered from
 *     where, leaving to where - because "the detour is 8 km" means nothing
 *     until you know whose trip it is;
 *   - PHYSICAL ISOLATION has its own block and its own words. It is an
 *     undirected fact about the graph and V1's central defect was letting a
 *     routing result produce its headline;
 *   - DIRECTIONAL ACCESS is likewise separate: one traversal failing is not a
 *     road losing access;
 *   - confidence and quality flags are shown, not filtered. A flag nobody
 *     sees is a flag that does not exist.
 *
 * Everything here is development-only and says so. The stability string comes
 * from the response and is rendered verbatim: paraphrasing it would put this
 * file in the business of deciding how much to trust the backend.
 */

import { count, distance, inlineMetres, ratio } from '../lib/format.js';
import type {
  V2BoundaryAnalysis,
  V2Corridor,
  V2CorridorPort,
  V2ReplacementPath,
  V2RouteGeometry,
} from '../api/types.js';

/**
 * `distance` returns a value and its unit separately, so a hero can size them
 * differently. Nothing here is a hero, and an em dash is the house style for a
 * figure that does not exist.
 */
function km(m: number | null | undefined): string {
  const d = distance(m);
  return d === null ? '—' : `${d.value} ${d.unit}`;
}

export default function V2BoundaryFindings({
  analysis,
  loading,
  error,
  onRetry,
}: {
  analysis: V2BoundaryAnalysis | null;
  loading: boolean;
  error: Error | null;
  onRetry: () => void;
}) {
  if (error) {
    return (
      <section className="v2-boundary">
        <h3>Boundary-movement measure</h3>
        <p className="muted">{error.message}</p>
        <button type="button" className="pbtn" onClick={onRetry}>
          Try again
        </button>
      </section>
    );
  }
  if (loading || !analysis) {
    return (
      <section className="v2-boundary">
        <h3>Boundary-movement measure</h3>
        <p className="muted">Calculating&hellip;</p>
      </section>
    );
  }

  const { principal, closure, movements, boundary } = analysis;
  const m = principal?.movement ?? null;

  return (
    <section className="v2-boundary">
      <h3>Boundary-movement measure &mdash; {analysis.stability}</h3>

      <p className="muted">{analysis.comparableToV1Detail}</p>

      {/* ---- what is closed, exactly ---- */}
      <dl className="kv">
        <dt>Closure scope</dt>
        <dd>
          {closure.scope === 'segment'
            ? 'the selected graph segment'
            : closure.scope === 'direction'
              ? `one directed traversal (${closure.direction ?? 'unspecified'})`
              : 'every graph segment of one AMDS source feature'}
          {' — '}
          {count(closure.removedLinkCount)} segment
          {closure.removedLinkCount === 1 ? '' : 's'},{' '}
          {km(closure.totalClosureLengthM)}
        </dd>
        <dt>Selected segment</dt>
        <dd>{km(closure.selectedSegmentLengthM)}</dd>
        <dt>Closure boundary</dt>
        <dd>
          {count(boundary.entryPortCount)} way
          {boundary.entryPortCount === 1 ? '' : 's'} in,{' '}
          {count(boundary.exitPortCount)} way
          {boundary.exitPortCount === 1 ? '' : 's'} out
          {boundary.reducesToEndpoints
            ? ' — all on the segment’s own two ends, so this measure and the endpoint measure ask the same question here'
            : ' — not confined to the segment’s own two ends, so this closure cannot be described by them'}
        </dd>
      </dl>

      {/* ---- the movement being measured ---- */}
      <h4>Movement evaluated</h4>
      {m === null || principal === null ? (
        <p className="muted">
          {movements.detail} No figure below describes a trip, because none was
          identified.
        </p>
      ) : (
        <>
          <p>
            One trip through the closure, of{' '}
            {count(movements.includedCount)} identified from{' '}
            {count(movements.candidatePairs)} considered: entering at node{' '}
            {m.entryNode} and leaving at node {m.exitNode}, crossing from{' '}
            {m.fromNode} to {m.toNode}.
          </p>
          <dl className="kv">
            <dt>Headline</dt>
            <dd>{analysis.headline}</dd>
            <dt>As it was</dt>
            <dd>{km(principal.intactDistanceM)}</dd>
            <dt>Replacement</dt>
            <dd>
              {principal.status === 'OK'
                ? km(principal.replacementDistanceM)
                : principal.status === 'DISCONNECTED'
                  ? 'none in the represented network'
                  : `not established (${principal.status})`}
            </dd>
            <dt>Network penalty</dt>
            <dd>
              {principal.networkPenaltyM === null
                ? '—'
                : `${inlineMetres(principal.networkPenaltyM)} further`}
              {principal.ratio !== null && ` (${ratio(principal.ratio)}×)`}
            </dd>
            <dt>Confidence</dt>
            <dd>
              {m.confidence}
              {m.evidence.length > 0 && ` — ${m.evidence.join(', ')}`}
            </dd>
          </dl>
          {!movements.exhaustive && (
            <div className="notice notice--warning" role="status">
              <div className="notice-title">
                This search did not evaluate every crossing
              </div>
              <p>
                {count(movements.omittedPairCount)} candidate pair
                {movements.omittedPairCount === 1 ? ' was' : 's were'} left
                unevaluated
                {movements.closureComponents > 1 &&
                  `, including every pair spanning the ${count(
                    movements.closureComponents,
                  )} disconnected pieces of this closure`}
                . The figures above describe what WAS evaluated. An unevaluated
                pair could hold a longer detour, or the only movement with no
                replacement at all, so nothing here is offered as the whole
                picture.
              </p>
            </div>
          )}
          <TurnCheck path={principal} />
        </>
      )}

      <Corridor corridor={analysis.corridor} />
      <RouteGeometry geometry={analysis.geometry?.replacement} />
      <Isolation analysis={analysis} />
      <Flags analysis={analysis} />

      <details>
        <summary>Runtime by stage</summary>
        <dl className="kv">
          {Object.entries(analysis.stageMs).map(([k, v]) => (
            <div key={k}>
              <dt>{k.replace(/_/g, ' ')}</dt>
              <dd>{v} ms</dd>
            </div>
          ))}
        </dl>
      </details>
    </section>
  );
}

function portLabel(p: V2CorridorPort | undefined): string {
  if (!p) return 'an unidentified point';
  const name = p.roadName ?? p.routeDesignation ?? `node ${p.node}`;
  return `${name} (${inlineMetres(p.outwardDistanceM)} out${
    p.isDecisionPoint ? ', a junction' : ', a through point'
  })`;
}

function Corridor({ corridor }: { corridor: V2Corridor | null }) {
  if (!corridor) return null;
  if (!corridor.chosenPair) {
    return (
      <>
        <h4>Corridor</h4>
        <p className="muted">{corridor.detail}</p>
      </>
    );
  }
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
      {/* The rule that chose it, in the engine's own sentence. A panel that
        * summarised it would be a second implementation of the rule. */}
      <p className="muted">{corridor.explanation}</p>
      {corridor.admissibilityLevel === 'all_candidates' && (
        <p className="muted">
          No pair of junctions either side had a replacement route between them,
          so these are through points rather than places a driver can turn.
        </p>
      )}
      {corridor.truncated && (
        <p className="muted">{corridor.truncationDetail}</p>
      )}
    </>
  );
}

/*
 * What may be drawn, and what may not.
 *
 * A gapped route is reported as gapped. The map draws the contiguous pieces
 * and nothing between them, and the reveal animation is disabled, because an
 * animation that sweeps along a line asserts the line is unbroken.
 */
function RouteGeometry({ geometry }: { geometry?: V2RouteGeometry }) {
  if (!geometry) return null;
  if (geometry.continuous) {
    return (
      <p className="muted">
        Replacement route geometry: one continuous line,{' '}
        {km(geometry.totalDrawnLengthM)} drawn.
      </p>
    );
  }
  return (
    <div className="notice notice--warning" role="status">
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

/*
 * Physical isolation, in its own block, in its own words.
 *
 * This is the strongest claim the system makes and V1's central defect was
 * letting a routing failure produce it. It is computed on the undirected
 * graph, it does not depend on any route search above, and a timeout in the
 * routing cannot change it.
 */
function Isolation({ analysis }: { analysis: V2BoundaryAnalysis }) {
  const iso = analysis.isolation;
  if (!iso) return null;
  return (
    <>
      <h4>Physical isolation &mdash; a separate question</h4>
      <p>
        {iso.physicallyIsolates && iso.separatedLinkCount > 0
          ? `${count(iso.separatedLinkCount)} link${
              iso.separatedLinkCount === 1 ? '' : 's'
            } (${km(iso.separatedLengthM)}) would be separated in the represented physical-access graph.`
          : 'Nothing is separated in the represented physical-access graph.'}
      </p>
      <p className="muted">
        Undirected and independent of the routing above: a trip having no
        replacement is not the same as a road losing access, and this is the
        one that says whether anything is cut off. Topology confidence:{' '}
        {iso.topologyConfidence}.
      </p>
    </>
  );
}

function Flags({ analysis }: { analysis: V2BoundaryAnalysis }) {
  const flags = [
    ...analysis.qualityFlags,
    ...(analysis.principal?.qualityFlags ?? []),
  ];
  if (flags.length === 0) return null;
  return (
    <>
      <h4>Quality flags</h4>
      <ul className="flag-list">
        {[...new Set(flags)].map((f) => (
          <li key={f}>{f.replace(/_/g, ' ').toLowerCase()}</li>
        ))}
      </ul>
    </>
  );
}

/*
 * Banned manoeuvres.
 *
 * The multi-target searches run on the plain arc graph, which knows nothing
 * about turns, so this is a post-route check. A route that uses a prohibited
 * turn is not offered as the detour — but the reader still has to be told that
 * NO route from this system is road-legal, which is a different and larger
 * caveat than any one restriction.
 */
function TurnCheck({ path }: { path: V2ReplacementPath | null }) {
  const tc = path?.turnCheck;
  if (!tc || !tc.checked) return null;
  if (tc.ok) {
    return (
      <p className="muted">
        No banned manoeuvre on this route ({count(tc.applicableRestrictions)}{' '}
        published restriction
        {tc.applicableRestrictions === 1 ? '' : 's'} apply to this vehicle
        nationally). Banned-turn coverage in the source data is negligible, so
        this is not a claim that the route is road-legal.
      </p>
    );
  }
  return (
    <div className="notice notice--warning" role="status">
      <div className="notice-title">This route uses a prohibited turn</div>
      <p>
        {count(tc.violationCount)} banned manoeuvre
        {tc.violationCount === 1 ? '' : 's'} on the route, so it is not offered
        as the replacement. That is not the same as there being no way round:
        this engine did not find a legal one.
      </p>
    </div>
  );
}
