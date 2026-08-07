/**
 * Mutation verification for tests/unit/unresolved-headline.test.ts.
 *
 *   node docs/audits/v1-timeout/mutation/reorder-hero.mjs apply
 *   node docs/audits/v1-timeout/mutation/reorder-hero.mjs revert
 *
 * `apply` moves the unresolved-corridor branch BELOW the "Road cut off" branch,
 * which is the change the ordering test exists to catch: it compiles, it reads
 * as tidying in a diff, and it silently restores the false claim for every
 * closure whose corridor search does not finish.
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const FILE = fileURLToPath(
  new URL('../../../../apps/web/src/inspector/ResultView.tsx', import.meta.url),
);

const START = "    if (corridor && statusKindOf(corridor.status) === 'fault') {";
const AFTER_POCKET = '    /* 3. Something really is stranded. */';
const END_OF_POCKET = '    /* 4. None of those.';

const mode = process.argv[2];
let src = readFileSync(FILE, 'utf8');
const eol = src.includes('\r\n') ? '\r\n' : '\n';
const lines = src.split(eol);

const startAt = lines.findIndex((l) => l === START);
if (startAt === -1) throw new Error('unresolved branch not found in its expected form');

if (mode === 'apply') {
  // The branch runs from its `if` to the closing `}` of its return.
  let end = startAt;
  let depth = 0;
  for (; end < lines.length; end += 1) {
    depth += (lines[end].match(/\{/g) || []).length;
    depth -= (lines[end].match(/\}/g) || []).length;
    if (depth === 0 && end > startAt) break;
  }
  const block = lines.splice(startAt, end - startAt + 1);
  // Drop the blank line the block left behind, then reinsert after the pocket
  // branch, immediately before comment 4.
  if (lines[startAt] === '') lines.splice(startAt, 1);
  const insertAt = lines.findIndex((l) => l.startsWith(END_OF_POCKET));
  if (insertAt === -1) throw new Error('could not find the pocket branch to move past');
  lines.splice(insertAt, 0, ...block, '');
  writeFileSync(FILE, lines.join(eol));
  console.log('mutation applied: unresolved branch moved below "Road cut off"');
} else if (mode === 'revert') {
  throw new Error('revert with git checkout; this mutation is not reversible in place');
} else {
  console.error('usage: reorder-hero.mjs apply   (revert with git checkout)');
  console.error(`anchors: ${AFTER_POCKET}`);
  process.exit(2);
}
