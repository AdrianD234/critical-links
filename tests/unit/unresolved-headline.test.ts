/**
 * An unresolved analysis must not be headlined as a finding about the road.
 *
 * V1's corridor search could return DISCONNECTED for a query PostgreSQL had
 * cancelled, and the inspector headlined that "NO REPLACEMENT PATH" on a
 * closure traffic in fact got past with an extra 260 m. The engine no longer
 * reports a cancelled search as a finding (docs/audits/v1-timeout/), and the
 * headline for one is "Analysis unresolved".
 *
 * The part that is easy to lose is the ORDER. Both "Road cut off" and "No
 * replacement path" assert that traffic cannot get past, so both must sit
 * BELOW the unresolved check — a reordering while editing the hero would
 * silently restore the false claim, and it would look like tidying in a diff.
 *
 * A source-level check, in the same spirit as tests/unit/product-copy.test.ts.
 * It cannot prove the component renders; the Playwright suite and the captured
 * screenshots in docs/audits/v1-timeout/ do that. What it does prove is that
 * the branch that must come first still does.
 */

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

const RESULT_VIEW = fileURLToPath(
  new URL('../../apps/web/src/inspector/ResultView.tsx', import.meta.url),
);
const RESULT_STATUS = fileURLToPath(
  new URL('../../apps/web/src/inspector/ResultStatus.tsx', import.meta.url),
);

const view = readFileSync(RESULT_VIEW, 'utf8');
const status = readFileSync(RESULT_STATUS, 'utf8');

/** Code, not prose: every anchor below is an expression or a rendered label. */
const UNRESOLVED_BRANCH = "if (corridor && statusKindOf(corridor.status) === 'fault')";
const CUT_OFF_BRANCH = 'label="Road cut off"';
const NO_PATH_BRANCH = '<div className="lab">No replacement path</div>';
const UNRESOLVED_LABEL = '<div className="lab">Analysis unresolved</div>';

function indexOf(haystack: string, needle: string, what: string): number {
  const at = haystack.indexOf(needle);
  expect(at, `${what} is no longer present in ResultView.tsx`).toBeGreaterThan(-1);
  return at;
}

describe('the unresolved headline', () => {
  it('is what an unresolved corridor search reports', () => {
    expect(view).toContain(UNRESOLVED_BRANCH);
    expect(view).toContain(UNRESOLVED_LABEL);
  });

  it('is decided before anything that claims traffic cannot get past', () => {
    const unresolved = indexOf(view, UNRESOLVED_BRANCH, 'the unresolved check');
    const cutOff = indexOf(view, CUT_OFF_BRANCH, 'the "Road cut off" hero');
    const noPath = indexOf(view, NO_PATH_BRANCH, 'the "No replacement path" hero');

    expect(
      unresolved,
      '"Road cut off" would be reached for a corridor search that never finished',
    ).toBeLessThan(cutOff);
    expect(
      unresolved,
      '"No replacement path" would be reached for a corridor search that never finished',
    ).toBeLessThan(noPath);
  });

  it('covers every status the engine can report for an unresolved search', () => {
    /* The branch tests statusKindOf(...) === 'fault' rather than naming
     * codes, so the two statuses route_many can now return have to be in the
     * FAULTS set for it to fire at all. */
    const faults = status.slice(
      status.indexOf('const FAULTS'),
      status.indexOf('export function statusKindOf'),
    );
    expect(faults).toContain('UNRESOLVED_TIMEOUT');
    expect(faults).toContain('API_ERROR');
    expect(status).not.toMatch(/FINDINGS = new Set\(\[[^\]]*UNRESOLVED_TIMEOUT/);
  });

  it('does not headline a fault as a plain absence of a result', () => {
    /* "No result" understated it: nothing was found because nothing finished.
     * Both unresolved headlines now say the same thing. */
    expect(view).not.toContain('<div className="lab">No result</div>');
  });
});
