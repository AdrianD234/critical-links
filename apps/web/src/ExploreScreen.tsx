/*
 * The Explore screen: state, wiring and the rules about what may be shown when.
 *
 * THREE RULES GOVERN EVERYTHING HERE
 *
 *   Feedback precedes computation. Selecting a road highlights it, opens the
 *   inspector and clears the previous result synchronously, on the click. The
 *   number arrives when it arrives.
 *
 *   A stale number is worse than no number. A result that no longer matches the
 *   current controls is never presented as the answer. The query key contains
 *   the scenario, so a scenario change produces a key with no data, and the
 *   panel shows skeletons rather than the previous figures.
 *
 *   ONE ENGINE ANSWERS. Every user-facing figure on this screen comes from the
 *   boundary-movement analysis. There is no second engine behind it and no
 *   fallback to one: the retired engine closes a different thing and measures
 *   between different points, so a number from it appearing when this one fails
 *   would answer a question nobody asked and conceal the failure that needed
 *   looking at. A failure here stays a failure here.
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
  type GeometryWarning,
  type HoverInfo,
  type MapResult,
  type ScaleReading,
} from './map/NetworkMap.js';
import { hasLinzKey } from './map/style.js';
import { coverageOf } from './api/coverage.js';
import AboutDialog from './shell/AboutDialog.js';
import { BasemapUnavailable } from './inspector/ResultNotices.js';
import InspectorActions from './inspector/InspectorActions.js';
import ClosureResultView from './inspector/ClosureResultView.js';
import {
  closureLabel,
  closureLabelShort,
  scopeOfResponse,
  type ClosureScope,
  type DirectionKey,
  type Scenario,
} from './api/scenario.js';
import type { LinkSummary, V2RouteGeometry } from './api/types.js';
import {
  useBoundaryAnalysisV2,
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
  /*
   * Which directed traversal is being withdrawn. Meaningful only under
   * `direction` scope, where a single directed closure has to name the one it
   * means; under the other scopes the engine ignores it and the request does
   * not send it. Kept in state and in the URL so a direction-scope permalink
   * still restores what it described.
   */
  const [focus, setFocus] = useState<DirectionKey>(initial.focus);
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
  /*
   * What a pre-promotion link asked for, if this session was opened from one.
   * Shown once and then dismissed; it is a statement about how this view was
   * arrived at, not a property of the result, so it must not reappear when the
   * scenario changes. See state/url.ts for the policy it reports.
   */
  const [migration, setMigration] = useState(initial.migration);
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

  /* ------------------------------------------------------------- data */

  const mobile = useIsMobile();
  const laptop = useIsLaptop();

  const metaQ = useMetadata();
  const meta = metaQ.data ?? null;
  /* What the snapshot covers, and therefore what the map opens on and what
   * Home returns to. Read from the backend, never inferred from a boolean. */
  const coverage = useMemo(() => coverageOf(meta), [meta]);

  /*
   * What the engine can do for this snapshot. Unconditional now: it is the
   * engine every session uses, so there is no longer a case in which asking is
   * a request nobody wanted. It is also what the scope control is built from,
   * and a control that guesses at its own options while a request is in flight
   * offers the wrong ones for the first second of every session.
   */
  const capsQ = useV2Capabilities(true);
  const caps = capsQ.data ?? null;

  /* Search is debounced at 180ms, per the interaction storyboard. */
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(query), 180);
    return () => window.clearTimeout(t);
  }, [query]);
  const searchQ = useRoadSearch(debounced, true);

  /*
   * The analysis.
   *
   * Keyed on the engine's own snapshot, algorithm and derivation versions: an
   * algorithm change against an unchanged snapshot must still invalidate every
   * figure in the cache, or a settings change would redisplay a number computed
   * by different code.
   */
  const analysisQ = useBoundaryAnalysisV2(
    {
      link,
      scenario,
      direction: focus,
      version: v2ResultVersion(capsQ.data),
    },
    true,
  );
  const analysis = analysisQ.data ?? null;

  /*
   * The result on screen belongs to the *current* query key by construction:
   * changing the scenario changes the key, and a key with no cached data has no
   * data to show. `stale` is therefore only about the summary chip, which says
   * "recalculating" while a fetch for the new key is in flight.
   */
  const loading = analysisQ.isPending && link !== null;
  const stale = loading && analysisQ.isFetching;

  /* --------------------------------------------------------- the URL */

  /*
   * The link in the URL is whatever we have: an AMDS id once the result
   * resolves, the internal numeric id a map click produced before that.
   */
  const urlLink =
    analysis?.selectedLink.amdsId ?? (link === null ? null : String(link));

  const urlState = useMemo(
    () => ({
      link: urlLink,
      scenario,
      focus,
      /* The forward/reverse comparison was a property of the endpoint measure,
       * which computed both directions of one link. This engine measures trips
       * across a boundary, where "the other direction" is a different crossing
       * rather than the same one reversed, so there is nothing to compare and
       * the flag is never set. It is still read, so an old link carrying it
       * does not fail to parse. */
      compare: false,
      snapshot: analysis?.snapshotId ?? meta?.snapshotId ?? null,
    }),
    [urlLink, scenario, focus, analysis, meta],
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
   *
   * Starts at the initial epoch, NOT -1. With -1 the very first write after
   * mount counted as a new selection and pushed, adding an entry on top of the
   * one the navigation had already created.
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
      setFocus(s.focus);
      setUrlSnapshot(s.snapshot);
      /* A history entry written by this build carries the semantics marker, so
       * going Back to one is not a migration. Going Back to an entry from
       * before the promotion is, and it is disclosed again — the reader is
       * looking at those figures afresh. */
      setMigration(s.migration);
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
    /* A fresh selection is not the link that was arrived on, so whatever that
     * link asked for no longer describes what is on screen. */
    setMigration(null);
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
      /* Move the map now, not when the result lands. On a national snapshot
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
    setMigration(null);
  }, []);

  /* Restore the closure scope a pre-promotion link asked for. */
  const onRestoreScope = useCallback((closureScope: ClosureScope) => {
    setScenario((s) => ({ ...s, closureScope }));
    setMigration(null);
  }, []);

  /* ------------------------------------------------------ map result */

  /*
   * What the map draws.
   *
   * The geometry arrives as a MultiLineString of contiguous PIECES and is
   * expanded into one LineString feature per piece. That is what lets the map's
   * existing gap handling work on it: it merges consecutive features only where
   * they actually meet, and turns the reveal animation off where they do not.
   * Handing it one flattened line would draw straight across every gap, which
   * is the single thing this must never do.
   */
  const mapResult: MapResult | null = useMemo(() => {
    if (!analysis) return null;
    const pieces = (g?: V2RouteGeometry) =>
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
      /* `closure` is emitted only when more was removed than the selected
       * segment. Under segment scope the two are the same thing, and the
       * fallback is what makes the closure visible there at all. */
      closure: pieces(
        analysis.geometry?.closure ?? analysis.geometry?.selectedSegment,
      ),
      focus: pieces(analysis.geometry?.replacement),
      compare: null,
      corridor: null,
      /* The links that lose access, in amber. Never a hull around them: the
       * engine identifies links, not a catchment. Null when the backend capped
       * the collection, because a truncated set drawn as if whole understates
       * the extent — the counts in the panel stay exact either way. */
      stranded: analysis.isolation?.separatedGeoJson ?? null,
      /* Framed on the closure AND the replacement together, computed from the
       * drawn pieces. The endpoint returns no bounds of its own, and with none
       * the map stays wherever it was — which on a national snapshot means the
       * answer is drawn somewhere off screen. */
      fitBounds: boundsOf([
        analysis.geometry?.closure,
        analysis.geometry?.selectedSegment,
        analysis.geometry?.replacement,
      ]),
      revealKey:
        `${analysis.snapshotId}:${analysis.linkId}:${scenario.metric}:` +
        `${scenario.vehicle}:${scenario.closureScope}`,
    };
  }, [analysis, scenario]);

  /*
   * The legend names what was actually closed.
   *
   * Read from the response rather than from the control: the response says what
   * was removed, and a control the reader has already moved on from must not
   * relabel a result computed under the previous one.
   */
  const legend = useMemo<LegendItem[]>(() => {
    const scope = analysis ? scopeOfResponse(analysis.closure.scope) : null;
    const items: LegendItem[] = [
      {
        colour: palette.closure,
        label: scope ? closureLabel(scope) : 'Modelled closure',
      },
    ];
    if (analysis?.geometry?.replacement?.geometry) {
      items.push({ colour: palette.route, label: 'Replacement route' });
    }
    /* Only when links are actually drawn. A legend entry for a layer with no
     * data tells the reader to look for something that is not there. */
    if (analysis?.isolation?.separatedGeoJson?.features?.length) {
      items.push({ colour: palette.stranded, label: 'Links losing access' });
    }
    items.push({ colour: palette.mapHighway, label: 'State highway' });
    return items;
  }, [analysis]);

  /* ---------------------------------------------------------- export */

  const onExport = useCallback(() => {
    if (!analysis) return;
    const blob = new Blob([JSON.stringify(analysis, null, 2)], {
      type: 'application/geo+json',
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `nzcl-${analysis.linkId}-${analysis.snapshotId}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }, [analysis]);

  /* ----------------------------------------------------------- error */

  const permalink = permalinkFor(urlState);

  /*
   * SNAPSHOT MISMATCH.
   *
   * The URL records a snapshot so a historical link cannot silently produce
   * different numbers later. But the API has no snapshot parameter — every
   * request answers from the backend's active snapshot — so a permalink from an
   * older snapshot is recalculated rather than reproduced.
   *
   * Recalculating is not wrong. Presenting the recalculated figures as if they
   * were the saved result would be, so the mismatch is stated instead.
   */
  const activeSnapshot = analysis?.snapshotId ?? meta?.snapshotId ?? null;
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
      canExport={Boolean(analysis)}
    />
  );

  const body =
    link === null ? (
      <InspectorEmpty coverage={coverage} />
    ) : (
      <ClosureResultView
        analysis={analysis}
        capabilities={caps}
        meta={meta}
        pendingName={pendingName}
        loading={loading}
        stale={stale}
        error={(analysisQ.error as Error) ?? null}
        onRetry={() => analysisQ.refetch()}
        scenario={scenario}
        onScenarioChange={setScenario}
        scenarioOpen={scenarioOpen}
        onScenarioToggle={() => setScenarioOpen((o) => !o)}
        onClear={clear}
        snapshotMismatch={snapshotMismatch}
        geometryWarning={geometryWarning}
        migration={migration}
        onDismissMigration={() => setMigration(null)}
        onRestoreScope={onRestoreScope}
      />
    );

  {
    /* "Modelled closure", never "Closure active": nothing here observes a road
     * being closed, and a screenshot of the latter is one step from being read
     * as a live incident feed. */
  }
  const closureBadge = analysis ? (
    <div className="map-badge">
      <span className="dot" />
      <span>
        {closureLabelShort(scopeOfResponse(analysis.closure.scope))} —{' '}
        {analysis.closure.removedLinkCount}{' '}
        {analysis.closure.removedLinkCount === 1 ? 'link' : 'links'},{' '}
        {analysis.closure.removedArcCount} directed arcs
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
          canExport={Boolean(analysis)}
          onExport={onExport}
          onCopyFailed={() => undefined}
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
            result={mapResult}
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
  geoms: (V2RouteGeometry | undefined)[],
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
