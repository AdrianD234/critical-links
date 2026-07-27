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

/**
 * The prose under the hero for a successful result.
 *
 * THIS WORDING IS LOAD-BEARING. It used to read "Traffic that used this link
 * must travel 55.45 km instead", which is a traffic-assignment claim the model
 * cannot support: the engine does not know which traffic used the link, how
 * trips redistribute, or whether affected trips would take this particular
 * replacement route. It computes one shortest path between two nodes.
 *
 * What it actually computed is what it now says — a path, between the selected
 * link's endpoints, under a hypothetical closure.
 */
export function detourDetail({
  alternativeM,
}: {
  alternativeM: number | null;
}) {
  const alt = alternativeM === null ? null : (alternativeM / 1000).toFixed(2);
  if (alt === null) {
    return (
      <>
        With this modelled closure, the shortest represented-network path
        between the selected link&rsquo;s endpoints goes another way.
      </>
    );
  }
  return (
    <>
      With this modelled closure, the shortest represented-network path between
      the selected link&rsquo;s endpoints is {alt} km.
    </>
  );
}
