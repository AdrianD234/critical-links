/*
 * What the figures on screen are, and what they are not.
 *
 * These statements are not decoration and are not negotiable while the model
 * remains pre-hardening. Each one exists because without it a reader would
 * reasonably draw a conclusion the analysis does not support:
 *
 *   - the closure removes a whole AMDS source feature, which is not
 *     necessarily a whole physical road, so the closed extent may be longer or
 *     shorter than the road a reader has in mind;
 *   - added time rests on estimated speeds, because AMDS publishes none;
 *   - the snapshot is a clipped extract, so a replacement path that would
 *     leave the extract cannot be found and the link may look more critical
 *     than it is;
 *   - none of this is traffic assignment. No figure here says how many
 *     vehicles are affected, because nothing in the pipeline knows.
 *
 * The scope line reads from the *response*, not from the control, so it always
 * describes what was actually computed rather than what was requested.
 */

import type { DetourResponse, NetworkMetadata } from '../api/types.js';

export default function ConfidenceNotice({
  detour,
  meta,
}: {
  detour: DetourResponse;
  meta: NetworkMetadata | null;
}) {
  const removed = detour.closure.removedLinkCount;
  const clipped = detour.clippedExtract || meta?.clippedExtract;

  return (
    <>
      <div className="scope">
        Closing <b>{scopeLabel(detour.closure.scope)}</b> — {removed}{' '}
        {removed === 1 ? 'link' : 'links'} removed from the graph
        {detour.closure.removedArcCount
          ? `, ${detour.closure.removedArcCount} directed arcs`
          : ''}
        . This is what the engine removes; it is not necessarily a whole
        physical road.
      </div>

      <div className="confidence">
        <b>Structural analysis, not a traffic forecast.</b> The replacement path
        is the shortest route the represented network still offers. It does not
        say how many vehicles are affected or how congested the alternative
        becomes.
        {clipped && (
          <>
            {' '}
            This snapshot is a clipped extract, so a replacement path that would
            leave the extract cannot be found.
          </>
        )}
      </div>
    </>
  );
}

function scopeLabel(wire: string): string {
  return wire === 'directed' ? 'one direction of travel' : 'one AMDS source feature';
}
