/**
 * Product-facing copy must not claim more than the analysis supports.
 *
 * The hero once read "Traffic that used this 1,971 m link must travel 55.45 km
 * instead". That is a traffic-assignment claim: it asserts which traffic used
 * the link, that all of it reroutes, and that it takes this particular path.
 * The engine computes one shortest path between two nodes and knows none of
 * those things.
 *
 * A reviewer caught it. This test is here so a reviewer does not have to catch
 * it again — the phrasings below are cheap to reintroduce while writing a
 * plausible-sounding sentence, and impossible to spot in a diff.
 */

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

const SRC = fileURLToPath(new URL('../../apps/web/src', import.meta.url));

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) return sourceFiles(full);
    return /\.(tsx?|css)$/.test(entry) ? [full] : [];
  });
}

/**
 * Strip comments before matching.
 *
 * Comments are where the ban is *explained*, and several of them necessarily
 * quote the wording they prohibit. Matching them would make it impossible to
 * document why a phrasing is forbidden without tripping the check — and the
 * explanation is the part that stops the mistake recurring.
 *
 * Crude but adequate: this is a lint over prose, not a parser. A `//` inside a
 * string literal (a URL) loses the rest of that line, which at worst hides a
 * violation on the same line as a URL — not a trade that has ever mattered
 * here, and the block-comment case is exact.
 */
function withoutComments(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/^\s*\/\/.*$/gm, ' ')
    .replace(/([^:])\/\/.*$/gm, '$1');
}

/** Text that would be a claim about traffic if it reached the screen. */
const FORBIDDEN: { pattern: RegExp; why: string }[] = [
  {
    pattern: /traffic that (used|uses)/i,
    why: 'asserts which traffic used the link; the model has no trip data',
  },
  {
    pattern: /vehicles affected|affected vehicles/i,
    why: 'implies a vehicle count; nothing in the pipeline knows volumes',
  },
  {
    pattern: /must (travel|take|use|divert)/i,
    why: 'asserts that traffic reroutes onto this path; not modelled',
  },
  {
    pattern: /\bdrivers (will|must|would)\b/i,
    why: 'asserts behaviour of road users; not modelled',
  },
  {
    pattern: /legally valid route|legal route/i,
    why: 'the engine does not evaluate legality',
  },
  {
    pattern: /whole road asset/i,
    why: 'the closure scope is an AMDS source feature, not a road asset',
  },
];

/** Wording that presents a modelled closure as a real one. */
const LIVE_EVENT_PHRASES = [/closure active/i, /road is closed/i, /currently closed/i];

describe('product copy', () => {
  const files = sourceFiles(SRC);

  it('finds source to check', () => {
    expect(files.length).toBeGreaterThan(20);
  });

  for (const { pattern, why } of FORBIDDEN) {
    it(`never says ${pattern.source} — ${why}`, () => {
      const offenders = files.filter((f) =>
        pattern.test(withoutComments(readFileSync(f, 'utf8'))),
      );
      expect(
        offenders.map((f) => f.slice(SRC.length + 1)),
        `Forbidden phrasing ${pattern}: ${why}`,
      ).toEqual([]);
    });
  }

  it('never presents a modelled closure as a live road event', () => {
    const offenders: string[] = [];
    for (const f of files) {
      const src = withoutComments(readFileSync(f, 'utf8'));
      for (const p of LIVE_EVENT_PHRASES) {
        if (p.test(src)) offenders.push(`${f.slice(SRC.length + 1)} :: ${p}`);
      }
    }
    expect(
      offenders,
      'Closures are hypothetical. Wording that reads as a live incident is ' +
        'one screenshot away from being taken for one.',
    ).toEqual([]);
  });

  it('describes the closure as modelled wherever it is named to the user', () => {
    const scenario = readFileSync(join(SRC, 'api', 'scenario.ts'), 'utf8');
    /* Every branch of the scope label must carry the word. */
    const labels = scenario.match(/return '(Modelled[^']*)'/g) ?? [];
    expect(labels.length).toBeGreaterThanOrEqual(6);
  });
});
