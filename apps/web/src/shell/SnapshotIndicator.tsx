/*
 * Which snapshot the figures on screen came from.
 *
 * This is permanently visible rather than buried under methodology because
 * every number in the inspector is only meaningful relative to a snapshot, and
 * the current one is an explicitly pre-hardening pilot. The short hash is the
 * last segment of the snapshot id, shown in monospace because it is an
 * identifier.
 */

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

  const short = meta.snapshotId.split('-').pop() ?? meta.snapshotId;
  const clipped = meta.clippedExtract;

  return (
    <div
      className="snapshot"
      title={[
        meta.snapshotId,
        meta.sourceDataset,
        `Retrieved ${meta.retrievedAtUtc}`,
        clipped
          ? 'Clipped extract — not national coverage'
          : 'Full extract',
      ].join('\n')}
    >
      <span className="dot" data-state={clipped ? 'degraded' : 'ok'} />
      <span>{clipped ? 'Wellington pilot' : 'National'} ·&nbsp;</span>
      <span className="sid">{short}</span>
    </div>
  );
}
