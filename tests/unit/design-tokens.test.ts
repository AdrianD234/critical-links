/**
 * The map palette and the CSS design tokens must not drift apart.
 *
 * MapLibre cannot read CSS custom properties, so the colours the map draws with
 * exist twice: once in apps/web/src/styles/tokens.css for the DOM, and once in
 * apps/web/src/styles/palette.ts for the GPU. Nothing in the type system or the
 * build connects them.
 *
 * Without this test the failure mode is quiet and ugly: someone adjusts the
 * route teal in tokens.css, the legend swatch in the inspector changes, the
 * line on the map does not, and the two are subtly different colours in every
 * screenshot from then on.
 */

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import {
  palette,
  paletteTokenNames,
} from '../../apps/web/src/styles/palette.js';

const TOKENS_CSS = fileURLToPath(
  new URL('../../apps/web/src/styles/tokens.css', import.meta.url),
);

/**
 * Read the `:root` declarations from tokens.css.
 *
 * Deliberately only the first `:root` block, so the reduced-motion overrides
 * further down the file cannot shadow a real value.
 */
function readRootTokens(): Map<string, string> {
  const css = readFileSync(TOKENS_CSS, 'utf8');
  const start = css.indexOf(':root {');
  expect(start, 'tokens.css must contain a :root block').toBeGreaterThan(-1);

  let depth = 0;
  let end = start;
  for (let i = css.indexOf('{', start); i < css.length; i++) {
    if (css[i] === '{') depth++;
    else if (css[i] === '}') {
      depth--;
      if (depth === 0) {
        end = i;
        break;
      }
    }
  }

  const block = css.slice(start, end);
  const tokens = new Map<string, string>();
  for (const m of block.matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+);/gi)) {
    tokens.set(m[1]!, m[2]!.trim());
  }
  return tokens;
}

describe('design tokens', () => {
  const tokens = readRootTokens();

  it('defines every token the map palette mirrors', () => {
    for (const name of Object.values(paletteTokenNames)) {
      expect(tokens.has(name), `tokens.css is missing ${name}`).toBe(true);
    }
  });

  it('matches the map palette exactly', () => {
    for (const [key, token] of Object.entries(paletteTokenNames)) {
      const css = tokens.get(token);
      const ts = palette[key as keyof typeof palette];
      expect(
        css?.toLowerCase(),
        `${token} in tokens.css is ${css}, but palette.${key} is ${ts}. ` +
          `The map and the DOM would render different colours.`,
      ).toBe(ts.toLowerCase());
    }
  });

  it('keeps the hero ink distinct from the closure ink', () => {
    /* The single most important decision in Direction C: the measurement is not
     * the alarm. If these ever become equal, the hero has silently gone back to
     * closure red and the design has lost the correction it was built around. */
    expect(tokens.get('--hero-ink')).toBeDefined();
    expect(tokens.get('--hero-ink')).not.toBe(tokens.get('--closure-ink'));
  });

  it('reserves closure red for the closure', () => {
    /* A fault is not a finding and must not borrow the closure's colour. */
    expect(tokens.get('--fault-ink')).toBeDefined();
    expect(tokens.get('--fault-ink')).not.toBe(tokens.get('--closure-ink'));
    expect(tokens.get('--fault-ink')).not.toBe(tokens.get('--closure'));
  });
});
