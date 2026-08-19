/**
 * What a shared outage span survives.
 *
 * The failure these guard is silent: a permalink that reopens onto a DIFFERENT
 * piece of road, or a different corridor between the same two points, while
 * still looking like the link that was sent. Nothing on screen would say so.
 *
 * Tested through the pure functions with explicit query strings rather than
 * through `window`, so they run in the plain node environment the rest of the
 * unit suite uses.
 */

import { describe, expect, it } from 'vitest';

import {
  mergeSpanIntoSearch,
  readSpanUrl,
  spanSearchParams,
  type SpanUrlState,
} from '../../apps/web/src/span/spanUrl.js';

const SPAN: SpanUrlState = {
  aLinkId: 1810,
  aFraction: 0.5,
  bLinkId: 1811,
  bFraction: 0.25,
  corridorId: 'c728a1a781750bf1be306b169e1b8a9b',
  direction: 'both',
  vehicle: 'car',
  metric: 'distance',
};

function roundTrip(s: SpanUrlState): SpanUrlState | null {
  // The vehicle and metric belong to the application's URL state, not to the
  // span - the span reads them but never writes them, so that one parameter
  // has one writer. A real URL always carries them, so the round trip supplies
  // them the way the application would.
  const p = spanSearchParams(s);
  p.set('vehicle', s.vehicle);
  p.set('metric', s.metric);
  return readSpanUrl(`?${p.toString()}`);
}

describe('round trip', () => {
  it('restores every field it was given', () => {
    expect(roundTrip(SPAN)).toEqual(SPAN);
  });

  it('restores a contraflow and a non-default scenario', () => {
    const s: SpanUrlState = {
      ...SPAN,
      direction: 'b_to_a',
      vehicle: 'heavy',
      metric: 'time',
    };

    expect(roundTrip(s)).toEqual(s);
  });

  it('keeps a fraction to nanometre precision on a kilometre', () => {
    const s: SpanUrlState = { ...SPAN, aFraction: 0.123456789 };

    expect(roundTrip(s)!.aFraction).toBeCloseTo(0.123456789, 9);
  });

  it('carries the corridor id, which is the part that is easy to think optional', () => {
    // Without it a restored link is free to rank two equally-evidenced
    // corridors the other way and close a different road under the same URL.
    expect(spanSearchParams(SPAN).get('sc')).toBe(SPAN.corridorId);
  });
});

describe('a URL with no span', () => {
  it('reads as no span rather than as a default one', () => {
    expect(readSpanUrl('?link=123&metric=distance')).toBeNull();
  });

  it('reads as no span when the flag is absent even if the rest is present', () => {
    expect(readSpanUrl('?sa=1:0.5&sb=2:0.5&sc=abc&sd=both')).toBeNull();
  });
});

describe('a partial span is refused entirely', () => {
  // Half a span silently completed from defaults is the failure this module
  // exists to prevent: it would describe a stretch of road nobody chose.
  const full = `?${spanSearchParams(SPAN)}`;

  it('rejects a missing handle', () => {
    expect(readSpanUrl(full.replace(/&?sb=[^&]*/, ''))).toBeNull();
  });

  it('rejects a missing corridor', () => {
    expect(readSpanUrl(full.replace(/&?sc=[^&]*/, ''))).toBeNull();
  });

  it('rejects a missing direction', () => {
    expect(readSpanUrl(full.replace(/&?sd=[^&]*/, ''))).toBeNull();
  });
});

describe('malformed values are refused, not coerced', () => {
  it('rejects a handle that is not linkId:fraction', () => {
    expect(readSpanUrl('?span=1&sa=nonsense&sb=2:0.5&sc=x&sd=both')).toBeNull();
  });

  it('rejects a fraction outside the link', () => {
    expect(readSpanUrl('?span=1&sa=1:1.5&sb=2:0.5&sc=x&sd=both')).toBeNull();
    expect(readSpanUrl('?span=1&sa=1:-0.2&sb=2:0.5&sc=x&sd=both')).toBeNull();
  });

  it('rejects a negative or fractional link id', () => {
    expect(readSpanUrl('?span=1&sa=-3:0.5&sb=2:0.5&sc=x&sd=both')).toBeNull();
    expect(readSpanUrl('?span=1&sa=1.5:0.5&sb=2:0.5&sc=x&sd=both')).toBeNull();
  });

  it('rejects a direction it does not recognise', () => {
    expect(readSpanUrl('?span=1&sa=1:0.5&sb=2:0.5&sc=x&sd=sideways')).toBeNull();
  });

  it('falls back for an unrecognised vehicle rather than refusing the span', () => {
    // The scenario is a view setting; the span is the thing being described.
    // A bad vehicle should not strand a link that still names its road.
    const s = readSpanUrl('?span=1&sa=1:0.5&sb=2:0.5&sc=x&sd=both&vehicle=tank');

    expect(s).not.toBeNull();
    expect(s!.vehicle).toBe('car');
  });
});

describe('merging with the rest of the application state', () => {
  it('leaves existing parameters untouched', () => {
    const merged = mergeSpanIntoSearch('?link=99&metric=time&v=2', SPAN);
    const p = new URLSearchParams(merged);

    expect(p.get('link')).toBe('99');
    expect(p.get('v')).toBe('2');
    expect(p.get('span')).toBe('1');
  });

  it('strips every span parameter when the span is cleared', () => {
    const withSpan = mergeSpanIntoSearch('?link=99&v=2', SPAN);
    const without = mergeSpanIntoSearch(withSpan, null);
    const p = new URLSearchParams(without);

    for (const key of ['span', 'sa', 'sb', 'sc', 'sd']) {
      expect(p.get(key)).toBeNull();
    }
    expect(p.get('link')).toBe('99');
    expect(p.get('v')).toBe('2');
  });

  it('is idempotent, so a redraw does not churn history', () => {
    const once = mergeSpanIntoSearch('?link=99', SPAN);
    expect(mergeSpanIntoSearch(once, SPAN)).toBe(once);
  });

  it('produces an empty string rather than a bare question mark', () => {
    expect(mergeSpanIntoSearch('', null)).toBe('');
  });

  it('does not disturb a URL that never had a span', () => {
    const before = '?link=99&metric=time&vehicle=car&v=2';
    const after = mergeSpanIntoSearch(before, null);

    expect(new URLSearchParams(after).get('link')).toBe('99');
    expect(readSpanUrl(after)).toBeNull();
  });
});
