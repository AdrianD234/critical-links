import { useCallback, useEffect, useState } from 'react';

import MapView from './MapView.js';
import ResultPanel from './ResultPanel.js';
import {
  api,
  type DetourResponse,
  type LinkSummary,
  type NetworkMetadata,
} from './api.js';

export interface Options {
  metric: 'distance' | 'time';
  vehicle: 'car' | 'heavy' | 'emergency';
  closureScope: 'physical' | 'directed';
  direction: 'forward' | 'reverse' | 'both';
}

const DEFAULTS: Options = {
  metric: 'distance',
  vehicle: 'car',
  closureScope: 'physical',
  direction: 'both',
};

/** Options and the selected link live in the URL, so any view is shareable. */
function readUrl(): { link: string | null; options: Options } {
  const p = new URLSearchParams(window.location.search);
  return {
    link: p.get('link'),
    options: {
      metric: (p.get('metric') as Options['metric']) || DEFAULTS.metric,
      vehicle: (p.get('vehicle') as Options['vehicle']) || DEFAULTS.vehicle,
      closureScope: (p.get('scope') as Options['closureScope']) || DEFAULTS.closureScope,
      direction: (p.get('direction') as Options['direction']) || DEFAULTS.direction,
    },
  };
}

function writeUrl(link: string | null, o: Options) {
  const p = new URLSearchParams();
  if (link) p.set('link', link);
  p.set('metric', o.metric);
  p.set('vehicle', o.vehicle);
  p.set('scope', o.closureScope);
  p.set('direction', o.direction);
  window.history.replaceState(null, '', `?${p}`);
}

export default function App() {
  const initial = readUrl();
  const [options, setOptions] = useState<Options>(initial.options);
  const [selected, setSelected] = useState<string | number | null>(initial.link);
  const [detour, setDetour] = useState<DetourResponse | null>(null);
  const [meta, setMeta] = useState<NetworkMetadata | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<LinkSummary[] | null>(null);

  useEffect(() => {
    api.metadata().then(setMeta).catch((e) => setError(String(e.message ?? e)));
  }, []);

  useEffect(() => {
    if (selected === null) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .detour(selected, options)
      .then((r) => {
        if (cancelled) return;
        setDetour(r);
        writeUrl(r.selectedLink.amdsId, options);
      })
      .catch((e) => {
        if (!cancelled) {
          setError(String(e.message ?? e));
          setDetour(null);
        }
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [selected, options]);

  const onSearch = useCallback(async () => {
    const q = query.trim();
    if (!q) return setResults(null);
    setError(null);
    try {
      const r = q.startsWith('{')
        ? await api.search({ amdsId: q, limit: 25 })
        : await api.search({ name: q, limit: 25 });
      setResults(r.results);
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }, [query]);

  const onPickLink = useCallback((id: number) => setSelected(id), []);

  const set = <K extends keyof Options>(k: K, v: Options[K]) =>
    setOptions((o) => ({ ...o, [k]: v }));

  return (
    <div className="app">
      <MapView
        detour={detour}
        onPickLink={onPickLink}
        showCorridor={options.direction !== 'forward'}
        snapshotId={meta?.snapshotId ?? null}
        tileSchemaVersion={meta?.tileSchemaVersion ?? 2}
        attribution={meta?.attribution ?? ''}
      />

      <div className="panel">
        <h1>NZ Critical Links</h1>
        <p className="sub">
          Close a road link and see the shortest replacement path available in
          the represented network.
          Structural resilience only &mdash; this does <strong>not</strong>{' '}
          predict how much traffic uses each alternative.
        </p>

        <div className="search">
          <input
            value={query}
            placeholder="Road name or AMDS id..."
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && onSearch()}
          />
          <button onClick={onSearch}>Search</button>
        </div>

        {results && (
          <div className="results">
            {results.length === 0 && <div className="note">No links matched.</div>}
            {results.map((r) => (
              <div
                key={r.amdsId}
                className="result"
                onClick={() => {
                  setSelected(r.amdsId);
                  setResults(null);
                }}
              >
                <div className="rn">{r.roadName ?? '(unnamed link)'}</div>
                <div className="meta">
                  {r.lengthM} m · {r.oneway ? 'one-way' : 'two-way'}
                  {r.rca ? ' · state highway' : ''}
                </div>
              </div>
            ))}
          </div>
        )}

        <div className="controls">
          <Toggle
            label="Metric"
            value={options.metric}
            options={[
              ['distance', 'Distance'],
              ['time', 'Time'],
            ]}
            onChange={(v) => set('metric', v as Options['metric'])}
          />
          <Toggle
            label="Vehicle"
            value={options.vehicle}
            options={[
              ['car', 'Car'],
              ['heavy', 'Heavy'],
              ['emergency', 'Emergency'],
            ]}
            onChange={(v) => set('vehicle', v as Options['vehicle'])}
          />
          <Toggle
            label="Closure"
            value={options.closureScope}
            options={[
              ['physical', 'AMDS feature'],
              ['directed', 'One direction'],
            ]}
            onChange={(v) => set('closureScope', v as Options['closureScope'])}
          />
          <Toggle
            label="Direction"
            value={options.direction}
            options={[
              ['both', 'Both'],
              ['forward', 'Forward'],
              ['reverse', 'Reverse'],
            ]}
            onChange={(v) => set('direction', v as Options['direction'])}
          />
        </div>

        {selected === null && !error && (
          <div className="card">
            <h2>Get started</h2>
            <div className="note">
              Click any road on the map, or search for one by name. The road you
              select is closed and the network is re-routed around it.
            </div>
          </div>
        )}

        <ResultPanel detour={detour} meta={meta} loading={loading} error={error} />
      </div>

      <div className="status-strip">
        {loading && <span className="pill loading">Calculating detour&hellip;</span>}
        {error && <span className="pill error">{error}</span>}
        {meta && !loading && !error && (
          <span className="pill">
            {meta.graph.links.toLocaleString()} links ·{' '}
            {meta.graph.arcs.toLocaleString()} arcs · snapshot {meta.snapshotId}
          </span>
        )}
      </div>
    </div>
  );
}

function Toggle({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: [string, string][];
  onChange: (v: string) => void;
}) {
  return (
    <div className="ctl-row">
      <span className="label">{label}</span>
      <span className="opts">
        {options.map(([v, text]) => (
          <button
            key={v}
            className={v === value ? 'on' : ''}
            onClick={() => onChange(v)}
          >
            {text}
          </button>
        ))}
      </span>
    </div>
  );
}
