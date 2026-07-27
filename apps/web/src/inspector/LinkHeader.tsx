/*
 * Road name first.
 *
 * The previous build led with the AMDS id, the internal link id and the RCA
 * metadata, and put the result below the fold. Identifiers are how the system
 * refers to a road; the name is how a person does. The identifiers are still
 * available — four levels down, under Source and methodology.
 */

import { CloseIcon } from '../shell/icons.js';

export default function LinkHeader({
  roadName,
  onClear,
}: {
  roadName: string | null;
  onClear: () => void;
}) {
  return (
    <div className="insp-head">
      <div className="eyebrow">Closure result</div>
      <h2 id="result">{roadName || '(unnamed link)'}</h2>
      <button
        type="button"
        className="insp-close"
        aria-label="Clear the selected road"
        title="Clear selection"
        onClick={onClear}
      >
        <CloseIcon size={12} />
      </button>
    </div>
  );
}
