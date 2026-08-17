/*
 * What the V2 preview is allowed to say, and what it may not turn into a
 * finding.
 *
 * Two defects sit behind this file, both of them cases where a true result was
 * rendered as a stronger claim than the analysis supports.
 *
 * The first is "No physical isolation". It reads as a statement about roads,
 * and the engine can only make a statement about the graph it ran on: Gu is
 * inferred topology, assembled with an ingest tolerance. The wording became
 * "No isolation in the represented physical-access graph", and two findings
 * that had previously been folded into stronger neighbours got their own words
 * — a network that splits with no decisive side, and a request that only
 * partly resolved.
 *
 * The second is `same_scc_after_closure` becoming three-state. Null means no
 * conclusion was reached. A ternary renders that as false, which asserts that
 * a return path was tested and did not exist. On a one-way link nothing was
 * tested at all.
 */

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import {
  BOUNDARY_VS_ENDPOINT,
  V2_BOUNDARY_DEFINITIVE_HEADLINES,
  MUTUAL_REACHABILITY_UNKNOWN,
  V2_BOUNDARY_HEADLINES,
  V2_HEADLINES,
  mutualReachability,
} from '../../apps/web/src/v2Wording.js';

const SRC = fileURLToPath(new URL('../../apps/web/src/', import.meta.url));

/**
 * Strip comments before matching, as tests/unit/product-copy.test.ts does.
 *
 * The comments are where the corrected wording is explained, and explaining it
 * means quoting it. A check that could not tell the two apart would make the
 * explanation impossible to write.
 */
function withoutComments(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/^\s*\/\/.*$/gm, ' ')
    .replace(/([^:])\/\/.*$/gm, '$1');
}

function source(rel: string): string {
  return withoutComments(readFileSync(SRC + rel, 'utf8'));
}

describe('the headline vocabulary', () => {
  it('is the eight the engine documents, and nothing else', () => {
    expect([...V2_HEADLINES].sort()).toEqual(
      [
        'Road cut off',
        'Network split into two represented components',
        'Through route found',
        'No endpoint route',
        'Directional access loss',
        'No isolation in the represented physical-access graph',
        'Partial analysis',
        'Analysis unresolved',
      ].sort(),
    );
  });

  /*
   * The two findings that previously had no words of their own. Without them a
   * split with no decisive side was reported as "Road cut off", naming a side
   * the data does not name, and a half-resolved request was reported as
   * whatever its surviving direction found.
   */
  it('has words for a split with no decisive side, and for a partial run', () => {
    expect(V2_HEADLINES).toContain(
      'Network split into two represented components',
    );
    expect(V2_HEADLINES).toContain('Partial analysis');
  });

  it('never says "No physical isolation" again', () => {
    expect(V2_HEADLINES).not.toContain('No physical isolation');
    for (const headline of V2_HEADLINES) {
      /* The claim is about the represented graph. Any headline that mentions
       * isolation has to say which. */
      if (/isolation/i.test(headline)) {
        expect(headline).toContain('represented physical-access graph');
      }
    }
  });

  it('says nothing twice', () => {
    expect(new Set(V2_HEADLINES).size).toBe(V2_HEADLINES.length);
  });
});

describe('mutual reachability after closure', () => {
  it('reports no conclusion as no conclusion', () => {
    expect(mutualReachability(null)).toBe(MUTUAL_REACHABILITY_UNKNOWN);
    expect(mutualReachability(undefined)).toBe(MUTUAL_REACHABILITY_UNKNOWN);
  });

  /*
   * The regression that matters. `value ? a : b` is the shortest thing to
   * write here and it is wrong, because it puts null on the false branch.
   */
  it('does not render an untested pair as a tested failure', () => {
    const unknown = mutualReachability(null);
    expect(unknown).not.toBe(mutualReachability(false));
    expect(unknown).not.toBe(mutualReachability(true));
    for (const negative of ['no', 'false', 'not reachable both ways']) {
      expect(unknown.toLowerCase()).not.toBe(negative);
    }
  });

  it('still answers plainly when there is an answer', () => {
    expect(mutualReachability(true)).toBe('reachable both ways');
    expect(mutualReachability(false)).toBe('not reachable both ways');
  });
});

/*
 * The isolation wording, in the panel the product actually renders.
 *
 * These assertions used to run against inspector/V2Preview.tsx, the panel that
 * existed while two engines were selectable side by side. That panel is gone;
 * the checks are not, because the defects they pin are properties of how
 * isolation is worded and not of which file words it.
 *
 * `iso` is the local binding the panel gives the isolation block, so the field
 * references read `iso.graphExact` rather than `isolation.graphExact`. Matching
 * on the field name alone is what makes that survive a rename of the binding
 * without weakening the check.
 *
 * The mutual-reachability assertions did NOT move. `same_scc_after_closure` is
 * carried by the endpoint response, and the panel renders the boundary one,
 * which has no such field — there is no ternary to get wrong. The helper and
 * its three-state contract stay exercised by the unit tests above, so a panel
 * that starts rendering directed access again has something to render it
 * through.
 */
describe('the closure result panel', () => {
  const panel = source('inspector/ClosureResultView.tsx');
  const types = source('api/types.ts');

  it('reports the exactness claims apart rather than as one flag', () => {
    expect(panel).not.toMatch(/\biso(lation)?\.exact\b/);
    expect(panel).toContain('.calculationExact');
    expect(panel).toContain('.partitionExact');
    expect(panel).toContain('.graphExact');
  });

  it('never claims the inferred graph models the road network unqualified', () => {
    /* The false branch is the one that matters: it is the branch nearly every
     * real result takes, and shortening it to "not exact" would drop the only
     * sentence saying WHAT is not exact. */
    expect(panel).toContain(
      'inferred topology — not an exact model of the road network',
    );
  });

  it('surfaces the topology confidence with its reason', () => {
    expect(panel).toContain('.topologyConfidence');
    expect(panel).toContain('.topologyConfidenceReason');
  });

  it('says when no side was named as cut off', () => {
    expect(panel).toContain('.principalSideAmbiguous');
    expect(panel).toContain('.principalSideRule');
  });

  /*
   * Isolation is the strongest claim the system makes and it is computed on the
   * undirected graph. Letting a routing result produce it was the retired
   * engine's central defect, so the panel has to keep saying they are separate
   * questions.
   */
  it('keeps isolation separate from the routing result in words', () => {
    expect(panel).toContain('Physical isolation — a separate question');
    expect(panel).toMatch(/independent of the routing/i);
  });

  it('keeps the retired wording out of the wire types', () => {
    expect(types).not.toContain('No physical isolation');
    expect(types).toContain(
      'No isolation in the represented physical-access graph',
    );
  });
});

/*
 * The corridor caveat.
 *
 * The 2026-08-08 coordinator adjudication took corridor truncation OUT of the
 * top-level headline in exchange for stating it where the corridor is shown.
 * The engine half of that trade is pinned in python/tests/test_movements.py.
 * This is the half the engine cannot check: if the caveat line were dropped,
 * or stopped being gated on `evaluationTruncated`, or stopped quoting the
 * generated-versus-evaluated counts, the adjudication would have removed a
 * warning and put nothing in its place — and every Python test would still be
 * green.
 */
describe('the corridor caveat the adjudication traded a headline gate for', () => {
  const findings = source('inspector/ClosureResultView.tsx');
  const types = source('api/types.ts');

  it('renders, gated on evaluationTruncated and nothing else', () => {
    expect(findings).toContain('corridor.evaluationTruncated &&');
    expect(findings).toContain('This starting point is provisional');
  });

  it('states the flag as the subtraction behind it', () => {
    for (const field of [
      'candidatesGeneratedUpstream',
      'candidatesGeneratedDownstream',
      'candidatesEvaluatedUpstream',
      'candidatesEvaluatedDownstream',
    ]) {
      expect(types).toContain(field);
      expect(findings).toContain(`corridor.${field}`);
    }
  });

  it('says the movement figures are unaffected, because they are', () => {
    /* The whole basis of the adjudication is that no corridor candidate can
     * change a movement conclusion. The panel has to say so, or a reader sees
     * a warning above numbers it does not apply to. */
    expect(findings).toMatch(/movement\s+figures[\s\S]{0,40}unaffected/i);
  });
});

/*
 * The boundary engine's vocabulary.
 *
 * Its headlines are the FIRST thing a reader of the new panel sees, and the
 * whole point of PR 2 is that they answer a different question from the
 * endpoint ones. The two lists must therefore stay disjoint: a boundary
 * headline appearing in the endpoint list would be a claim about the closed
 * segment's own ends, made by an engine that never measured them.
 */
describe('boundary headlines', () => {
  it('are exactly the four the engine documents', () => {
    expect([...V2_BOUNDARY_HEADLINES].sort()).toEqual(
      [
        'Analysis unresolved',
        'No through movement identified',
        'Partial analysis',
        'Through movement diverts',
        'Through movement has no represented replacement',
      ].sort(),
    );
  });

  /*
   * A bounded search may report what it found; it may not imply it found
   * everything. The national sample recorded 10 truncated analyses that still
   * carried a definitive sentence, which is why the two lists are separate.
   */
  it('separate the sentences that assert something about the road', () => {
    for (const h of V2_BOUNDARY_DEFINITIVE_HEADLINES) {
      expect(V2_BOUNDARY_HEADLINES).toContain(h);
    }
    expect(V2_BOUNDARY_DEFINITIVE_HEADLINES).not.toContain('Partial analysis');
    expect(V2_BOUNDARY_DEFINITIVE_HEADLINES).not.toContain(
      'Analysis unresolved',
    );
  });

  it('share only the two non-findings with the endpoint vocabulary', () => {
    const shared = V2_BOUNDARY_HEADLINES.filter((h) =>
      (V2_HEADLINES as string[]).includes(h),
    );
    /* Both engines may fail to resolve or run out of budget, and both must say
     * so in the same words. Everything else is a claim only one of them is
     * entitled to make. */
    expect([...shared].sort()).toEqual(
      ['Analysis unresolved', 'Partial analysis'].sort(),
    );
  });

  it('never describe a movement result as a road losing access', () => {
    /* "Cut off" belongs to the undirected isolation result alone. A routing
     * headline borrowing it is precisely the V1 defect PR 1 catalogued. */
    for (const h of V2_BOUNDARY_HEADLINES) {
      expect(h.toLowerCase()).not.toContain('cut off');
      expect(h.toLowerCase()).not.toContain('isolat');
    }
  });

  it('explains that a boundary/endpoint difference is not a disagreement', () => {
    expect(BOUNDARY_VS_ENDPOINT).toContain('different quantities');
    expect(BOUNDARY_VS_ENDPOINT).toContain('not a disagreement');
  });
});
