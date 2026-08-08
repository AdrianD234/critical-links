/**
 * Capture ordinary V1 detour responses from the running API, for a byte-for-byte
 * comparison across the fix.
 *
 *   node capture.mjs <before|after> <snapshot-label>
 *
 * The API must already be serving the snapshot, with NO squeezed budget:
 *   bash docs/audits/v1-timeout/start-api.sh none
 *
 * Three fields are normalised, and only three. Each is a clock or a stopwatch,
 * none is a result, and leaving them in would make every capture differ from
 * every other capture of the same request:
 *
 *   calculatedAtUtc   when this response was computed
 *   retrievedAtUtc    when the snapshot was ingested
 *   runtimeMs         how long the search took
 *
 * Everything else - every status, every metric, every flag, every coordinate,
 * every string - is compared exactly as served.
 */
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const [phase, label] = process.argv.slice(2);
if (!phase || !label) {
  console.error('usage: node capture.mjs <before|after> <snapshot-label>');
  process.exit(2);
}

const API = process.env.NZCL_API_URL ?? 'http://127.0.0.1:8010';
const dir = fileURLToPath(new URL(`./${phase}/`, import.meta.url));
mkdirSync(dir, { recursive: true });

const VOLATILE = new Set(['calculatedAtUtc', 'retrievedAtUtc', 'runtimeMs']);

/** Stable key order, and the three clocks blanked. */
function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') {
    const out = {};
    for (const k of Object.keys(value).sort()) {
      out[k] = VOLATILE.has(k) ? '<normalised>' : canonical(value[k]);
    }
    return out;
  }
  return value;
}

/* The API runs in WSL; port forwarding to the Windows loopback can lag a
 * rebind by a second or two, so wait for it rather than racing it. */
async function waitForApi() {
  for (let i = 0; i < 60; i += 1) {
    try {
      const r = await fetch(`${API}/health`);
      if (r.ok) return;
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw new Error(`API never became reachable at ${API}`);
}
await waitForApi();

const meta = await (await fetch(`${API}/api/v1/network/metadata`)).json();
console.log(`snapshot: ${meta.snapshotId}`);

const search = await (
  await fetch(`${API}/api/v1/links/search?name=&limit=500`)
).json();

/*
 * Which links. All of them when the snapshot is small; otherwise the bypass and
 * its ramps - the links whose closure actually reaches the corridor search -
 * plus a deterministic slice of the grid so the ordinary OK path is covered too.
 */
const all = search.results.map((r) => r.amdsId);
const chosen =
  all.length <= 20
    ? all
    : [
        ...['CONN_W', 'EB1', 'EB2', 'CONN_E'].filter((id) => all.includes(id)),
        ...all.filter((id) => /^(H|V)/.test(id)).sort().slice(0, 12),
      ];

let n = 0;
for (const amdsId of chosen) {
  for (const metric of ['distance', 'time']) {
    for (const scope of ['physical', 'directed']) {
      const q =
        `metric=${metric}&vehicle=car&closure_scope=${scope}` +
        `&direction=both&geometry=true&labels=true`;
      const res = await fetch(
        `${API}/api/v1/links/${encodeURIComponent(amdsId)}/detour?${q}`,
      );
      const body = canonical(await res.json());
      const name = `${label}__${amdsId}__${metric}__${scope}.json`.replace(
        /[^A-Za-z0-9_.-]/g,
        '_',
      );
      writeFileSync(dir + name, JSON.stringify(body, null, 2) + '\n', 'utf8');
      n += 1;
    }
  }
}
console.log(`${phase}: wrote ${n} captures for ${chosen.length} links`);
