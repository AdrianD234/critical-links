/*
 * The dark compact top bar: brand, mode navigation, global search, snapshot
 * indicator and share/export actions.
 *
 * Composition only — every child owns its own behaviour. At the laptop
 * breakpoint the search box shrinks before anything else, so the top-bar
 * actions on the right are never clipped.
 */

import EngineSwitch, { type Engine } from './EngineSwitch.js';
import GlobalRoadSearch, { type SearchState } from './GlobalRoadSearch.js';
import ModeNavigation, { type Mode } from './ModeNavigation.js';
import ShareExportActions from './ShareExportActions.js';
import SnapshotIndicator from './SnapshotIndicator.js';
import { BrandMark } from './icons.js';
import type { LinkSummary, NetworkMetadata } from '../api/types.js';

export default function TopBar({
  mode,
  onModeChange,
  meta,
  query,
  onQueryChange,
  searchState,
  onSelectLink,
  onPreviewLink,
  permalink,
  canExport,
  onExport,
  onCopyFailed,
  engine,
  onEngineChange,
}: {
  mode: Mode;
  onModeChange: (m: Mode) => void;
  meta: NetworkMetadata | null;
  query: string;
  onQueryChange: (q: string) => void;
  searchState: SearchState;
  onSelectLink: (link: LinkSummary) => void;
  onPreviewLink: (link: LinkSummary | null) => void;
  permalink: string | null;
  canExport: boolean;
  onExport: () => void;
  onCopyFailed: (url: string) => void;
  /** Development only; the control renders nothing in a production build. */
  engine: Engine;
  onEngineChange: (e: Engine) => void;
}) {
  return (
    <header className="topbar">
      <div className="brand">
        <BrandMark />
        <span className="brand-name">NZ Critical Links</span>
      </div>

      <ModeNavigation mode={mode} onChange={onModeChange} />

      <GlobalRoadSearch
        query={query}
        onQueryChange={onQueryChange}
        state={searchState}
        onSelect={onSelectLink}
        onPreview={onPreviewLink}
      />

      <div className="topbar-right">
        {/* Beside the snapshot indicator, because both answer "which numbers
          * am I looking at" and a reader checking one will check the other. */}
        <EngineSwitch engine={engine} onChange={onEngineChange} />
        <SnapshotIndicator meta={meta} />
        <ShareExportActions
          permalink={permalink}
          canExport={canExport}
          onExport={onExport}
          onCopyFailed={onCopyFailed}
        />
      </div>
    </header>
  );
}
