/**
 * Direction normalisation.
 *
 * The bug these guard: the URL defaults to `reverse`, a one-way link has no
 * reverse result, and nothing moved the focus. The panel loaded with reverse
 * selected, reverse disabled, no hero, no route, and — because the roving
 * tabindex followed the selected view — no keyboard-focusable tab either.
 */

import { describe, expect, it } from 'vitest';

import {
  availabilityOf,
  focusableTab,
  normaliseDirection,
} from '../../apps/web/src/state/direction.js';

const BOTH = { forward: true, reverse: true };
const FWD_ONLY = { forward: true, reverse: false };
const REV_ONLY = { forward: false, reverse: true };
const NEITHER = { forward: false, reverse: false };

describe('availabilityOf', () => {
  it('reads a direction as available when the response carries a result', () => {
    expect(availabilityOf({ forward: {}, reverse: {} })).toEqual(BOTH);
    expect(availabilityOf({ forward: {}, reverse: null })).toEqual(FWD_ONLY);
    expect(availabilityOf({ forward: null, reverse: null })).toEqual(NEITHER);
  });

  it('counts a DISCONNECTED direction as available', () => {
    /* DISCONNECTED is a finding about that direction, not an absence of it.
     * Hiding the tab would misreport "no replacement path exists" as "this
     * direction does not exist", which are very different statements. */
    const disconnected = { status: 'DISCONNECTED' };
    expect(availabilityOf({ forward: disconnected, reverse: null })).toEqual(
      FWD_ONLY,
    );
  });
});

describe('normaliseDirection', () => {
  it('leaves an available direction alone', () => {
    const n = normaliseDirection('reverse', BOTH);
    expect(n).toEqual({ view: 'reverse', changed: false, announcement: null });
  });

  it('switches to forward when a one-way link has no reverse', () => {
    /* The exact case that produced a blank panel. */
    const n = normaliseDirection('reverse', FWD_ONLY);
    expect(n.view).toBe('forward');
    expect(n.changed).toBe(true);
    expect(n.announcement).toMatch(/one-way/i);
    expect(n.announcement).toMatch(/forward/i);
  });

  it('switches to reverse when only reverse is represented', () => {
    const n = normaliseDirection('forward', REV_ONLY);
    expect(n.view).toBe('reverse');
    expect(n.changed).toBe(true);
  });

  it('falls back from Compare to the single available direction', () => {
    const n = normaliseDirection('compare', FWD_ONLY);
    expect(n.view).toBe('forward');
    expect(n.changed).toBe(true);
    expect(n.announcement).toMatch(/cannot be compared/i);
  });

  it('keeps Compare when both directions exist', () => {
    expect(normaliseDirection('compare', BOTH).changed).toBe(false);
  });

  it('changes nothing when neither direction is available', () => {
    /* There is no better view to move to, and the response's own failure state
     * is what should be shown. Silently switching tabs would hide that. */
    for (const v of ['forward', 'reverse', 'compare'] as const) {
      expect(normaliseDirection(v, NEITHER)).toEqual({
        view: v,
        changed: false,
        announcement: null,
      });
    }
  });

  it('always lands on an available direction whenever one exists', () => {
    for (const available of [BOTH, FWD_ONLY, REV_ONLY]) {
      for (const requested of ['forward', 'reverse', 'compare'] as const) {
        const { view } = normaliseDirection(requested, available);
        if (view === 'compare') {
          expect(available.forward && available.reverse).toBe(true);
        } else {
          expect(available[view]).toBe(true);
        }
      }
    }
  });
});

describe('focusableTab', () => {
  it('gives the tab stop to the selected tab when it is enabled', () => {
    expect(focusableTab('reverse', BOTH)).toBe('reverse');
    expect(focusableTab('compare', BOTH)).toBe('compare');
  });

  it('moves the tab stop off a disabled tab', () => {
    /* Otherwise the only tabIndex=0 tab is disabled and the entire tablist
     * drops out of the keyboard order. */
    expect(focusableTab('reverse', FWD_ONLY)).toBe('forward');
    expect(focusableTab('compare', REV_ONLY)).toBe('reverse');
  });

  it('reports no tab stop when nothing is enabled', () => {
    expect(focusableTab('reverse', NEITHER)).toBeNull();
  });

  it('never returns a disabled tab', () => {
    for (const available of [BOTH, FWD_ONLY, REV_ONLY, NEITHER]) {
      for (const view of ['forward', 'reverse', 'compare'] as const) {
        const t = focusableTab(view, available);
        if (t === null) continue;
        if (t === 'compare') {
          expect(available.forward && available.reverse).toBe(true);
        } else {
          expect(available[t]).toBe(true);
        }
      }
    }
  });
});
