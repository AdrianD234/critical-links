/**
 * The URL is the application's state.
 *
 * Every selection and setting is readable from and writable to the query
 * string, so any view is shareable and the browser's Back and Forward buttons
 * restore state rather than leaving the page.
 *
 * The snapshot id is recorded too. A permalink whose figures came from a
 * different snapshot than the one now active is not the same result, and the
 * app must be able to say so rather than silently recomputing and presenting
 * different numbers under the same link.
 *
 * `pushState` is used for a change of selection (a new thing to go Back from)
 * and `replaceState` for a change of scenario on the same link, which would
 * otherwise fill the history stack with every toggle of a radio button.
 *
 * ---------------------------------------------------------------------------
 * WHAT `v=2` IS FOR
 *
 * The URL has to say which engine's semantics it describes, because the scope
 * names alone cannot. `scope=amds-feature` means one thing in a link made
 * before the closure engine was promoted — it was the only default the old
 * engine had, and it measured between the closed feature's own two endpoints —
 * and something else in a link made after, where it is a deliberate advanced
 * choice measured across the closure boundary.
 *
 * Without a marker the two are indistinguishable, and the app would have to
 * guess. `v=2` is written on every URL this build produces; a URL carrying a
 * link and no `v=2` is a pre-promotion link and is treated as one.
 *
 * THE POLICY FOR A PRE-PROMOTION LINK IS TO MIGRATE IT, VISIBLY.
 *
 * The alternative was to refuse it and ask for the analysis to be re-run. That
 * was rejected: the link identifies the road unambiguously and that identity is
 * not in doubt, so refusing would strand every circulating link without telling
 * the reader anything they did not already know.
 *
 * What IS in doubt is the closure scope and the measure, and both are changing.
 * So the migration is explicit rather than silent: the scope is moved to the
 * current default — the segment the reader pointed at — and `readUrl` reports
 * what the link originally asked for so the interface can say so, once, and
 * offer to restore it. Quietly re-reading an old source-feature link as a
 * segment closure would change what was analysed while the link still looked
 * unchanged, which is the one outcome this must not produce.
 */

import {
  DEFAULT_SCENARIO,
  closureScopeFromWire,
  type ClosureScope,
  type DirectionKey,
  type Metric,
  type Scenario,
  type Vehicle,
} from '../api/scenario.js';

/**
 * Which closure method the map's clicks belong to.
 *
 *   link  the ordinary workflow: a click selects one graph link.
 *   span  the two-point outage editor: clicks place and drag A and B.
 *
 * A runtime choice, not a build one. The feature flag decides whether `span`
 * is AVAILABLE; this field records which method is ACTIVE, so a link permalink
 * reopens in link mode and a span permalink in span mode rather than the
 * build's flag deciding retroactively what a shared URL meant.
 */
export type ClosureTool = 'link' | 'span';

export interface ExploreUrlState {
  link: string | null;
  scenario: Scenario;
  /** Which direction the inspector is focused on. */
  focus: DirectionKey;
  /** True when both routes are shown together. */
  compare: boolean;
  /** The snapshot the permalink was created against, if it recorded one. */
  snapshot: string | null;
  /** The active closure method. Absent in the URL means `link`. */
  tool: ClosureTool;
}

/**
 * What a pre-promotion link asked for, and what it was changed to.
 *
 * Present only when the two differ. Equal values are not a migration and must
 * not produce a notice: a notice that fires when nothing changed teaches the
 * reader to dismiss the one that fires when something did.
 */
export interface LegacyMigration {
  requestedScope: ClosureScope;
  appliedScope: ClosureScope;
}

export interface RestoredUrlState extends ExploreUrlState {
  migration: LegacyMigration | null;
}

/** The semantics marker written on every URL this build produces. */
export const URL_SEMANTICS_VERSION = '2';

const SCOPES: ClosureScope[] = ['amds-feature', 'direction', 'segment'];

function oneOf<T extends string>(
  raw: string | null,
  allowed: readonly T[],
  fallback: T,
): T {
  return raw && (allowed as readonly string[]).includes(raw)
    ? (raw as T)
    : fallback;
}

export function readUrl(search = window.location.search): RestoredUrlState {
  const p = new URLSearchParams(search);

  /*
   * `scope` accepts both vocabularies. Links made before the promotion may
   * carry the retired wire enum (`physical`/`directed`); ones made since carry
   * the product scope. Reading both means a permalink someone shared last
   * month still names a scope rather than falling silently to the default.
   */
  const rawScope = p.get('scope');
  const requestedScope: ClosureScope =
    rawScope === 'physical' || rawScope === 'directed'
      ? closureScopeFromWire(rawScope)
      : oneOf(rawScope, SCOPES, DEFAULT_SCENARIO.closureScope);

  const link = p.get('link');

  /*
   * A link with no semantics marker was made before the promotion. See the
   * header: the scope is migrated to the current default rather than honoured
   * as written, and what it asked for is reported so it can be disclosed.
   *
   * Only when a link is actually named. A bare `/` is not a stale permalink,
   * it is someone opening the application, and it has nothing to migrate.
   */
  const legacy = link !== null && p.get('v') !== URL_SEMANTICS_VERSION;
  const closureScope = legacy ? DEFAULT_SCENARIO.closureScope : requestedScope;
  const migration =
    legacy && requestedScope !== closureScope
      ? { requestedScope, appliedScope: closureScope }
      : null;

  /*
   * The active closure method. `tool=span` says it explicitly; a URL carrying
   * span state (`span=1`) but predating the tool field is read as span mode
   * too, so links shared from the first editor build keep restoring the
   * editor rather than silently reopening as the other workflow. Absent both,
   * `link` - every URL that existed before the editor did means link mode.
   */
  const tool: ClosureTool =
    p.get('tool') === 'span' || p.get('span') === '1' ? 'span' : 'link';

  return {
    link,
    scenario: {
      metric: oneOf<Metric>(
        p.get('metric'),
        ['distance', 'time'],
        DEFAULT_SCENARIO.metric,
      ),
      vehicle: oneOf<Vehicle>(
        p.get('vehicle'),
        ['car', 'heavy', 'emergency'],
        DEFAULT_SCENARIO.vehicle,
      ),
      closureScope,
    },
    focus: oneOf<DirectionKey>(p.get('focus'), ['forward', 'reverse'], 'reverse'),
    compare: p.get('compare') === '1',
    snapshot: p.get('snapshot'),
    tool,
    migration,
  };
}

export function buildSearch(s: ExploreUrlState): string {
  const p = new URLSearchParams();
  if (s.link) p.set('link', s.link);
  p.set('metric', s.scenario.metric);
  p.set('vehicle', s.scenario.vehicle);
  p.set('scope', s.scenario.closureScope);
  p.set('focus', s.focus);
  if (s.compare) p.set('compare', '1');
  if (s.snapshot) p.set('snapshot', s.snapshot);
  /* Written only when it departs from the default, so every pre-editor URL in
   * circulation stays byte-identical to what this build would produce for the
   * same state. */
  if (s.tool === 'span') p.set('tool', 'span');
  /* Last, so it reads as a property of the whole state rather than of the
   * setting next to it. Written unconditionally: a URL that omits it is, by
   * this build's own rule, a pre-promotion URL. */
  p.set('v', URL_SEMANTICS_VERSION);
  return `?${p.toString()}`;
}

/**
 * The keys this writer owns. It rewrites these and never anything else.
 *
 * It used to rebuild the whole query string from its own state, which was
 * correct while it was the only writer. The two-point outage editor added a
 * second one, keeping its own `span`/`s*` keys - and this writer, running on
 * every mount, silently deleted them. A shared span URL opened, lost its span
 * before the restore effect could read it, and came up as an empty editor
 * with nothing anywhere saying why.
 *
 * Preserving unknown keys is the general contract, not a span-specific patch:
 * any future feature that keeps state in the URL has the same need, and a
 * writer that discards what it does not recognise makes every such feature
 * a timing accident.
 */
const OWN_KEYS = new Set([
  'link', 'metric', 'vehicle', 'scope', 'focus', 'compare', 'snapshot', 'v',
  'tool',
]);

export function writeUrl(s: ExploreUrlState, mode: 'push' | 'replace') {
  const p = new URLSearchParams(buildSearch(s));
  for (const [key, value] of new URLSearchParams(window.location.search)) {
    if (!OWN_KEYS.has(key)) p.append(key, value);
  }
  const next = `?${p.toString()}`;
  if (next === window.location.search) return;
  const fn = mode === 'push' ? 'pushState' : 'replaceState';
  window.history[fn](null, '', next);
}

/** The absolute permalink for the current state. */
export function permalinkFor(s: ExploreUrlState): string | null {
  if (!s.link) return null;
  return `${window.location.origin}${window.location.pathname}${buildSearch(s)}`;
}
