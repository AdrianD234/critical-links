/*
 * The answer.
 *
 * Added distance, at 58px, in neutral charcoal — deliberately not closure red.
 * Red is the closure; the measurement is not the alarm. 53.5 km is severe for a
 * commuter and unremarkable for a freight operator with a schedule allowance,
 * and the analysis does not know which is reading it.
 *
 * When the result is DISCONNECTED there is no added distance to show, so the
 * hero switches to what is cut off. That is the honest headline for that case:
 * the question "how much further" has no answer, and inventing one — an
 * infinity, a dash, a zero — would be worse than answering the question the
 * result actually settles.
 *
 * The value is keyed so that a change replays the 140ms reveal. There is no
 * count-up: a number that spins through wrong values on the way to the right
 * one is a stale number with a motion curve attached.
 */

import { inlineMetres } from '../lib/format.js';

export function HeroSkeleton() {
  return (
    <div className="headline">
      <div className="lab">Added distance</div>
      <div className="val" aria-hidden="true">
        <span className="skeleton" style={{ height: '0.72em', minWidth: '2.6em' }} />
      </div>
      <p className="sub">Calculating the replacement path&hellip;</p>
    </div>
  );
}

export default function HeroMetric({
  label,
  value,
  unit,
  revealKey,
  detail,
}: {
  label: string;
  value: string;
  unit: string;
  revealKey: string;
  detail: React.ReactNode;
}) {
  return (
    <div className="headline">
      <div className="lab">{label}</div>
      <div className="val reveal tnum" key={revealKey}>
        {value}
        <span className="unit">{unit}</span>
      </div>
      <p className="sub">{detail}</p>
    </div>
  );
}

/** The prose under the hero for a successful result. */
export function detourDetail({
  selectedLengthM,
  alternativeM,
}: {
  selectedLengthM: number | null;
  alternativeM: number | null;
}) {
  const alt = alternativeM === null ? null : (alternativeM / 1000).toFixed(2);
  if (alt === null) {
    return <>Traffic that used this {inlineMetres(selectedLengthM)} link must take another path.</>;
  }
  return (
    <>
      Traffic that used this {inlineMetres(selectedLengthM)} link must travel{' '}
      {alt} km instead.
    </>
  );
}
