/*
 * How a road with no displayed name is described, in one place.
 *
 * "(unnamed link)" was doing four jobs at once. It appeared on a road that
 * genuinely has no name, on a road whose name simply was not found, on a road
 * two sources disagree about, and on a road whose name is known but cannot be
 * shown for licensing reasons. Those are different facts about the world and a
 * reader can act on them differently, so they get different words.
 *
 * "No name" was worse. It was the map chip's answer to all four, and it was
 * also the answer for a state highway that the database can describe perfectly
 * well from its classification, its locality and the authority that manages it.
 * The reported Tokoroa case is exactly that: a road two sources independently
 * identify as State Highway 1, rendered as a blank. The string is gone from
 * this file and nothing here may reintroduce it.
 *
 * The backend now sends the authoritative label as `displayLabel`, and that is
 * preferred whenever it is present. The ladder below is the fallback for the
 * two callers that cannot have it: vector tiles, which carry a name status, a
 * route number and a state-highway flag but no label, and responses from a
 * backend that predates the field. It mirrors the server's priority order
 * rather than inventing a second vocabulary — the words a reader sees must not
 * depend on which code path produced them.
 */

import type { NameStatus } from './api/types.js';

export const NAME_STATE_LABEL: Record<NameStatus, string> = {
  amds_named: '',
  route_designation_only: '',
  externally_enriched: '',
  officially_unnamed: 'Unnamed road',
  ambiguous_conflict: 'Name disputed',
  unresolved: 'Name not recorded',
};

/**
 * A name IS held for this road and is not being shown, because that source's
 * licence has not been confirmed.
 *
 * Distinct from every other state on this list, and the most actionable of
 * them: the fix is a licence check, not a survey.
 */
export const WITHHELD_LABEL = 'Name withheld pending licence';

/** The last resort, when nothing at all is known. */
const UNKNOWN_LABEL = 'Name not recorded';

/**
 * The subset of a link that decides its label.
 *
 * Structural rather than `LinkSummary`, so a vector-tile property bag and a
 * search result can both be passed without either being converted first.
 */
export interface LabelFields {
  /** The backend's authoritative label. Preferred over everything below. */
  displayLabel?: string | null;
  roadName?: string | null;
  /** "SH 1" from a tile, where the naming block is not available. */
  roadNumber?: string | null;
  naming?: {
    status?: string | null;
    routeDesignation?: string | null;
    withheldSource?: string | null;
  } | null;
  /** Set from the tile's `stateHighway` flag, or from an RCA code of 1. */
  stateHighway?: boolean | null;
  locality?: string | null;
  /** The road controlling authority's name, as reported. */
  rca?: string | null;
}

/**
 * What can be said about a road from its class, its locality and its manager.
 *
 * This is the line that kills "No name". A state highway near Kinleith with no
 * matched name is still a state highway near Kinleith, and saying so is both
 * true and more useful than saying nothing.
 */
function contextualLabel(f: LabelFields): string | null {
  if (f.locality) {
    return f.stateHighway
      ? `State-highway section near ${f.locality}`
      : `Local-road section near ${f.locality}`;
  }
  if (f.stateHighway) return 'State-highway section';
  if (f.rca) return `Road section managed by ${f.rca}`;
  return null;
}

/**
 * The one label, from whatever is known. Never empty, and never "No name".
 *
 * The order is the server's: a real name, then the route the road carries,
 * then the states an authority asserts, then a licence hold, then context,
 * then the bare state. `unresolved` deliberately falls all the way through —
 * "Name not recorded" is true but it is the least informative thing that can
 * be said, so anything else that is also true is said instead.
 */
export function linkDisplayLabel(link: LabelFields | null | undefined): string {
  const f = link ?? {};
  if (f.displayLabel) return f.displayLabel;
  if (f.roadName) return f.roadName;

  const designation = f.naming?.routeDesignation || f.roadNumber;
  if (designation) return designation;

  const status = f.naming?.status ?? null;
  if (status === 'officially_unnamed') return NAME_STATE_LABEL.officially_unnamed;
  if (status === 'ambiguous_conflict') return NAME_STATE_LABEL.ambiguous_conflict;
  if (f.naming?.withheldSource) return WITHHELD_LABEL;

  return (
    contextualLabel(f) ||
    NAME_STATE_LABEL[(status ?? '') as NameStatus] ||
    UNKNOWN_LABEL
  );
}

/**
 * How long a chip label may run before the chip itself becomes the problem.
 *
 * Wide enough that "State-highway section near Kinleith" — the longest label
 * the contextual line produces in practice — survives intact. Truncation is a
 * last resort for an unusually long locality or authority name, not the normal
 * case.
 */
const CHIP_MAX = 40;

/**
 * Shorten a label for the map chip.
 *
 * Cuts at a word boundary and elides, rather than substituting a shorter form
 * of words. A chip that says something different from the inspector heading
 * for the same road is a second vocabulary the reader has to reconcile, which
 * is the failure this module exists to prevent.
 */
export function chipLabel(label: string, max = CHIP_MAX): string {
  const text = label.trim();
  if (text.length <= max) return text;
  const cut = text.slice(0, max);
  const space = cut.lastIndexOf(' ');
  const head = (space > max / 2 ? cut.slice(0, space) : cut).replace(
    /[\s,;:\-–—]+$/,
    '',
  );
  return `${head}…`;
}

/** What to show in the road-name position. Never the empty string. */
export function displayName(link: LabelFields | null | undefined): string;
export function displayName(
  name: string | null | undefined,
  status?: string | null,
): string;
export function displayName(
  first: LabelFields | string | null | undefined,
  status?: string | null,
): string {
  return linkDisplayLabel(asFields(first, status));
}

/**
 * A shorter form, for the map hover chip and the route breakdown, where space
 * is tight and the reader is scanning rather than reading.
 *
 * It shortens; it does not flatten. Every state that reads differently in the
 * inspector reads differently here too.
 */
export function shortDisplayName(link: LabelFields | null | undefined): string;
export function shortDisplayName(
  name: string | null | undefined,
  status?: string | null,
): string;
export function shortDisplayName(
  first: LabelFields | string | null | undefined,
  status?: string | null,
): string {
  return chipLabel(linkDisplayLabel(asFields(first, status)));
}

/**
 * The two call shapes, reconciled.
 *
 * The `(name, status)` form predates the label and is still how the tile
 * handlers call in; keeping it working is what lets the label be adopted one
 * caller at a time rather than in one sweep.
 */
function asFields(
  first: LabelFields | string | null | undefined,
  status?: string | null,
): LabelFields {
  if (typeof first === 'object' && first !== null) return first;
  return { roadName: first ?? null, naming: { status: status ?? null } };
}
