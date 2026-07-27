/**
 * Result panel.
 *
 * Design rule: no number appears without the qualifier that belongs to it.
 * A DISCONNECTED result is explained rather than left as a blank, an estimated
 * travel time is labelled estimated, and the structural-vs-traffic distinction
 * is stated on screen rather than buried in documentation.
 */

import type {
  DetourResponse,
  DirectionResult,
  NetworkMetadata,
} from './api.js';

const km = (m: number | null | undefined) =>
  m === null || m === undefined ? '—' : `${(m / 1000).toFixed(2)} km`;
const metres = (m: number | null | undefined) =>
  m === null || m === undefined ? '—' : `${Math.round(m).toLocaleString()} m`;
const mins = (s: number | null | undefined) =>
  s === null || s === undefined ? '—' : `${(s / 60).toFixed(1)} min`;
const ratio = (r: number | null | undefined) =>
  r === null || r === undefined ? '—' : `${r.toFixed(2)}×`;

const WARN_FLAGS = new Set([
  'TIME_ESTIMATED',
  'SPEED_ESTIMATED',
  'DISCONNECTED_UNVERIFIED_OUTSIDE_EXTRACT',
  'ROUTE_USES_BUFFER',
  'ISOLATES_SIGNIFICANT_AREA',
  'SOLE_ACCESS',
  'ONEWAY_UNSET_ASSUMED_TWO_WAY',
  'MULTIPART_GEOMETRY_FIRST_PATH_USED',
]);

export default function ResultPanel({
  detour,
  meta,
  loading,
  error,
}: {
  detour: DetourResponse | null;
  meta: NetworkMetadata | null;
  loading: boolean;
  error: string | null;
}) {
  if (error) {
    return (
      <div className="card">
        <h2>Request failed</h2>
        <div className="note">
          {error}
          <br />
          <br />
          This is an application or transport error. It is <strong>not</strong> a
          finding about the road network &mdash; no conclusion should be drawn
          about whether a detour exists.
        </div>
      </div>
    );
  }

  if (!detour) {
    return loading ? <div className="card">Calculating&hellip;</div> : null;
  }

  const link = detour.selectedLink;

  return (
    <>
      <div className="card">
        <h2>Selected link</h2>
        <table>
          <tbody>
            <Row k="Road name" v={link.roadName ?? '(unnamed)'} />
            <Row k="AMDS id" v={<code style={{ fontSize: 10 }}>{link.amdsId}</code>} />
            <Row k="Internal link id" v={String(link.linkId)} />
            <Row k="Controlling authority" v={link.rca ?? `code ${link.assetOwnerOrganisation ?? '?'}`} />
            <Row k="Classification" v={link.modelAssetTypeName ?? '—'} />
            <Row k="Surface" v={link.surfaceTypeName ?? '—'} />
            <Row k="Length" v={metres(link.lengthM)} />
            <Row k="Direction of travel" v={link.oneway ? 'One-way' : 'Two-way'} />
            <Row k="Lifeline route" v={link.lifeLineRoute ? 'Yes' : 'No'} />
            <Row
              k="Assumed speed"
              v={`${link.speedKph ?? '—'} km/h (${link.speedSource.replace(/_/g, ' ')})`}
            />
          </tbody>
        </table>
        {link.qualityFlags.length > 0 && <Flags flags={link.qualityFlags} />}
      </div>

      <div className="card">
        <h2>Closure applied</h2>
        <table>
          <tbody>
            <Row
              k="Scope"
              v={detour.closure.scope === 'physical'
                ? 'All pieces of one AMDS source feature'
                : 'Single direction'}
            />
            <Row k="Links removed" v={String(detour.closure.removedLinkCount)} />
            <Row k="Directed arcs removed" v={String(detour.closure.removedArcCount)} />
          </tbody>
        </table>
        <div className="note">
          {detour.closure.scope === 'physical'
            ? 'Every graph link derived from this AMDS source feature is removed, in both directions. AMDS publishes no road-asset or paired-carriageway grouping, so this is a source-feature closure, not necessarily a whole physical road.'
            : 'Only the arc travelling in the direction under test is removed; the opposite direction stays open.'}
        </div>
      </div>

      {detour.forward && <DirectionCard d={detour.forward} />}
      {detour.reverse && <DirectionCard d={detour.reverse} />}

      <div className="card">
        <h2>Provenance</h2>
        <table>
          <tbody>
            <Row k="Snapshot" v={<code style={{ fontSize: 10 }}>{detour.snapshotId}</code>} />
            <Row k="Source" v={detour.sourceDataset} />
            <Row k="Retrieved" v={new Date(detour.retrievedAtUtc).toISOString().slice(0, 16).replace('T', ' ') + ' UTC'} />
            <Row k="Algorithm" v={`${detour.algorithm} v${detour.algorithmVersion}`} />
            <Row k="Result" v={detour.cached ? 'From cache' : 'Computed now'} />
          </tbody>
        </table>
        <div className="note">{detour.attribution}</div>
        <div style={{ marginTop: 10 }}>
          <button onClick={() => navigator.clipboard?.writeText(window.location.href)}>
            Copy permalink
          </button>{' '}
          <button
            onClick={() => {
              const blob = new Blob([JSON.stringify(detour, null, 2)], {
                type: 'application/json',
              });
              const a = document.createElement('a');
              a.href = URL.createObjectURL(blob);
              a.download = `detour-${link.amdsId.replace(/[{}]/g, '')}.json`;
              a.click();
            }}
          >
            Download result
          </button>
        </div>
      </div>

      <details className="card">
        <summary>Known limitations that apply to these numbers</summary>
        <ul className="limitations" style={{ marginTop: 10 }}>
          {detour.limitations.map((l) => (
            <li key={l}>{l}</li>
          ))}
        </ul>
        {meta && (
          <div className="note">
            Graph: {meta.graph.links.toLocaleString()} links,{' '}
            {meta.graph.nodes.toLocaleString()} nodes, {meta.graph.components}{' '}
            connected components, {meta.graph.turnRestrictions} turn restrictions.
          </div>
        )}
      </details>
    </>
  );
}

function DirectionCard({ d }: { d: DirectionResult }) {
  const ok = d.status === 'OK';
  const disc = d.status === 'DISCONNECTED';
  const m = d.metrics;

  return (
    <div className="card">
      <h2>
        <span className="badge dir">{d.direction}</span>{' '}
        <span className={`badge ${ok ? 'ok' : disc ? 'disc' : 'err'}`}>{d.status}</span>
      </h2>

      {ok && (
        <table>
          <tbody>
            <Row k="Closed link length" v={metres(m.selectedLinkLengthM)} />
            <Row k="Normal shortest path" v={km(m.normalPathDistanceM)} />
            <Row k="Replacement path" v={km(m.alternativeDistanceM)} strong />
            <Row k="Added vs closed link" v={metres(m.addedDistanceVsLinkM)} />
            <Row k="Network penalty" v={metres(m.networkPenaltyM)} strong />
            <Row k="Detour ratio" v={ratio(m.detourRatioVsLink)} />
            <Row k="Normal time (est.)" v={mins(m.normalPathTimeS)} />
            <Row k="Replacement time (est.)" v={mins(m.alternativeTimeS)} />
            <Row k="Added time (est.)" v={mins(m.addedTimeS)} />
          </tbody>
        </table>
      )}

      {!ok && (
        <div className="banner">
          {d.statusMeaning}
          {d.errorDetail && (
            <>
              <br />
              <code style={{ fontSize: 10 }}>{d.errorDetail}</code>
            </>
          )}
        </div>
      )}

      {d.corridor && (
        <>
          <h2 style={{ marginTop: 12 }}>Corridor measure</h2>
          {d.corridor.status === 'OK' ? (
            <table>
              <tbody>
                <Row k="Normal through trip" v={km(d.corridor.normalDistanceM)} />
                <Row k="With closure" v={km(d.corridor.alternativeDistanceM)} strong />
                <Row k="Added distance" v={metres(d.corridor.penaltyM)} strong />
                <Row k="Added time (est.)" v={mins(d.corridor.penaltyTimeS)} />
                <Row
                  k="Measured over"
                  v={`${d.corridor.hopsUpstream} link(s) back, ${d.corridor.hopsDownstream} forward`}
                />
              </tbody>
            </table>
          ) : (
            <div className="note">
              No through route exists either, within {d.corridor.hopsUpstream}{' '}
              link(s) upstream and {d.corridor.hopsDownstream} downstream
              {d.corridor.truncated ? ' (search bounded)' : ''}.
            </div>
          )}
          <div className="note">{d.corridor.meaning}</div>
        </>
      )}

      {d.isolation && d.isolation.side !== 'none' && (
        <>
          <h2 style={{ marginTop: 12 }}>What is cut off</h2>
          <table>
            <tbody>
              <Row
                k="Stranded side"
                v={d.isolation.side === 'downstream' ? 'Beyond the closure' : 'Behind the closure'}
              />
              <Row k="Links stranded" v={d.isolation.pocketLinkCount.toLocaleString()} />
              <Row k="Road length stranded" v={km(d.isolation.pocketLengthM)} />
            </tbody>
          </table>
          <div className="note">
            {d.isolation.pocketLinkCount <= 3
              ? 'A pocket this small is a cul-de-sac or driveway rather than a community.'
              : d.isolation.pocketLinkCount >= 100
                ? 'A substantial area loses its connection under this closure.'
                : 'A small local area loses its connection under this closure.'}
            {!d.isolation.exact && ' The pocket exceeded the search bound, so this is a lower bound.'}
          </div>
        </>
      )}

      {d.qualityFlags.length > 0 && <Flags flags={d.qualityFlags} />}
      <div className="note">Computed in {d.runtimeMs} ms.</div>
    </div>
  );
}

function Row({
  k,
  v,
  strong,
}: {
  k: string;
  v: React.ReactNode;
  strong?: boolean;
}) {
  return (
    <tr>
      <td className="k">{k}</td>
      <td className="v" style={strong ? { fontWeight: 700 } : undefined}>
        {v}
      </td>
    </tr>
  );
}

function Flags({ flags }: { flags: string[] }) {
  return (
    <div className="flags">
      {flags.map((f) => (
        <span key={f} className={`flag ${WARN_FLAGS.has(f) ? 'warn' : ''}`}>
          {f}
        </span>
      ))}
    </div>
  );
}
