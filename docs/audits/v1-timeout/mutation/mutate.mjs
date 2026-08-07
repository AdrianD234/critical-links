/**
 * Mutation verification for the timeout tests.
 *
 *   node docs/audits/v1-timeout/mutation/mutate.mjs apply
 *   node docs/audits/v1-timeout/mutation/mutate.mjs revert
 *
 * `apply` puts back the behaviour the fix removed: `route_many` swallowing
 * every database failure and returning an empty, resolved-looking result, so a
 * cancelled statement is indistinguishable from a graph with no route. The type
 * is left in place deliberately - the point is to prove the tests detect the
 * DEFECT and not merely the refactor, so they must fail on their assertions
 * rather than on an AttributeError.
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const FILE = fileURLToPath(
  new URL('../../../../python/src/nzcl/routing.py', import.meta.url),
);

const FIXED = [
  '    except Exception as exc:  # noqa: BLE001',
  '        # The same reading `_run_dijkstra` above has always applied to the',
  '        # single-pair search: a timeout is NOT a finding about the network.',
  '        if "statement timeout" in str(exc).lower() or "canceling" in str(exc).lower():',
  '            return ManyCostResult(',
  '                "UNRESOLVED_TIMEOUT",',
  '                detail=f"statement timeout after {statement_timeout_ms} ms")',
  '        return ManyCostResult("API_ERROR", detail=str(exc))',
].join('\n');

const MUTATED = [
  '    except Exception:  # noqa: BLE001 - MUTATION: the pre-fix behaviour',
  '        return ManyCostResult("OK")',
].join('\n');

const mode = process.argv[2];
const src = readFileSync(FILE, 'utf8');
const eol = src.includes('\r\n') ? '\r\n' : '\n';
const nl = (s) => s.split('\n').join(eol);

if (mode === 'apply') {
  if (!src.includes(nl(FIXED))) throw new Error('fixed form not found');
  writeFileSync(FILE, src.replace(nl(FIXED), nl(MUTATED)));
  console.log('mutation applied: route_many swallows every failure again');
} else if (mode === 'revert') {
  if (!src.includes(nl(MUTATED))) throw new Error('mutated form not found');
  writeFileSync(FILE, src.replace(nl(MUTATED), nl(FIXED)));
  console.log('mutation reverted');
} else {
  console.error('usage: mutate.mjs <apply|revert>');
  process.exit(2);
}
