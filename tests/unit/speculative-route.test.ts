/**
 * A POSSIBLE-graph route must never be drawn as the teal replacement path.
 *
 * Teal is the colour of the answer this system publishes: minimum
 * represented-network distance on the canonical graph. A route found on the
 * POSSIBLE graph assumed that crossings the classifier could not resolve are
 * junctions — it exists to measure how much the answer depends on those
 * assumptions, and it is not an answer. Drawing one in the other's colour
 * would present an unverified claim as a measured result, and it would do so
 * silently: nothing errors, the map simply lies.
 *
 * The rule is enforced STRUCTURALLY rather than by a paint condition. There is
 * one function, `routeTarget`, that maps a route to a source and a layer, and
 * the two layers are fed from two different sources. Wiring the whole MapLibre
 * component is impractical here — the test environment has no canvas and no
 * requestAnimationFrame, which is why tests/unit/map-style.test.ts validates
 * the style rather than rendering it — so the decision function is what is
 * tested, plus the fact that it really is the only decision: NetworkMap.tsx is
 * read and asserted to name neither the teal nor either route layer itself.
 */

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import {
  ROUTE_TARGETS,
  otherTarget,
  routeTarget,
  type PossibleProvenance,
} from '../../apps/web/src/map/routeTarget.js';
import { LYR, OVERLAY_LAYERS, SRC } from '../../apps/web/src/map/style.js';
import { palette } from '../../apps/web/src/styles/palette.js';

const NETWORK_MAP = fileURLToPath(
  new URL('../../apps/web/src/map/NetworkMap.tsx', import.meta.url),
);

/** A provenance block, as `nzcl.provenance.as_dict` emits it. */
function provenance(over: Partial<PossibleProvenance> = {}): PossibleProvenance {
  return {
    graph: 'possible',
    canonical: false,
    robustness: 'ONE_UNRESOLVED_CROSSING',
    speculativeJunctionCount: 1,
    changedByOneCrossing: true,
    requiresMultipleAssumptions: false,
    decisivenessMethod: 'SINGLE_CROSSING_BY_CONSTRUCTION',
    unresolvedCrossingIds: [7],
    decisiveCrossingIds: [7],
    crossings: [
      {
        crossingId: 7,
        sourceA: 'GREENDALE',
        sourceB: 'CLINTONS',
        x: 1520000,
        y: 5165000,
        lon: 172.0077615,
        lat: -43.6636988,
        reason: 'NO_EVIDENCE_EITHER_WAY',
        confidence: 'MEDIUM',
        angleDeg: 90,
        placeId: 0,
        fromLinkId: 1,
        toLinkId: 2,
        decisive: true,
      },
    ],
    detail: 'a synthetic block for the drawing rule',
    ...over,
  };
}

describe('a speculative route is never the teal replacement path', () => {
  it('sends a route carrying provenance away from palette.route', () => {
    /* The assertion the whole change exists for. */
    const target = routeTarget(provenance());
    expect(target.colour).not.toBe(palette.route);
    expect(target.layer).not.toBe(LYR.routeFocus);
    expect(target.source).not.toBe(SRC.routeFocus);
    expect(target.kind).toBe('speculative');
  });

  it('does so for every robustness the classifier can report', () => {
    /*
     * Including ROBUST. A possible-graph route that turned out to rely on no
     * unresolved crossing is still a possible-graph route, and the moment the
     * rule starts reading the count there is a value of the count that puts a
     * speculative result back into the published colour.
     */
    for (const robustness of [
      'ROBUST',
      'ONE_UNRESOLVED_CROSSING',
      'MULTIPLE_UNRESOLVED_CROSSINGS',
    ] as const) {
      for (const speculativeJunctionCount of [0, 1, 4]) {
        const t = routeTarget(
          provenance({ robustness, speculativeJunctionCount }),
        );
        expect(t.colour, `${robustness}/${speculativeJunctionCount}`).not.toBe(
          palette.route,
        );
        expect(t.kind).toBe('speculative');
      }
    }
  });

  it('keeps the canonical route teal when there is no provenance', () => {
    /* The other half. A rule that painted everything violet would also pass
     * the test above and would be just as wrong. */
    for (const absent of [null, undefined]) {
      const t = routeTarget(absent);
      expect(t.colour).toBe(palette.route);
      expect(t.layer).toBe(LYR.routeFocus);
      expect(t.source).toBe(SRC.routeFocus);
      expect(t.kind).toBe('canonical');
    }
  });

  it('gives the two kinds different sources, not one layer repainted', () => {
    /* Separate sources are what make the rule hold under a later edit: the
     * teal layer cannot draw a speculative route because it is not fed from
     * the source a speculative route is written to. */
    const a = routeTarget(null);
    const b = routeTarget(provenance());
    expect(a.source).not.toBe(b.source);
    expect(a.layer).not.toBe(b.layer);
    expect(otherTarget(a)).toEqual(b);
    expect(otherTarget(b)).toEqual(a);
    expect(new Set(ROUTE_TARGETS.map((t) => t.source)).size).toBe(2);
  });

  it('never animates the dashed speculative layer', () => {
    /* MapLibre supports neither line-gradient nor line-dasharray beside the
     * other, so an animated speculative layer would silently draw nothing. */
    expect(routeTarget(provenance()).animated).toBe(false);
    expect(routeTarget(null).animated).toBe(true);
  });
});

describe('the speculative layer itself', () => {
  const layer: any = OVERLAY_LAYERS.find((l) => l.id === LYR.routeSpeculative);

  it('exists and is fed from its own source', () => {
    expect(layer).toBeDefined();
    expect(layer.source).toBe(SRC.routeSpeculative);
    expect(layer.source).not.toBe(SRC.routeFocus);
  });

  it('is dashed and is not the route teal', () => {
    expect(layer.paint['line-dasharray']).toBeDefined();
    expect(layer.paint['line-color']).not.toBe(palette.route);
    expect(layer.paint['line-color']).toBe(palette.speculative);
    /* No gradient, so nothing can try to reveal it. */
    expect(layer.paint['line-gradient']).toBeUndefined();
  });

  it('draws beneath the canonical route', () => {
    /* They should never both be populated — NetworkMap clears the one it did
     * not choose — but if that ever failed, the published answer must be the
     * one on top rather than a speculative line covering it. */
    const ids = OVERLAY_LAYERS.map((l) => l.id);
    expect(ids.indexOf(LYR.routeFocus)).toBeGreaterThan(
      ids.indexOf(LYR.routeSpeculative),
    );
  });

  it('is not what the teal layer draws', () => {
    const focus: any = OVERLAY_LAYERS.find((l) => l.id === LYR.routeFocus);
    expect(focus.source).toBe(SRC.routeFocus);
    expect(JSON.stringify(focus.paint['line-gradient'])).toContain(
      palette.route,
    );
  });
});

describe('the decision is made in exactly one place', () => {
  /*
   * `routeTarget` is only a guarantee if nothing else chooses. This reads the
   * map component and asserts it names neither the colour nor either route
   * layer — so a future edit that wanted to paint the focused route
   * conditionally would have to reintroduce one of these names, and would fail
   * here rather than ship a speculative route in canonical teal.
   *
   * Deliberately a source assertion and not a duplicated definition: nothing
   * is copied here that could drift, only the absence of names.
   */
  const source = readFileSync(NETWORK_MAP, 'utf8');

  it('the map component never names the route teal', () => {
    expect(source).not.toContain('palette.route');
    expect(source).not.toContain(palette.route);
  });

  it('the map component never names either route layer or source', () => {
    for (const name of [
      'LYR.routeFocus',
      'SRC.routeFocus',
      'LYR.routeSpeculative',
      'SRC.routeSpeculative',
    ]) {
      expect(source, `NetworkMap.tsx still names ${name}`).not.toContain(name);
    }
  });

  it('the map component reaches the route layers through routeTarget', () => {
    expect(source).toContain('routeTarget(');
    expect(source).toContain('ROUTE_TARGETS');
    expect(source).toContain('otherTarget(');
  });
});
