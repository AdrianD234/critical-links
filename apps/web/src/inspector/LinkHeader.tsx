/*
 * Road name first.
 *
 * The previous build led with the AMDS id, the internal link id and the RCA
 * metadata, and put the result below the fold. Identifiers are how the system
 * refers to a road; the name is how a person does. The identifiers are still
 * available — four levels down, under Source and methodology.
 *
 * Where there is no name, the heading says which KIND of no-name this is.
 * "(unnamed link)" collapsed four different situations into one phrase: a road
 * that genuinely has no name, a road whose name we could not find, a road two
 * sources disagree about, and a road we do have a name for but are not licensed
 * to print. Those call for different actions from a reader, so they read
 * differently.
 */

import type { Naming } from '../api/types.js';
import { CloseIcon } from '../shell/icons.js';

/** Fallback wording. The API sends its own label; this is for older responses. */
const FALLBACK_LABEL: Record<string, string> = {
  officially_unnamed: 'Unnamed road',
  ambiguous_conflict: 'Name disputed',
  unresolved: 'Name not recorded',
};

export default function LinkHeader({
  roadName,
  naming,
  onClear,
}: {
  roadName: string | null;
  naming?: Naming;
  onClear: () => void;
}) {
  const status = naming?.status ?? (roadName ? 'amds_named' : 'unresolved');
  const heading =
    roadName || naming?.label || FALLBACK_LABEL[status] || 'Name not recorded';
  const designation = naming?.routeDesignation;
  // Never repeat the designation under a heading that already is it.
  const showDesignation = Boolean(designation) && designation !== roadName;

  return (
    <div className="insp-head">
      <div className="eyebrow">Closure result</div>
      <h2 id="result" className={roadName ? undefined : 'insp-noname'}>
        {heading}
      </h2>
      {(showDesignation || naming?.conflict || naming?.withheldSource) && (
        <div className="insp-naming">
          {showDesignation && <span className="insp-desig">{designation}</span>}
          {naming?.conflict && (
            <span className="insp-name-flag" title={naming.explanation ?? undefined}>
              sources disagree
            </span>
          )}
          {naming?.withheldSource && (
            <span
              className="insp-name-flag"
              title={
                'A name for this road was matched from ' +
                naming.withheldSource +
                ', but that source’s licence has not been confirmed, so it is ' +
                'not displayed.'
              }
            >
              name withheld pending licence
            </span>
          )}
        </div>
      )}
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
