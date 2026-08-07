/*
 * About this analysis, and what it does not claim.
 *
 * The info button used to open the scenario controls, which is not what it
 * said it did. This is the surface it promised: what the tool computes, what
 * the numbers mean, and every limitation that applies to whatever is currently
 * on screen — read from the live snapshot rather than written into the copy, so
 * it cannot go stale when the backend changes.
 *
 * A native <dialog> so focus trapping, Escape and inertness of the rest of the
 * page come from the platform rather than being reimplemented approximately.
 */

import { useEffect, useRef } from 'react';

import { CloseIcon } from './icons.js';
import { timestamp } from '../lib/format.js';
import type { NetworkMetadata } from '../api/types.js';

export default function AboutDialog({
  open,
  onClose,
  meta,
}: {
  open: boolean;
  onClose: () => void;
  meta: NetworkMetadata | null;
}) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      className="about paper"
      aria-labelledby="about-title"
      onClose={onClose}
      onClick={(e) => {
        /* Click the backdrop to dismiss. The dialog element itself fills only
         * its own box, so a click whose target IS the dialog is the backdrop. */
        if (e.target === ref.current) onClose();
      }}
    >
      <div className="about-head">
        <h2 id="about-title">About this analysis</h2>
        <button
          type="button"
          className="insp-close"
          onClick={onClose}
          aria-label="Close"
        >
          <CloseIcon size={12} />
        </button>
      </div>

      {/* Focusable so it can be scrolled from the keyboard. */}
      <div className="about-body" tabIndex={0}>
        <section>
          <h3>What it computes</h3>
          <p>
            You select a road link. The tool removes it from the routable graph
            and finds the shortest remaining path between the two ends of what
            it removed. The headline figure is how much further that path is
            than the link itself.
          </p>
        </section>

        <section>
          <h3>What it does not compute</h3>
          <p>
            <b>This is not traffic assignment.</b> Nothing in the pipeline knows
            how many vehicles use a road, where their trips start or end, how
            they would redistribute after a closure, or how congested an
            alternative becomes. No figure here counts vehicles, and none should
            be read as though it did.
          </p>
          <p>
            It is a structural measure of the network: what the road layout
            still offers once something is taken out of it.
          </p>
        </section>

        <section>
          <h3>What a closure means here</h3>
          <p>
            Every closure is <b>modelled</b> — posited by you, not observed. The
            tool has no connection to live road status, incidents or works.
          </p>
          <p>
            The engine currently removes every graph link derived from one AMDS
            source feature, which is not necessarily a whole physical road.
            Segment-level closure is not yet implemented.
          </p>
        </section>

        <section>
          <h3>Travel times are estimated</h3>
          <p>
            AMDS publishes no speed attribute, so every duration is derived from
            an estimated speed for the road class. Distances are measured from
            NZTM geometry and are not estimates.
          </p>
        </section>

        {meta && (
          <section>
            <h3>This snapshot</h3>
            <dl className="kv">
              <div style={{ display: 'contents' }}>
                <dt>Snapshot</dt>
                <dd>{meta.snapshotId}</dd>
              </div>
              <div style={{ display: 'contents' }}>
                <dt>Source</dt>
                <dd>{meta.sourceDataset}</dd>
              </div>
              <div style={{ display: 'contents' }}>
                <dt>Retrieved</dt>
                <dd>{timestamp(meta.retrievedAtUtc)}</dd>
              </div>
              <div style={{ display: 'contents' }}>
                <dt>Coverage</dt>
                <dd>
                  {meta.clippedExtract ? 'Clipped extract' : 'Full extract'}
                </dd>
              </div>
              <div style={{ display: 'contents' }}>
                <dt>Links / arcs</dt>
                <dd>
                  {meta.graph.links.toLocaleString('en-NZ')} /{' '}
                  {meta.graph.arcs.toLocaleString('en-NZ')}
                </dd>
              </div>
              {meta.capabilities && (
                <div style={{ display: 'contents' }}>
                  <dt>Algorithm</dt>
                  <dd>{meta.capabilities.algorithmVersion}</dd>
                </div>
              )}
            </dl>
          </section>
        )}

        {/*
          * Naming coverage, stated rather than left to be inferred from how
          * many labels happen to be visible. The withheld figure is the one
          * that matters: those roads have a name, it is simply not one this
          * application is licensed to print yet, and reporting them as
          * unnamed would be the same overclaim in the other direction.
          */}
        {meta?.naming && (
          <section>
            <h3>Road names</h3>
            <dl className="kv">
              <div style={{ display: 'contents' }}>
                <dt>Named links</dt>
                <dd>
                  {meta.naming.namedLinks.toLocaleString('en-NZ')} of{' '}
                  {meta.naming.graphLinks.toLocaleString('en-NZ')} (
                  {Math.round(
                    (100 * meta.naming.namedLinks) / meta.naming.graphLinks,
                  )}
                  %)
                </dd>
              </div>
              {Object.entries(meta.naming.byStatus)
                .sort((a, b) => b[1] - a[1])
                .map(([status, n]) => (
                  <div key={status} style={{ display: 'contents' }}>
                    <dt>{status.replaceAll('_', ' ')}</dt>
                    <dd>{n.toLocaleString('en-NZ')}</dd>
                  </div>
                ))}
              {meta.naming.withheldTotal > 0 && (
                <div style={{ display: 'contents' }}>
                  <dt>name known, not shown</dt>
                  <dd>
                    {meta.naming.withheldTotal.toLocaleString('en-NZ')} — source
                    licence unconfirmed
                  </dd>
                </div>
              )}
            </dl>
          </section>
        )}

        {meta && meta.limitations.length > 0 && (
          <section>
            <h3>Recorded limitations</h3>
            <ul>
              {meta.limitations.map((l) => (
                <li key={l}>{l}</li>
              ))}
            </ul>
          </section>
        )}

        {meta && (
          <p className="about-attrib">
            {meta.attribution} {meta.licence}
          </p>
        )}

        {/* Every source whose names are displayed, attributed as its licence
          * requires. Read from the response, so a source cannot start
          * appearing on the map without its attribution appearing here. */}
        {meta?.nameAttributions?.map((a) => (
          <p className="about-attrib" key={a.source}>
            {a.attribution} {a.licence}
          </p>
        ))}
      </div>
    </dialog>
  );
}
