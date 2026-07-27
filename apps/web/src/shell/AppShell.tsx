/*
 * The application frame.
 *
 * Pure layout: a two-row grid (top bar, body) whose body is a three-column grid
 * (rail, workspace, inspector). It takes slots rather than knowing what goes in
 * them, so the shell's proportions can be reasoned about — and screenshotted —
 * independently of what the Explore screen happens to be showing.
 *
 * The skip link is first in the DOM so a keyboard user can reach the result
 * without tabbing through the map's controls.
 */

import type { ReactNode } from 'react';

export default function AppShell({
  topBar,
  rail,
  workspace,
  inspector,
  bottomInset = 0,
}: {
  topBar: ReactNode;
  rail: ReactNode;
  workspace: ReactNode;
  inspector: ReactNode;
  /**
   * How much of the map's bottom edge the inspector covers, published as a CSS
   * variable so map furniture can sit clear of it.
   *
   * The attribution is the reason this exists. LINZ Basemaps (CC BY 4.0) and
   * the AMDS source both require visible attribution, and on mobile the bottom
   * sheet would otherwise cover it at every stop but collapsed.
   */
  bottomInset?: number;
}) {
  return (
    <div
      className="app-shell"
      style={{ ['--sheet-h' as string]: `${bottomInset}px` }}
    >
      <a className="sr-only sr-only-focusable" href="#result">
        Skip to the closure result
      </a>

      {topBar}

      <div className="shell-body">
        {rail}
        {workspace}
        {inspector}
      </div>
    </div>
  );
}
