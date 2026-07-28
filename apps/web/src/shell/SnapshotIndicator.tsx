/*
 * Which snapshot the figures on screen came from, and what it covers.
 *
 * Permanently visible, because every number in the inspector is only meaningful
 * relative to a snapshot — and because a national tool serving a regional
 * extract must never be able to look national.
 *
 * The coverage name comes from the backend, which records it at ingest. This
 * component used to translate `clippedExtract === true` into the literal string
 * "Wellington pilot", which would have announced an Auckland or custom extract
 * under Wellington's name.
 */

import { coverageOf } from '../api/coverage.js';
import type { NetworkMetadata } from '../api/types.js';

export default function SnapshotIndicator({
  meta,
}: {
  meta: NetworkMetadata | null;
}) {
  if (!meta) {
    return (
      <div className="snapshot" title="Loading snapshot metadata">
        <span className="dot" data-state="unknown" />
        <span>Loading snapshot&hellip;</span>
      </div>
    );
  }

  const coverage = coverageOf(meta);
  const short = meta.snapshotId.split('-').pop() ?? meta.snapshotId;

  return (
    <div
      className="snapshot"
      data-coverage={coverage.kind}
      title={[
        coverage.caveat,
        meta.snapshotId,
        meta.sourceDataset,
        `Retrieved ${meta.retrievedAtUtc}`,
        `${meta.graph.links.toLocaleString('en-NZ')} links, ` +
          `${meta.graph.arcs.toLocaleString('en-NZ')} arcs`,
        meta.selectionReason ? `Selected: ${meta.selectionReason}` : null,
      ]
        .filter(Boolean)
        .join('\n')}
    >
      <span className="dot" data-state={dotState(coverage.kind)} />
      <span>{coverage.name} ·&nbsp;</span>
      <span className="sid">{short}</span>
    </div>
  );
}

/**
 * Green for national, amber for anything less.
 *
 * Amber is the product's "this is context, not the answer" colour, and a
 * regional extract is exactly that: usable, but not what the tool is for.
 */
function dotState(kind: string): string {
  if (kind === 'national') return 'ok';
  if (kind === 'unknown') return 'unknown';
  return 'degraded';
}
