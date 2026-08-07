/*
 * How a road with no displayed name reads.
 *
 * The reported defect was a tooltip saying "(unnamed link)" on a section of
 * State Highway 3 — a road that plainly has a name. Two separate failures sat
 * behind it: the name was never looked up properly, and the interface used one
 * phrase for four different situations. This guards the second.
 *
 * The Tokoroa case then showed the same failure one level down. The map chip
 * answered every one of those four situations with "No name", including for a
 * state highway the database can describe from its classification, its
 * locality and the authority that manages it. Enrichment lifted name coverage
 * from 37.3% to 66.4% and the map looked exactly as unnamed as before, because
 * the chip was flattening everything the backend had learned.
 *
 * So this file now asserts two things at once: that the distinct states stay
 * distinct, and that "No name" cannot come back.
 */

import { describe, expect, it } from 'vitest';

import {
  NAME_STATE_LABEL,
  WITHHELD_LABEL,
  chipLabel,
  displayName,
  linkDisplayLabel,
  shortDisplayName,
} from '../../apps/web/src/naming.js';

/** Every status the wire can carry, plus the shapes it cannot. */
const ALL_STATUSES = [
  ...Object.keys(NAME_STATE_LABEL),
  null,
  undefined,
  '',
  'something_new',
];

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

describe('the backend label wins', () => {
  it('prefers displayLabel over the road name and over any fallback', () => {
    expect(
      displayName({
        displayLabel: 'State Highway 1',
        roadName: 'Campbell Road',
        naming: { status: 'externally_enriched' },
      }),
    ).toBe('State Highway 1');

    expect(
      shortDisplayName({
        displayLabel: 'Unnamed road',
        naming: { status: 'unresolved' },
      }),
    ).toBe('Unnamed road');
  });

  it('passes a contextual label through unchanged', () => {
    const label = 'State-highway section near Kinleith';
    expect(linkDisplayLabel({ displayLabel: label })).toBe(label);
    expect(displayName({ displayLabel: label })).toBe(label);
    /* The chip shortens; it must not shorten THIS. A label the backend
     * composed from class and locality is the shortest true thing there is
     * to say, and eliding it would put the reader back at "No name". */
    expect(shortDisplayName({ displayLabel: label })).toBe(label);
  });
});

describe('shortDisplayName', () => {
  it('keeps the chip terse but still distinguishes every state', () => {
    expect(shortDisplayName(null, 'officially_unnamed')).toBe('Unnamed road');
    expect(shortDisplayName(null, 'ambiguous_conflict')).toBe('Name disputed');
    expect(shortDisplayName(null, 'unresolved')).toBe('Name not recorded');
    expect(shortDisplayName('Queen Street', 'amds_named')).toBe('Queen Street');
  });

  /*
   * The regression that matters. If someone collapses these again — for the
   * perfectly reasonable-sounding reason that the chip is narrow — this fails
   * before it reaches a map.
   */
  it('produces a different string for every distinct state', () => {
    const states = [
      shortDisplayName(null, 'officially_unnamed'),
      shortDisplayName(null, 'ambiguous_conflict'),
      shortDisplayName(null, 'unresolved'),
      shortDisplayName({ naming: { status: 'unresolved', withheldSource: 'linz' } }),
      shortDisplayName({ naming: { status: 'unresolved' }, stateHighway: true }),
    ];
    expect(new Set(states).size).toBe(states.length);
  });

  it('never returns "No name" for any status', () => {
    for (const status of ALL_STATUSES) {
      expect(shortDisplayName(null, status)).not.toBe('No name');
      expect(shortDisplayName('Queen Street', status)).not.toBe('No name');
      /* Object form, including the withheld case, which the string form has
       * no way to express. */
      expect(
        shortDisplayName({ naming: { status, withheldSource: 'linz' } }),
      ).not.toBe('No name');
      expect(shortDisplayName({ naming: { status } })).not.toBe('No name');
    }
    expect(shortDisplayName(null)).not.toBe('No name');
    expect(shortDisplayName({})).not.toBe('No name');
  });

  it('says a name is withheld rather than absent', () => {
    const withheld = shortDisplayName({
      naming: { status: 'unresolved', withheldSource: 'linz_road_sections' },
    });
    expect(withheld).toBe(WITHHELD_LABEL);
    expect(withheld).not.toBe(shortDisplayName(null, 'unresolved'));
  });
});

describe('the fallback ladder', () => {
  /*
   * Vector tiles carry a name status, a route number and a state-highway flag
   * but no label, so the chip over an untouched tile still has to say
   * something better than nothing. It mirrors the backend's order rather than
   * inventing a second vocabulary.
   */
  it('falls back to the route the road carries before any no-name wording', () => {
    expect(
      linkDisplayLabel({ roadNumber: 'SH 1', naming: { status: 'unresolved' } }),
    ).toBe('SH 1');
  });

  it('describes a state highway from its class and locality', () => {
    expect(
      linkDisplayLabel({
        naming: { status: 'unresolved' },
        stateHighway: true,
        locality: 'Kinleith',
      }),
    ).toBe('State-highway section near Kinleith');

    expect(
      linkDisplayLabel({ naming: { status: 'unresolved' }, stateHighway: true }),
    ).toBe('State-highway section');
  });

  it('names the managing authority when there is nothing else', () => {
    expect(
      linkDisplayLabel({
        naming: { status: 'unresolved' },
        rca: 'Waitomo District Council',
      }),
    ).toBe('Road section managed by Waitomo District Council');
  });

  it('states the no-name case only when nothing else is known', () => {
    expect(linkDisplayLabel({ naming: { status: 'unresolved' } })).toBe(
      'Name not recorded',
    );
    expect(linkDisplayLabel({})).toBe('Name not recorded');
    expect(linkDisplayLabel(null)).toBe('Name not recorded');
  });

  it('keeps an authoritative "no name" above derived context', () => {
    /* officially_unnamed is a fact an authority asserts. Replacing it with a
     * contextual description would discard the more specific answer. */
    expect(
      linkDisplayLabel({
        naming: { status: 'officially_unnamed' },
        stateHighway: true,
        locality: 'Kinleith',
      }),
    ).toBe('Unnamed road');
  });
});

describe('chipLabel', () => {
  it('leaves anything that fits alone', () => {
    expect(chipLabel('State-highway section near Kinleith')).toBe(
      'State-highway section near Kinleith',
    );
    expect(chipLabel('Queen Street')).toBe('Queen Street');
  });

  it('cuts at a word boundary and says it cut', () => {
    const long = 'Local-road section near Otaki Forks Recreational Reserve Road';
    const short = chipLabel(long);
    expect(short.length).toBeLessThanOrEqual(41);
    expect(short.endsWith('…')).toBe(true);
    expect(long.startsWith(short.slice(0, -1))).toBe(true);
  });

  it('never truncates to nothing', () => {
    expect(chipLabel('a'.repeat(80)).length).toBeGreaterThan(1);
  });
});
