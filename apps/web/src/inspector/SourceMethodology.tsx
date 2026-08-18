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
import type { LinkSummary, NetworkMetadata } from '../api/types.js';

/**
 * Provenance, stated independently of which response carried it.
 *
 * The fields are named after what they are rather than after the response they
 * were lifted from, because the two engines' responses do not share a shape and
 * a component keyed to one of them would have to be duplicated to serve the
 * other. Duplicating it is how the two copies come to disagree about which
 * algorithm version produced the figures above them, which is the one thing
 * this panel exists to state exactly.
 *
 * Optional fields are omitted from the table rather than rendered empty: a row
 * reading "Retrieved —" invites the reader to wonder what went wrong, when the
 * answer is that this response does not carry that field.
 */
export interface Provenance {
  selectedLink: LinkSummary;
  closureGroupId: string;
  snapshotId: string;
  sourceDataset?: string;
  retrievedAtUtc?: string;
  calculatedAtUtc?: string;
  algorithm: string;
  algorithmVersion: string;
  /** The engine's own sentence about how settled it is. Never paraphrased. */
  stability?: string;
  licence?: string;
  limitations: string[];
  attribution?: string;
}

export default function SourceMethodology({
  provenance,
  meta,
}: {
  provenance: Provenance;
  meta: NetworkMetadata | null;
}) {
  const naming = provenance.selectedLink.naming;
  const optional = (k: string, v: string | undefined): [string, string][] =>
    v ? [[k, v]] : [];
  const rows: [string, string][] = [
    ['AMDS id', provenance.selectedLink.amdsId],
    ['Source OBJECTID', String(provenance.selectedLink.sourceObjectId)],
    ['Internal link id', String(provenance.selectedLink.linkId)],
    ['Closure group', provenance.closureGroupId],
    ['Snapshot', provenance.snapshotId],
    ...optional('Source dataset', provenance.sourceDataset),
    ...optional('Retrieved', provenance.retrievedAtUtc
      ? timestamp(provenance.retrievedAtUtc)
      : undefined),
    ...optional('Calculated', provenance.calculatedAtUtc
      ? timestamp(provenance.calculatedAtUtc)
      : undefined),
    ['Algorithm', `${provenance.algorithm} ${provenance.algorithmVersion}`],
    ...optional('Engine maturity', provenance.stability),
    ...(meta?.capabilities
      ? ([['Processing version', meta.capabilities.processingVersion]] as [
          string,
          string,
        ][])
      : []),
    ...optional('Licence', provenance.licence),
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

      {provenance.limitations.length > 0 && (
        <ul
          style={{
            margin: '12px 0 0',
            paddingLeft: 16,
            fontSize: 'var(--text-meta)',
            color: 'var(--panel-muted)',
            lineHeight: 'var(--leading-relaxed)',
          }}
        >
          {provenance.limitations.map((l) => (
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
        {provenance.attribution}
      </p>
    </>
  );
}
