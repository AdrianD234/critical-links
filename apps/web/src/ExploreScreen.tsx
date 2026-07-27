/*
 * The Explore screen: state, wiring and the rules about what may be shown when.
 *
 * TWO RULES GOVERN EVERYTHING HERE
 *
 *   Feedback precedes computation. Selecting a road highlights it, opens the
 *   inspector and clears the previous result synchronously, on the click. The
 *   number arrives when it arrives.
 *
 *   A stale number is worse than no number. A result that no longer matches the
 *   current controls is never presented as the answer. `staleResult` below is
 *   the mechanism: the query key contains the scenario, so a scenario change
 *   produces a key with no data, and the panel shows skeletons rather than the
 *   previous figures.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import ContextInspector, {
  INSPECTOR_MIN,
  InspectorEmpty,
} from './shell/ContextInspector.js';
import AppShell from './shell/AppShell.js';
import BottomSheet, { type SheetStop, sheetHeight } from './shell/BottomSheet.js';
import LayerRail, { type MapLayerState } from './shell/LayerRail.js';
import MapWorkspace from './shell/MapWorkspace.js';
import TopBar from './shell/TopBar.js';
import NetworkMap, {
  type HoverInfo,
  type MapResult,
  type ScaleReading,
} from './map/NetworkMap.js';
import InspectorActions from './inspector/InspectorActions.js';
import ResultView from './inspector/ResultView.js';
import type { DirectionView } from './inspector/DirectionTabs.js';
import { UnsupportedScopeError } from './api/client.js';
import { type DirectionKey, type Scenario } from './api/scenario.js';
import type { LinkSummary } from './api/types.js';
import {
  resultVersion,
  useDetour,
  useMetadata,
  useRoadSearch,
} from './state/queries.js';
import { permalinkFor, readUrl, writeUrl } from './state/url.js';
import { palette } from './styles/palette.js';
import { inlineMetres } from './lib/format.js';
import { useIsLaptop, useIsMobile } from './lib/useMediaQuery.js';

interface LegendItem {
  colour: string;
  label: string;
  dashed?: boolean;
}

const LEGEND_BASE: LegendItem[] = [
  { colour: palette.closure, label: 'Closed segment' },
];

export default function ExploreScreen() {
  const initial = useRef(readUrl()).current;

  const [link, setLink] = useState<string | number | null>(initial.link);
  const [pendingName, setPendingName] = useState<string | null>(null);
  const [scenario, setScenario] = useState<Scenario>(initial.scenario);
  const [view, setView] = useState<DirectionView>(
    initial.compare ? 'compare' : initial.focus,
  );
  const [scenarioOpen, setScenarioOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [debounced, setDebounced] = useState('');
  const [hover, setHover] = useState<HoverInfo | null>(null);
  const [scale, setScale] = useState<ScaleReading | null>(null);
  const [preview, setPreview] = useState<LinkSummary | null>(null);
  const [inspectorWidth, setInspectorWidth] = useState(400);
  const [sheetStop, setSheetStop] = useState<SheetStop>('medium');
  const [sheetPx, setSheetPx] = useState(() =>
    sheetHeight('medium', window.innerHeight),
  );
  const [layers, setLayers] = useState<MapLayerState>({
    network: true,
    basemap: true,
    labels: true,
    quality: false,
  });

  /* ------------------------------------------------------------- data */

  const mobile = useIsMobile();
  const laptop = useIsLaptop();

  const metaQ = useMetadata();
  const meta = metaQ.data ?? null;
  const version = resultVersion(metaQ.data);

  const detourQ = useDetour({ link, scenario, version });
  const detour = detourQ.data ?? null;

  /* Search is debounced at 180ms, per the interaction storyboard. */
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(query), 180);
    return () => window.clearTimeout(t);
  }, [query]);
  const searchQ = useRoadSearch(debounced, true);

  /*
   * The result on screen belongs to the *current* query key by construction:
   * changing the scenario changes the key, and a key with no cached data has
   * no data to show. `stale` is therefore only about the summary chip, which
   * says "recalculating" while a fetch for the new key is in flight.
   */
  const loading = detourQ.isPending && link !== null;
  const stale = loading && detourQ.isFetching;

  /* --------------------------------------------------------- the URL */

  const urlState = useMemo(
    () => ({
      link: detour?.selectedLink.amdsId ?? (link === null ? null : String(link)),
      scenario,
      focus: (view === 'compare' ? 'reverse' : view) as DirectionKey,
      compare: view === 'compare',
      snapshot: detour?.snapshotId ?? meta?.snapshotId ?? null,
    }),
    [detour, link, scenario, view, meta],
  );

  /* A new selection is a new history entry; changing a setting on the same
   * link replaces, so Back does not have to step through every toggle. */
  const lastLink = useRef(initial.link);
  useEffect(() => {
    const mode = urlState.link !== lastLink.current ? 'push' : 'replace';
    lastLink.current = urlState.link;
    writeUrl(urlState, mode);
  }, [urlState]);

  /* Back and Forward restore state rather than leaving the page. */
  useEffect(() => {
    const onPop = () => {
      const s = readUrl();
      setLink(s.link);
      setScenario(s.scenario);
      setView(s.compare ? 'compare' : s.focus);
      lastLink.current = s.link;
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  /* ------------------------------------------------------- selection */

  const selectLink = useCallback((id: string | number, name?: string | null) => {
    /* Synchronous, on the click: the previous result is cleared and the name
     * we already know is shown, so the panel is never blank or — worse —
     * showing the last road's numbers under this road's name. */
    setLink(id);
    setPendingName(name ?? null);
    setHover(null);
  }, []);

  const onPickLink = useCallback(
    (id: number) => selectLink(id, null),
    [selectLink],
  );

  const onSelectSearchResult = useCallback(
    (l: LinkSummary) => {
      selectLink(l.amdsId, l.roadName);
      setQuery('');
      setDebounced('');
    },
    [selectLink],
  );

  const clear = useCallback(() => {
    setLink(null);
    setPendingName(null);
  }, []);

  /* ------------------------------------------------------ map result */

  const focusKey: DirectionKey = view === 'compare' ? 'reverse' : view;

  const mapResult: MapResult | null = useMemo(() => {
    if (!detour) return null;
    const focused = detour[focusKey];
    const other = detour[focusKey === 'reverse' ? 'forward' : 'reverse'];

    return {
      closure: detour.closure.geoJson ?? null,
      focus: focused?.routeGeoJson ?? null,
      /* The comparison route is only drawn in Compare. In single-direction
       * view a second route would compete with the answer. */
      compare: view === 'compare' ? (other?.routeGeoJson ?? null) : null,
      corridor: null,
      stranded: null,
      fitBounds: detour.fitBounds,
      revealKey: `${detour.snapshotId}:${detour.selectedLink.linkId}:${focusKey}:${scenario.metric}:${scenario.vehicle}:${scenario.closureScope}`,
    };
  }, [detour, focusKey, view, scenario]);

  const legend = useMemo<LegendItem[]>(() => {
    const items: LegendItem[] = [...LEGEND_BASE];
    if (detour) {
      items.push({
        colour: palette.route,
        label: `Replacement path — ${focusKey}`,
      });
      if (view === 'compare') {
        items.push({
          colour: palette.compare,
          label: focusKey === 'reverse' ? 'Forward direction' : 'Reverse direction',
          dashed: true,
        });
      }
      if (detour[focusKey]?.status === 'DISCONNECTED') {
        items.push({ colour: palette.stranded, label: 'Stranded links' });
      }
    }
    items.push({ colour: palette.mapHighway, label: 'State highway' });
    return items;
  }, [detour, focusKey, view]);

  /* ---------------------------------------------------------- export */

  const onExport = useCallback(() => {
    if (!detour) return;
    const blob = new Blob([JSON.stringify(detour, null, 2)], {
      type: 'application/geo+json',
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `nzcl-${detour.selectedLink.linkId}-${detour.snapshotId}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }, [detour]);

  /* ----------------------------------------------------------- error */

  const error = detourQ.error;
  const unsupported = error instanceof UnsupportedScopeError;

  const permalink = permalinkFor(urlState);

  /* ------------------------------------------------------------ view */

  /* The inspector's content and footer are identical on every size; only the
   * container differs. Built once here so the desktop column and the mobile
   * sheet cannot drift apart. */
  const actions = (
    <InspectorActions
      permalink={permalink}
      onExport={onExport}
      canExport={Boolean(detour)}
    />
  );

  const body =
    link === null ? (
      <InspectorEmpty clipped={Boolean(meta?.clippedExtract)} />
    ) : error ? (
      <div className="inspector-empty">
        <h2>{unsupported ? 'Not available yet' : 'Analysis failed'}</h2>
        <p>{(error as Error).message}</p>
        {!unsupported && (
          <button type="button" className="pbtn" onClick={() => detourQ.refetch()}>
            Try again
          </button>
        )}
      </div>
    ) : (
      <ResultView
        detour={detour}
        meta={meta}
        pendingName={pendingName}
        loading={loading}
        stale={stale}
        scenario={scenario}
        onScenarioChange={setScenario}
        scenarioOpen={scenarioOpen}
        onScenarioToggle={() => setScenarioOpen((o) => !o)}
        view={view}
        onViewChange={setView}
        onClear={clear}
      />
    );

  const closureBadge = detour ? (
    <div className="map-badge">
      <span className="dot" />
      <span>
        Closure active — {detour.closure.removedLinkCount}{' '}
        {detour.closure.removedLinkCount === 1 ? 'link' : 'links'},{' '}
        {detour.closure.removedArcCount} directed arcs
      </span>
    </div>
  ) : null;

  return (
    <AppShell
      bottomInset={mobile ? sheetPx : 0}
      topBar={
        <TopBar
          mode="explore"
          onModeChange={() => undefined}
          meta={meta}
          query={query}
          onQueryChange={setQuery}
          searchState={{
            results: searchQ.data?.results ?? null,
            loading: searchQ.isFetching,
            error: searchQ.error ? String((searchQ.error as Error).message) : null,
          }}
          onSelectLink={onSelectSearchResult}
          onPreviewLink={setPreview}
          permalink={permalink}
          canExport={Boolean(detour)}
          onExport={onExport}
          onCopyFailed={() => undefined}
        />
      }
      rail={
        <LayerRail
          layers={layers}
          onToggle={(id) => setLayers((l) => ({ ...l, [id]: !l[id] }))}
          onAbout={() => setScenarioOpen(true)}
        />
      }
      workspace={
        <MapWorkspace
          closureBadge={closureBadge}
          legend={legend}
          scale={scale}
          attribution={
            meta?.attribution ??
            'Basemap © LINZ, CC BY 4.0 · Contains data sourced from the NZTA Waka Kotahi AMDS Network Model'
          }
        >
          <NetworkMap
            snapshotId={meta?.snapshotId ?? null}
            tileSchemaVersion={meta?.tileSchemaVersion ?? 2}
            result={mapResult}
            onPickLink={onPickLink}
            onHoverChange={setHover}
            onScaleChange={setScale}
            onReady={() => undefined}
            inset={
              mobile
                ? { right: 0, bottom: sheetPx }
                : { right: inspectorWidth, bottom: 0 }
            }
            previewLinkId={preview?.linkId ?? null}
            layersVisible={layers}
          />

          {hover && (
            <div
              className="hover-tip"
              style={{ left: hover.x + 14, top: hover.y + 14 }}
              aria-hidden="true"
            >
              <b>{hover.name}</b>
              <span>
                {[
                  hover.roadNumber || null,
                  hover.stateHighway ? 'State highway' : null,
                  inlineMetres(hover.lengthM),
                  hover.oneway ? 'one-way' : null,
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </span>
            </div>
          )}
        </MapWorkspace>
      }
      inspector={
        mobile ? (
          <BottomSheet
            label="Closure result"
            stop={sheetStop}
            onStopChange={setSheetStop}
            onHeightChange={setSheetPx}
            footer={link !== null ? actions : undefined}
          >
            {body}
          </BottomSheet>
        ) : (
          <ContextInspector
            width={inspectorWidth}
            onWidthChange={setInspectorWidth}
            /* The grip needs room to drag into. On a laptop the map is already
             * the tighter of the two, so the column is fixed there. */
            resizable={!laptop}
            footer={link !== null ? actions : undefined}
          >
            {body}
          </ContextInspector>
        )
      }
    />
  );
}

export { INSPECTOR_MIN };
