/**
 * The map palette, in TypeScript.
 *
 * MapLibre paint properties cannot read CSS custom properties — a style
 * specification is JSON evaluated on the GPU, not a DOM subtree. So the colours
 * the map draws with have to exist as literals somewhere.
 *
 * These MUST stay identical to the corresponding tokens in tokens.css. That is
 * not left to discipline: styles/palette.test.ts parses tokens.css and asserts
 * every key below matches, so a change to one file without the other fails the
 * test run rather than quietly producing a map whose teal is a slightly
 * different teal from the legend swatch beside it.
 */

export const palette = {
  mapWater: '#0f1418',
  mapLand: '#161c21',
  mapContour: '#1e262c',
  mapHighway: '#4a7fb5',
  mapLocal: '#39434d',
  mapLabel: '#5d6874',

  shellBg: '#1b2127',
  shellRaised: '#222a31',
  shellLine: '#2c353d',
  shellFg: '#e8edf1',
  shellFgMuted: '#8794a1',
  shellFgFaint: '#5d6874',

  closure: '#ff4d4d',
  route: '#2de1c2',
  compare: '#ffb020',
  stranded: '#ffb020',
  corridor: '#7fb2e0',
  /*
   * A route found on the POSSIBLE graph, which assumed that crossings the
   * classifier could not resolve are junctions. Violet because it must not be
   * mistaken for any of the four above at a glance — and never teal, because
   * teal is the canonical replacement path and a sensitivity result must never
   * be readable as the official answer.
   */
  speculative: '#b48cf2',
} as const;

/** The CSS custom property each key mirrors. Used by the drift test. */
export const paletteTokenNames: Record<keyof typeof palette, string> = {
  mapWater: '--map-water',
  mapLand: '--map-land',
  mapContour: '--map-contour',
  mapHighway: '--map-highway',
  mapLocal: '--map-local',
  mapLabel: '--map-label',

  shellBg: '--shell-bg',
  shellRaised: '--shell-raised',
  shellLine: '--shell-line',
  shellFg: '--shell-fg',
  shellFgMuted: '--shell-fg-muted',
  shellFgFaint: '--shell-fg-faint',

  closure: '--closure',
  route: '--route',
  compare: '--compare',
  stranded: '--stranded',
  corridor: '--corridor',
  speculative: '--speculative',
};
