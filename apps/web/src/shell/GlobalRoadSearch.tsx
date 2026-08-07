/*
 * Global road search.
 *
 * Implements the ARIA 1.2 combobox-with-listbox pattern rather than a div that
 * happens to respond to clicks: the input owns `role="combobox"`, the popup is
 * a real `listbox`, and the highlighted candidate is communicated through
 * `aria-activedescendant` so focus never leaves the input. Arrow keys move the
 * highlight, Enter selects, Escape closes and restores.
 *
 * Highlighting a candidate previews it on the map *before* selection, per the
 * interaction storyboard — feedback precedes computation.
 */

import { useEffect, useId, useRef, useState } from 'react';

import { SearchIcon } from './icons.js';
import type { LinkSummary } from '../api/types.js';
import { displayName } from '../naming.js';

export interface SearchState {
  results: LinkSummary[] | null;
  loading: boolean;
  error: string | null;
}

export default function GlobalRoadSearch({
  query,
  onQueryChange,
  state,
  onSelect,
  onPreview,
}: {
  query: string;
  onQueryChange: (q: string) => void;
  state: SearchState;
  onSelect: (link: LinkSummary) => void;
  onPreview: (link: LinkSummary | null) => void;
}) {
  const listId = useId();
  const optionId = (i: number) => `${listId}-opt-${i}`;
  const inputRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);

  const results = state.results ?? [];
  const expanded = open && query.trim().length > 0;

  /* "/" focuses search from anywhere, the convention for a map application.
   * Ignored while the user is already typing in a field. */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const typing =
        t &&
        (t.tagName === 'INPUT' ||
          t.tagName === 'TEXTAREA' ||
          t.isContentEditable);
      if (e.key === '/' && !typing) {
        e.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  /* A new result set invalidates the old highlight index. */
  useEffect(() => setActive(-1), [state.results]);

  function move(delta: number) {
    if (!results.length) return;
    const next = (active + delta + results.length) % results.length;
    setActive(next);
    onPreview(results[next] ?? null);
    document
      .getElementById(optionId(next))
      ?.scrollIntoView({ block: 'nearest' });
  }

  function choose(link: LinkSummary) {
    onSelect(link);
    onPreview(null);
    setOpen(false);
    setActive(-1);
    inputRef.current?.blur();
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setOpen(true);
      move(1);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      move(-1);
    } else if (e.key === 'Enter') {
      const pick = results[active] ?? results[0];
      if (pick) {
        e.preventDefault();
        choose(pick);
      }
    } else if (e.key === 'Escape') {
      e.preventDefault();
      setOpen(false);
      onPreview(null);
      setActive(-1);
    }
  }

  return (
    <div className="omnibox">
      <div className="omnibox-field">
        <SearchIcon />
        <input
          ref={inputRef}
          type="text"
          role="combobox"
          aria-expanded={expanded}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={
            expanded && active >= 0 ? optionId(active) : undefined
          }
          aria-label="Search for a road by name, route number, RCA or AMDS id"
          placeholder="Search a road, route number, RCA or AMDS id"
          value={query}
          onChange={(e) => {
            onQueryChange(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
          /* A blur delay would race the click; instead the popup closes on
           * pointerdown of the option, which fires before blur. */
          onBlur={() => window.setTimeout(() => setOpen(false), 120)}
        />
        <span className="kbd" aria-hidden="true">
          /
        </span>
      </div>

      {expanded && (
        <div
          className="omnibox-results"
          id={listId}
          role="listbox"
          aria-label="Search results"
        >
          {state.loading && (
            <div className="omnibox-empty">Searching&hellip;</div>
          )}

          {!state.loading && state.error && (
            <div className="omnibox-empty">{state.error}</div>
          )}

          {!state.loading && !state.error && results.length === 0 && (
            <div className="omnibox-empty">
              No links matched. You can search by road name, route number, RCA
              or AMDS id.
            </div>
          )}

          {results.map((r, i) => (
            <button
              key={r.amdsId}
              id={optionId(i)}
              role="option"
              type="button"
              aria-selected={i === active}
              data-active={i === active}
              onMouseEnter={() => {
                setActive(i);
                onPreview(r);
              }}
              onMouseLeave={() => onPreview(null)}
              onPointerDown={(e) => {
                e.preventDefault();
                choose(r);
              }}
            >
              <span className="r-name">
                {displayName(r)}
                {/* The route number sits with the name, not in the metadata
                  * line: on a national snapshot it is often the only thing
                  * distinguishing two identically named roads. */}
                {r.roadNumber && <span className="r-num">{r.roadNumber}</span>}
              </span>
              <span className="r-meta">
                {[
                  /* Locality first — it is what a reader scans for when a
                   * dozen "Main Road" results come back from all over the
                   * country. The RCA is a territorial authority, so it names
                   * the district even though it is an ownership field, and it
                   * stands in where the backend reports no locality. */
                  r.locality ?? r.rca,
                  r.urbanRural,
                  formatMetres(r.lengthM),
                  r.oneway ? 'one-way' : 'two-way',
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function formatMetres(m: number): string {
  return `${Math.round(m).toLocaleString('en-NZ')} m`;
}
