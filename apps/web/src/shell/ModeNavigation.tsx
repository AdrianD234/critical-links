/*
 * Top-level mode navigation.
 *
 * Network Overview and Data Quality are present but not yet navigable: their
 * backend endpoints and result-classification semantics are not stable, and
 * shipping a screen whose numbers might be reclassified later is worse than
 * showing that it is coming. They are marked `aria-disabled` rather than
 * removed, because hiding them would misrepresent the product's scope.
 */

export type Mode = 'explore' | 'overview' | 'quality';

const MODES: { id: Mode; label: string; ready: boolean; why?: string }[] = [
  { id: 'explore', label: 'Explore', ready: true },
  {
    id: 'overview',
    label: 'Network Overview',
    ready: false,
    why: 'Available once national criticality classification is finalised',
  },
  {
    id: 'quality',
    label: 'Data Quality',
    ready: false,
    why: 'Available once source-quality reporting is finalised',
  },
];

export default function ModeNavigation({
  mode,
  onChange,
}: {
  mode: Mode;
  onChange: (m: Mode) => void;
}) {
  return (
    <nav className="modenav" aria-label="Application sections">
      {MODES.map((m) => (
        <a
          key={m.id}
          href={m.ready ? `#/${m.id}` : undefined}
          aria-current={m.id === mode ? 'page' : undefined}
          aria-disabled={m.ready ? undefined : 'true'}
          title={m.why}
          onClick={(e) => {
            e.preventDefault();
            if (m.ready) onChange(m.id);
          }}
        >
          {m.label}
        </a>
      ))}
    </nav>
  );
}
