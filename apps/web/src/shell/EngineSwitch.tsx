/*
 * The development-only engine switch.
 *
 * Two closure engines exist in this repository at once: V1, which removes every
 * graph segment derived from one AMDS source feature and reports isolation from
 * a bounded directed walk, and V2, which closes the selected segment and
 * derives isolation exactly. They answer different questions and return
 * different numbers for the same road, which is precisely why both are kept —
 * one cross-validates the other.
 *
 * V1 IS THE DEFAULT AND STAYS THE DEFAULT. V2 is a development preview: its
 * algorithm version is a `-dev` string, and a figure from it must never reach
 * someone who did not deliberately ask for it.
 *
 * `import.meta.env.DEV` is a literal Vite replaces at build time, so a
 * production bundle contains `false` here and the control, its label and its
 * state are all eliminated by dead-code removal rather than merely hidden.
 */

export type Engine = 'v1' | 'v2';

/** True only in a development build. Every V2 call sites gates on this. */
export const ENGINE_SWITCH_VISIBLE = import.meta.env.DEV;

const OPTIONS: { value: Engine; label: string; title: string }[] = [
  {
    value: 'v1',
    label: 'V1 legacy',
    title:
      'The shipped engine. Removes every graph segment derived from one AMDS ' +
      'source feature.',
  },
  {
    value: 'v2',
    label: 'V2 closure analysis',
    title:
      'Development preview. Closes the selected segment and derives physical ' +
      'isolation exactly. Not a published contract.',
  },
];

export default function EngineSwitch({
  engine,
  onChange,
}: {
  engine: Engine;
  onChange: (e: Engine) => void;
}) {
  if (!ENGINE_SWITCH_VISIBLE) return null;

  return (
    <div
      className="engine-switch"
      role="group"
      aria-label="Analysis engine — development only"
    >
      <span className="engine-tag">dev</span>
      {OPTIONS.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={engine === o.value}
          title={o.title}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
