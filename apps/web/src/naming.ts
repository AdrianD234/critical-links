/*
 * How a road with no name is described, in one place.
 *
 * "(unnamed link)" was doing four jobs at once. It appeared on a road that
 * genuinely has no name, on a road whose name simply was not found, on a road
 * two sources disagree about, and on a road whose name is known but cannot be
 * shown for licensing reasons. Those are different facts about the world and a
 * reader can act on them differently, so they get different words.
 *
 * The API sends the authoritative label with each link; these are the fallback
 * for tile properties and older responses, which carry a status but no prose.
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

/** What to show in the road-name position. Never the empty string. */
export function displayName(
  name: string | null | undefined,
  status?: string | null,
): string {
  if (name) return name;
  const label = NAME_STATE_LABEL[(status ?? '') as NameStatus];
  return label || 'Name not recorded';
}

/**
 * A shorter form, for the map hover chip where space is tight and the reader
 * is scanning rather than reading.
 */
export function shortDisplayName(
  name: string | null | undefined,
  status?: string | null,
): string {
  if (name) return name;
  return status === 'officially_unnamed' ? 'Unnamed road' : 'No name';
}
