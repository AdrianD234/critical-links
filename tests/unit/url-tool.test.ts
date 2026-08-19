/**
 * The closure-method field in the URL.
 *
 * The property that matters is backward compatibility in both directions.
 * Every URL that existed before the editor did must read as link mode, and a
 * link-mode URL written by this build must be byte-identical to what the
 * pre-editor build wrote for the same state - anything else would churn every
 * circulating permalink. Span URLs from the first editor build carry `span=1`
 * but no `tool`; they must keep restoring the editor.
 */

import { describe, expect, it } from 'vitest';

import { buildSearch, readUrl, type ExploreUrlState } from '../../apps/web/src/state/url.js';

const LINK_STATE: ExploreUrlState = {
  link: 'abc-123',
  scenario: { metric: 'distance', vehicle: 'car', closureScope: 'segment' },
  focus: 'reverse',
  compare: false,
  snapshot: null,
  tool: 'link',
};

describe('reading the tool', () => {
  it('absent means link, for every pre-editor URL in circulation', () => {
    expect(readUrl('?link=abc&v=2').tool).toBe('link');
    expect(readUrl('?metric=distance&v=2').tool).toBe('link');
    expect(readUrl('').tool).toBe('link');
  });

  it('tool=span means span', () => {
    expect(readUrl('?tool=span&v=2').tool).toBe('span');
  });

  it('a first-build span URL without the field still reads as span', () => {
    // Shared before `tool` existed; carrying span state IS the mode.
    expect(readUrl('?span=1&sa=1:0.5&sb=2:0.5&sc=x&sd=both&v=2').tool).toBe('span');
  });

  it('an unrecognised value falls back to link rather than guessing', () => {
    expect(readUrl('?tool=lasso&v=2').tool).toBe('link');
  });
});

describe('writing the tool', () => {
  it('link mode writes nothing, keeping old URLs byte-identical', () => {
    expect(buildSearch(LINK_STATE)).not.toContain('tool=');
  });

  it('span mode says so explicitly', () => {
    const search = buildSearch({ ...LINK_STATE, tool: 'span' });
    expect(new URLSearchParams(search).get('tool')).toBe('span');
  });

  it('round-trips through its own reader', () => {
    expect(readUrl(buildSearch({ ...LINK_STATE, tool: 'span' })).tool).toBe('span');
    expect(readUrl(buildSearch(LINK_STATE)).tool).toBe('link');
  });
});
