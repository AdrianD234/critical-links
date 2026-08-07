/*
 * How a road with no name reads.
 *
 * The reported defect was a tooltip saying "(unnamed link)" on a section of
 * State Highway 3 — a road that plainly has a name. Two separate failures sat
 * behind it: the name was never looked up properly, and the interface used one
 * phrase for four different situations. This guards the second.
 */

import { describe, expect, it } from 'vitest';

import { NAME_STATE_LABEL, displayName, shortDisplayName } from '../../apps/web/src/naming.js';

describe('displayName', () => {
  it('shows the name when there is one, whatever the status says', () => {
    expect(displayName('State Highway 3', 'route_designation_only')).toBe(
      'State Highway 3',
    );
    expect(displayName('Queen Street', 'amds_named')).toBe('Queen Street');
  });

  it('distinguishes a road with no name from a road whose name is unknown', () => {
    expect(displayName(null, 'officially_unnamed')).toBe('Unnamed road');
    expect(displayName(null, 'unresolved')).toBe('Name not recorded');
    expect(displayName(null, 'officially_unnamed')).not.toBe(
      displayName(null, 'unresolved'),
    );
  });

  it('says so when sources disagree rather than picking silently', () => {
    expect(displayName(null, 'ambiguous_conflict')).toBe('Name disputed');
  });

  it('never returns an empty string, whatever it is handed', () => {
    for (const status of [null, undefined, '', 'something_new']) {
      expect(displayName(null, status).length).toBeGreaterThan(0);
      expect(displayName(undefined, status).length).toBeGreaterThan(0);
    }
  });

  it('never says "unnamed link" again', () => {
    const produced = [
      ...Object.keys(NAME_STATE_LABEL).map((s) => displayName(null, s)),
      ...Object.keys(NAME_STATE_LABEL).map((s) => shortDisplayName(null, s)),
      displayName(null, null),
      shortDisplayName(null, null),
    ];
    for (const text of produced) {
      expect(text.toLowerCase()).not.toContain('unnamed link');
      expect(text).not.toContain('(');
    }
  });
});

describe('shortDisplayName', () => {
  it('keeps the map chip terse but still distinguishes the two cases', () => {
    expect(shortDisplayName(null, 'officially_unnamed')).toBe('Unnamed road');
    expect(shortDisplayName(null, 'unresolved')).toBe('No name');
    expect(shortDisplayName('Queen Street', 'amds_named')).toBe('Queen Street');
  });
});
