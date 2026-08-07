/*
 * Provenance: identifiers, snapshot, algorithm, licence.
 *
 * Deepest level of disclosure, and every value read from the response rather
 * than written into the component. That is deliberate and load-bearing: when
 * turn-restriction semantics and segment scope land, the algorithm version
 * changes, and a version hard-coded here would keep asserting the old one
 * beside figures computed by the new engine.
 *
 * Identifiers and versions are the canonical case for monospace — they are
 * strings to be compared character by character, not read as language.
 */

import { timestamp } from '../lib/format.js';
import type { DetourResponse, NetworkMetadata } from '../api/types.js';

export default function SourceMethodology({
  detour,
  meta,
}: {
  detour: DetourResponse;
  meta: NetworkMetadata | null;
}) {
  const naming = detour.selectedLink.naming;
  const rows: [string, string][] = [
    ['AMDS id', detour.selectedLink.amdsId],
    ['Source OBJECTID', String(detour.selectedLink.sourceObjectId)],
    ['Internal link id', String(detour.selectedLink.linkId)],
    ['Closure group', detour.closure.closureGroupId],
    ['Snapshot', detour.snapshotId],
    ['Source dataset', detour.sourceDataset],
    ['Retrieved', timestamp(detour.retrievedAtUtc)],
    ['Calculated', timestamp(detour.calculatedAtUtc)],
    ['Algorithm', `${detour.algorithm} ${detour.algorithmVersion}`],
    ...(meta?.capabilities
      ? ([['Processing version', meta.capabilities.processingVersion]] as [
          string,
          string,
        ][])
      : []),
    ['Licence', detour.licence],
    /*
     * Where the road's NAME came from, which is not always where its geometry
     * came from. AMDS carries a name for about a third of links; the rest are
     * matched from an external source or have none, and a reader comparing
     * this label against another map deserves to know which.
     */
    ...(naming
      ? ([
          ['Name state', naming.status],
          ['Name source', naming.source ?? 'none'],
          ...(naming.confidence
            ? ([['Name match confidence', naming.confidence]] as [string, string][])
            : []),
          ...(naming.withheldSource
            ? ([
                [
                  'Name withheld',
                  `matched from ${naming.withheldSource}; licence unconfirmed`,
                ],
              ] as [string, string][])
            : []),
        ] as [string, string][])
      : []),
  ];

  return (
    <>
      {naming?.explanation && (
        <p
          style={{
            margin: '0 0 12px',
            fontSize: 'var(--text-meta)',
            color: 'var(--panel-muted)',
          }}
        >
          {naming.explanation}
        </p>
      )}

      <dl className="kv">
        {rows.map(([k, v]) => (
          <div key={k} style={{ display: 'contents' }}>
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>

      {detour.limitations.length > 0 && (
        <ul
          style={{
            margin: '12px 0 0',
            paddingLeft: 16,
            fontSize: 'var(--text-meta)',
            color: 'var(--panel-muted)',
            lineHeight: 'var(--leading-relaxed)',
          }}
        >
          {detour.limitations.map((l) => (
            <li key={l}>{l}</li>
          ))}
        </ul>
      )}

      <p
        style={{
          marginTop: 12,
          fontSize: 'var(--text-meta)',
          color: 'var(--panel-muted)',
          lineHeight: 'var(--leading-relaxed)',
        }}
      >
        {detour.attribution}
      </p>
    </>
  );
}
