/*
 * Road name first.
 *
 * The previous build led with the AMDS id, the internal link id and the RCA
 * metadata, and put the result below the fold. Identifiers are how the system
 * refers to a road; the name is how a person does. The identifiers are still
 * available — four levels down, under Source and methodology.
 *
 * Where there is no name, the heading says what CAN be said. "(unnamed link)"
 * collapsed four situations into one phrase: a road that genuinely has no name,
 * a road whose name we could not find, a road two sources disagree about, and a
 * road we do have a name for but are not licensed to print. Those call for
 * different actions from a reader, so they read differently — and a road with
 * none of them, but a class, a locality and a managing authority, is described
 * from those rather than left blank.
 *
 * The backend decides the heading and sends it as `displayLabel`. This renders
 * it and never rebuilds it; naming.ts covers the older responses that carry a
 * status but no label. "Name not recorded" survives as provenance below the
 * heading, which is where it belongs — it says what the pipeline found, not
 * what the road is.
 */

import type { LinkSummary } from '../api/types.js';
import { linkDisplayLabel } from '../naming.js';
import { CloseIcon } from '../shell/icons.js';

export default function LinkHeader({
  link,
  pendingName,
  onClear,
}: {
  link: LinkSummary | null;
  /** Known from the click, before the result lands. */
  pendingName: string | null;
  onClear: () => void;
}) {
  const naming = link?.naming;
  const roadName = link?.roadName ?? pendingName;
  const heading = linkDisplayLabel({
    displayLabel: link?.displayLabel,
    roadName,
    naming,
    locality: link?.locality,
    rca: link?.rca,
  });
  /* A heading that came from a name is the road's own; anything else is
   * derived, and the muted treatment says so without a second sentence. */
  const named = Boolean(roadName) || link?.displayLabelKind === 'road_name';

  const designation = naming?.routeDesignation;
  // Never repeat the designation under a heading that already is it.
  const showDesignation = Boolean(designation) && designation !== heading;
  const secondary = link?.displayLabelSecondary;
  const basis = link?.displayLabelBasis;

  return (
    <div className="insp-head">
      <div className="eyebrow">Closure result</div>
      <h2 id="result" className={named ? undefined : 'insp-noname'}>
        {heading}
      </h2>
      {(showDesignation ||
        secondary ||
        naming?.conflict ||
        naming?.withheldSource) && (
        <div className="insp-naming">
          {showDesignation && <span className="insp-desig">{designation}</span>}
          {/* The stable id, so two roads that both read "State-highway
            * section near Tokoroa" are still tellable apart. */}
          {secondary && !named && (
            <span className="insp-name-flag" title={basis || undefined}>
              {secondary}
            </span>
          )}
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
