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
import { ENGINE_SWITCH_VISIBLE, type Engine } from './shell/EngineSwitch.js';
import LayerRail, { type MapLayerState } from './shell/LayerRail.js';
import MapWorkspace from './shell/MapWorkspace.js';
import TopBar from './shell/TopBar.js';
import NetworkMap, {
  type GeometryWarning,
  type HoverInfo,
  type MapResult,
  type ScaleReading,
} from './map/NetworkMap.js';
import { hasLinzKey } from './map/style.js';
import { coverageOf } from './api/coverage.js';
import AboutDialog from './shell/AboutDialog.js';
import { BasemapUnavailable } from './inspector/ResultNotices.js';
import { availabilityOf, normaliseDirection } from './state/direction.js';
import InspectorActions from './inspector/InspectorActions.js';
import ResultView from './inspector/ResultView.js';
import V2Preview from './inspector/V2Preview.js';
import type { DirectionView } from './inspector/DirectionTabs.js';
import { UnsupportedScopeError } from './api/client.js';
import {
  DEFAULT_SCENARIO,
  DEFAULT_SCENARIO_V2,
  closureLabel,
  closureLabelShort,
  scopeOfResponse,
  type DirectionKey,
  type Scenario,
} from './api/scenario.js';
import type { LinkSummary } from './api/types.js';
import {
  resultVersion,
  useBoundaryAnalysisV2,
  useClosureAnalysisV2,
  useDetour,
  useMetadata,
  useRoadSearch,
  useV2Capabilities,
  v2ResultVersion,
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
  /* Increments once per user selection. History keys on this rather than on the
   * link identifier, which changes shape when a numeric id resolves to an AMDS
   * id for the same road. */
  const [selectionEpoch, setSelectionEpoch] = useState(0);
  /* The snapshot a permalink asked for, if it named one. Cleared on any fresh
   * selection, because that is no longer the saved result. */
  const [urlSnapshot, setUrlSnapshot] = useState<string | null>(
    initial.snapshot,
  );
  const [aboutOpen, setAboutOpen] = useState(false);
  const [geometryWarning, setGeometryWarning] =
    useState<GeometryWarning | null>(null);
  const [basemapFailed, setBasemapFailed] = useState(false);
  /* Bumped to send the map back to the snapshot's full extent. */
  const [goHome, setGoHome] = useState(0);
  const [locate, setLocate] = useState<{
    lon: number;
    lat: number;
    seq: number;
  } | null>(null);
  const [sheetStop, setSheetStop] = useState<SheetStop>('medium');
  const [sheetPx, setSheetPx] = useState(() =>
    sheetHeight('medium', window.innerHeight),
  );
  const [layers, setLayers] = useState<MapLayerState>({
    network: true,
    basemap: true,
    labels: true,
  });
  /*
   * V1 IS THE DEFAULT, in development as well as in production. The switch
   * that changes this is compiled out of a production build; see
   * shell/EngineSwitch.tsx.
   */
  const [engine, setEngine] = useState<Engine>('v1');
  const v2 = ENGINE_SWITCH_VISIBLE && engine === 'v2';

  /* ------------------------------------------------------------- data */

  const mobile = useIsMobile();
  const laptop = useIsLaptop();

  const metaQ = useMetadata();
  const meta = metaQ.data ?? null;
  const version = resultVersion(metaQ.data);
  /* What the snapshot covers, and therefore what the map opens on and what
   * Home returns to. Read from the backend, never inferred from a boolean. */
  const coverage = useMemo(() => coverageOf(meta), [meta]);

  const detourQ = useDetour({ link, scenario, version }, !v2);
  const detour = detourQ.data ?? null;

  /* Only fetched when V2 is selected: under V1 nothing reads it, and an
   * unconditional request would put a V2 call in every session. */
  const v2CapsQ = useV2Capabilities(v2);
  const v2Caps = v2CapsQ.data ?? null;

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

  /* ------------------------------------------- direction normalisation */

  /*
   * A one-way link has no reverse result, and the URL defaults to reverse. Left
   * alone that produces a panel with the Reverse tab selected, the Reverse tab
   * disabled, no hero, no route, and no keyboard-focusable tab to escape with.
   *
   * So the focus follows what the response can actually show. The decision is
   * in state/direction.ts, pure and tested; this only applies it and remembers
   * what to announce.
   */
  const available = useMemo(
    () => availabilityOf({ forward: detour?.forward, reverse: detour?.reverse }),
    [detour],
  );

  /*
   * DERIVED, not an effect that writes back to state.
   *
   * An effect version of this was wrong in a way the browser tests caught:
   * after Back restored a URL asking for an unavailable direction, the effect's
   * dependencies (the result, the availability) had not changed, so it never
   * re-ran and the panel returned to the blank state it was written to prevent.
   *
   * Deriving it on every render means the displayed direction cannot disagree
   * with what the response can show, whatever route the request came in by —
   * fresh load, map click, permalink, or history navigation.
   */
  const normalised = useMemo(
    () => normaliseDirection(view, available),
    [view, available],
  );
  const effectiveView = detour ? normalised.view : view;
  const directionNotice = detour && normalised.changed
    ? normalised.announcement
    : null;

  /* --------------------------------------------------------- the URL */

  /*
   * The link in the URL is whatever we have: an AMDS id once the result
   * resolves, the internal numeric id a map click produced before that.
   */
  const urlLink =
    detour?.selectedLink.amdsId ?? (link === null ? null : String(link));

  const urlState = useMemo(
    () => ({
      link: urlLink,
      scenario,
      focus: (effectiveView === 'compare' ? 'reverse' : effectiveView) as DirectionKey,
      compare: effectiveView === 'compare',
      snapshot: detour?.snapshotId ?? meta?.snapshotId ?? null,
    }),
    [urlLink, scenario, effectiveView, detour, meta],
  );

  /*
   * HISTORY: one entry per user selection, never two.
   *
   * A map click selects an internal numeric link id, which goes into the URL
   * immediately. When the result lands, the same road is now identified by its
   * AMDS id — a different string for the same selection. Comparing strings, as
   * this used to, saw a change and pushed a second entry, so Back returned to
   * the same road in its numeric form before returning to the previous road.
   *
   * The fix is to key history on a selection *epoch* rather than on the
   * identifier. The epoch increments only when the user picks something; every
   * other change to the URL, including the numeric-to-AMDS canonicalisation, is
   * a replace.
   */
  /*
   * Starts at the initial epoch, NOT -1.
   *
   * With -1 the very first write after mount counted as a new selection and
   * pushed, adding an entry on top of the one the navigation had already
   * created. Back then returned to the same road, which is precisely the
   * double-entry symptom this mechanism exists to prevent — the app was
   * causing it at load as well as on click.
   */
  const lastEpoch = useRef(0);
  useEffect(() => {
    const mode = selectionEpoch !== lastEpoch.current ? 'push' : 'replace';
    lastEpoch.current = selectionEpoch;
    writeUrl(urlState, mode);
  }, [urlState, selectionEpoch]);

  /* Back and Forward restore state rather than leaving the page. */
  useEffect(() => {
    const onPop = () => {
      const s = readUrl();
      setLink(s.link);
      setScenario(s.scenario);
      setView(s.compare ? 'compare' : s.focus);
      setUrlSnapshot(s.snapshot);
      /* A popstate is a move within existing history, so the next write must
       * not push another entry on top of it. */
      setSelectionEpoch((e) => {
        lastEpoch.current = e;
        return e;
      });
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
    /* One epoch bump per user selection — this is what makes it one history
     * entry regardless of how the identifier is later canonicalised. */
    setSelectionEpoch((e) => e + 1);
    setUrlSnapshot(null);
  }, []);

  const onPickLink = useCallback(
    (id: number) => selectLink(id, null),
    [selectLink],
  );

  const onSelectSearchResult = useCallback(
    (l: LinkSummary) => {
      /* The label, not the raw name: it is what the row the user just clicked
       * said, and the panel must not change wording on the way in. */
      selectLink(l.amdsId, l.displayLabel ?? l.roadName);
      setQuery('');
      setDebounced('');
      /* Move the map now, not when the detour lands. On a national snapshot
       * the chosen road may be on the other island. */
      if (
        typeof l.centroid?.lon === 'number' &&
        typeof l.centroid?.lat === 'number'
      ) {
        setLocate((prev) => ({
          lon: l.centroid.lon,
          lat: l.centroid.lat,
          seq: (prev?.seq ?? 0) + 1,
        }));
      }
    },
    [selectLink],
  );

  const clear = useCallback(() => {
    setLink(null);
    setPendingName(null);
  }, []);

  /*
   * Switching engine switches the default question with it.
   *
   * V2 defaults to the selected segment, which is what the user pointed at;
   * V1 cannot close that and defaults to the whole AMDS source feature.
   * Carrying one engine's scope across to the other would either fail loudly
   * (segment under V1) or silently answer a different question, so the
   * scenario is reset to the default the chosen engine is built around.
   */
  const onEngineChange = useCallback((next: Engine) => {
    setEngine(next);
    setScenario(next === 'v2' ? DEFAULT_SCENARIO_V2 : DEFAULT_SCENARIO);
  }, []);

  /* ------------------------------------------------------ map result */

  const focusKey: DirectionKey = effectiveView === 'compare' ? 'reverse' : effectiveView;

  /*
   * The V2 request.
   *
   * Keyed on the V2 engine's own versions rather than the V1 metadata block:
   * the two version independently, and a V2 algorithm change against an
   * unchanged snapshot must invalidate every V2 figure in the cache.
   */
  const v2Q = useClosureAnalysisV2(
    {
      link,
      scenario,
      direction: focusKey,
      version: v2ResultVersion(v2CapsQ.data),
    },
    v2,
  );
  const analysis = v2Q.data ?? null;

  /* The boundary-movement request, on its own query key. See queries.ts for
   * why it must never share a cache entry with the endpoint analysis above. */
  const boundaryQ = useBoundaryAnalysisV2(
    {
      link,
      scenario,
      direction: focusKey,
      version: v2ResultVersion(v2CapsQ.data),
    },
    v2,
  );
  const boundary = boundaryQ.data ?? null;

  /*
   * What the map draws under the V2 engine.
   *
   * Built from the boundary result rather than the V1 one. Under V2 the panel
   * is describing the boundary measure, and drawing V1's route beside V2's
   * figures would attribute one engine's line to the other's numbers.
   *
   * The geometry arrives as a MultiLineString of contiguous PIECES and is
   * expanded into one LineString feature per piece. That is what lets the
   * map's existing gap handling work on it: it merges consecutive features
   * only where they actually meet, and turns the reveal animation off where
   * they do not. Handing it one flattened line would draw straight across
   * every gap, which is the single thing this must never do.
   */
  const v2MapResult: MapResult | null = useMemo(() => {
    if (!v2 || !boundary) return null;
    const pieces = (g?: { geometry: GeoJSON.MultiLineString | null }) =>
      g?.geometry
        ? ({
            type: 'FeatureCollection',
            features: g.geometry.coordinates.map((coordinates, i) => ({
              type: 'Feature',
              geometry: { type: 'LineString', coordinates },
              properties: { piece: i },
            })),
          } as GeoJSON.FeatureCollection)
        : null;
    return {
      closure: pieces(
        boundary.geometry?.closure ?? boundary.geometry?.selectedSegment,
      ),
      focus: pieces(boundary.geometry?.replacement),
      compare: null,
      corridor: null,
      stranded: null,
      /* Framed on the closure AND the replacement together, computed from the
       * drawn pieces. The V2 endpoint returns no bounds of its own, and with
       * none the map stays wherever it was - which on a national snapshot
       * means the answer is drawn somewhere off screen. */
      fitBounds: boundsOf([
        boundary.geometry?.closure,
        boundary.geometry?.selectedSegment,
        boundary.geometry?.replacement,
      ]),
      /* Carries the engine name, so switching engines cannot replay a V1
       * reveal over a V2 line. */
      revealKey: `v2b:${boundary.snapshotId}:${boundary.linkId}:${scenario.metric}:${scenario.vehicle}:${scenario.closureScope}`,
    };
  }, [v2, boundary, scenario]);

  const mapResult: MapResult | null = useMemo(() => {
    if (!detour) return null;
    const focused = detour[focusKey];
    const other = detour[focusKey === 'reverse' ? 'forward' : 'reverse'];

    return {
      closure: detour.closure.geoJson ?? null,
      focus: focused?.routeGeoJson ?? null,
      /* The comparison route is only drawn in Compare. In single-direction
       * view a second route would compete with the answer. */
      compare: effectiveView === 'compare' ? (other?.routeGeoJson ?? null) : null,
      corridor: null,
      /* The links that lose connectivity, in amber. Never a hull around them:
       * the engine identifies links, not a catchment. */
      stranded: focused?.isolation?.linkGeoJson ?? null,
      fitBounds: detour.fitBounds,
      revealKey: `${detour.snapshotId}:${detour.selectedLink.linkId}:${focusKey}:${scenario.metric}:${scenario.vehicle}:${scenario.closureScope}`,
    };
  }, [detour, focusKey, effectiveView, scenario]);

  /*
   * The legend names what was actually closed.
   *
   * It used to read "Closed segment" regardless of scope, which was wrong on
   * two counts: the engine removes an AMDS source feature, not a segment, and
   * "closed" without qualification reads as a live road status.
   */
  const legend = useMemo<LegendItem[]>(() => {
    const scope = detour ? scopeOfResponse(detour.closure.scope) : null;
    const items: LegendItem[] = [
      {
        colour: palette.closure,
        label: scope ? closureLabel(scope) : 'Modelled closure',
      },
    ];
    if (detour) {
      items.push({
        colour: palette.route,
        label: `Replacement path — ${focusKey}`,
      });
      if (effectiveView === 'compare') {
        items.push({
          colour: palette.compare,
          label: focusKey === 'reverse' ? 'Forward direction' : 'Reverse direction',
          dashed: true,
        });
      }
      /* Only when links are genuinely stranded. A DISCONNECTED one-way result
       * routinely strands nothing, and a legend entry for an absent layer
       * claims the map is showing something it is not. */
      /* Only when stranded links are actually drawn. A legend entry for a
       * layer with no data tells the reader to look for something that is not
       * there. */
      const iso = detour[focusKey]?.isolation;
      if (iso?.linkGeoJson?.features?.length) {
        items.push({ colour: palette.stranded, label: 'Stranded links' });
      }
    }
    items.push({ colour: palette.mapHighway, label: 'State highway' });
    return items;
  }, [detour, focusKey, effectiveView]);

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

  /*
   * SNAPSHOT MISMATCH.
   *
   * The URL records a snapshot so a historical link cannot silently produce
   * different numbers later. But the API has no snapshot parameter yet — every
   * request answers from the backend's active snapshot — so a permalink from an
   * older snapshot is recalculated rather than reproduced.
   *
   * Recalculating is not wrong. Presenting the recalculated figures as if they
   * were the saved result would be, so the mismatch is stated instead.
   */
  const activeSnapshot = detour?.snapshotId ?? meta?.snapshotId ?? null;
  const snapshotMismatch =
    urlSnapshot && activeSnapshot && urlSnapshot !== activeSnapshot
      ? { requested: urlSnapshot, active: activeSnapshot }
      : null;

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
      <InspectorEmpty coverage={coverage} />
    ) : v2 ? (
      <V2Preview
        analysis={analysis}
        boundary={boundary}
        boundaryLoading={boundaryQ.isPending}
        boundaryError={(boundaryQ.error as Error) ?? null}
        onBoundaryRetry={() => boundaryQ.refetch()}
        capabilities={v2Caps}
        meta={meta}
        pendingName={pendingName}
        loading={v2Q.isPending}
        error={(v2Q.error as Error) ?? null}
        scenario={scenario}
        onScenarioChange={setScenario}
        onClear={clear}
        onRetry={() => v2Q.refetch()}
      />
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
        view={effectiveView}
        onViewChange={setView}
        onClear={clear}
        snapshotMismatch={snapshotMismatch}
        geometryWarning={geometryWarning}
        directionNotice={directionNotice}
      />
    );

  {
    /* "Modelled closure", never "Closure active": nothing here observes a road
     * being closed, and a screenshot of the latter is one step from being read
     * as a live incident feed. */
  }
  const closureBadge = detour ? (
    <div className="map-badge">
      <span className="dot" />
      <span>
        {closureLabelShort(scopeOfResponse(detour.closure.scope))} —{' '}
        {detour.closure.removedLinkCount}{' '}
        {detour.closure.removedLinkCount === 1 ? 'link' : 'links'},{' '}
        {detour.closure.removedArcCount} directed arcs
      </span>
    </div>
  ) : null;

  return (
    <>
    <AboutDialog
      open={aboutOpen}
      onClose={() => setAboutOpen(false)}
      meta={meta}
    />
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
          engine={engine}
          onEngineChange={onEngineChange}
        />
      }
      rail={
        <LayerRail
          layers={layers}
          onToggle={(id) => setLayers((l) => ({ ...l, [id]: !l[id] }))}
          onAbout={() => setAboutOpen(true)}
          onHome={() => {
            /* Clearing the selection as well, so Home means "show me
             * everything" rather than "keep this result but look away". */
            clear();
            setGoHome((n) => n + 1);
          }}
          homeLabel={coverage.name}
        />
      }
      workspace={
        <MapWorkspace
          closureBadge={closureBadge}
          legend={legend}
          scale={scale}
          attribution={
            meta?.attribution ??
            'Contains data sourced from the NZTA Waka Kotahi AMDS Network Model'
          }
          basemapAttribution={
            hasLinzKey() ? 'Basemap © LINZ, CC BY 4.0' : ''
          }
        >
          <NetworkMap
            snapshotId={meta?.snapshotId ?? null}
            tileSchemaVersion={meta?.tileSchemaVersion ?? 2}
            result={v2 ? v2MapResult : mapResult}
            onPickLink={onPickLink}
            onHoverChange={setHover}
            onScaleChange={setScale}
            onReady={() => undefined}
            onGeometryWarning={setGeometryWarning}
            onBasemapError={() => setBasemapFailed(true)}
            homeExtent={coverage.extent}
            goHomeSignal={goHome}
            locate={locate}
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
                  /* Not under a heading that already is the route number:
                   * with no street name the label falls back to it. */
                  hover.roadNumber && hover.roadNumber !== hover.name
                    ? hover.roadNumber
                    : null,
                  hover.stateHighway ? 'State highway' : null,
                  inlineMetres(hover.lengthM),
                  hover.oneway ? 'one-way' : null,
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </span>
            </div>
          )}

          {basemapFailed && (
            <BasemapUnavailable onDismiss={() => setBasemapFailed(false)} />
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
    </>
  );
}

export { INSPECTOR_MIN };

/**
 * A bounding box over several route geometries, or null if none has any.
 *
 * Computed from the DRAWN pieces rather than from a separate extent field, so
 * the frame can never include ground that is not on screen — a gapped route
 * whose pieces are far apart frames all of them, and nothing between.
 */
function boundsOf(
  geoms: ({ geometry: GeoJSON.MultiLineString | null } | undefined)[],
): [number, number, number, number] | null {
  let west = Infinity;
  let south = Infinity;
  let east = -Infinity;
  let north = -Infinity;
  for (const g of geoms) {
    for (const piece of g?.geometry?.coordinates ?? []) {
      for (const [lon, lat] of piece) {
        if (lon < west) west = lon;
        if (lon > east) east = lon;
        if (lat < south) south = lat;
        if (lat > north) north = lat;
      }
    }
  }
  return Number.isFinite(west) ? [west, south, east, north] : null;
}
