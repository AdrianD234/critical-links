/**
 * Corridor-level replacement path.
 *
 * These cover the case that motivated the measure: a one-way carriageway whose
 * downstream endpoint is an internal node of the one-way system, so the
 * endpoint measure has no answer even though traffic plainly reroutes.
 */

import { describe, expect, it } from 'vitest';

import { synthetic, type SpecLink } from '../fixtures/synthetic.js';

/**
 * A one-way pair with a parallel local street.
 *
 *   A --NB1--> B --NB2--> C          (northbound, one-way)
 *   A <--SB--- ...  <---- C          (southbound, one-way, via D)
 *   A --L1--> E --L2--> C            (two-way local bypass)
 *
 * Closing NB1 leaves B reachable only by driving NB1, so the endpoint measure
 * for A->B is DISCONNECTED. A through trip A->C is barely affected.
 */
const ONE_WAY_PAIR: SpecLink[] = [
  { id: 'NB1', pts: [[0, 0], [500, 0]], oneway: true },
  { id: 'NB2', pts: [[500, 0], [1000, 0]], oneway: true },
  { id: 'SB', pts: [[1000, 20], [0, 20]], oneway: true },
  { id: 'JOIN_N', pts: [[1000, 0], [1000, 20]] },
  { id: 'JOIN_S', pts: [[0, 20], [0, 0]] },
  { id: 'L1', pts: [[0, 0], [0, -300]] },
  { id: 'L2', pts: [[0, -300], [1000, -300]] },
  { id: 'L3', pts: [[1000, -300], [1000, 0]] },
];

describe('one-way carriageway with an internal downstream node', () => {
  it('has no endpoint-measure answer, which is expected rather than an error', () => {
    const net = synthetic(ONE_WAY_PAIR);
    const r = net.engine.compute({ linkId: net.byId.get('NB1')! });
    expect(r.forward!.status).toBe('DISCONNECTED');
    expect(r.forward!.alternativeDistanceM).toBeNull();
  });

  it('still reports a corridor result, so the closure is not mislabelled as isolating', () => {
    const net = synthetic(ONE_WAY_PAIR);
    const r = net.engine.compute({ linkId: net.byId.get('NB1')! });
    const c = r.forward!.corridor!;
    expect(c).not.toBeNull();
    expect(c.status).toBe('OK');
    expect(r.forward!.qualityFlags).toContain('ENDPOINT_MEASURE_UNDEFINED_CORRIDOR_USED');
  });

  it('measures the through-trip penalty via the local bypass', () => {
    const net = synthetic(ONE_WAY_PAIR);
    const c = net.engine.compute({ linkId: net.byId.get('NB1')! }).forward!.corridor!;
    // The corridor expands one hop each way: entry (0,20), exit (1000,0).
    // Normal   = JOIN_S 20 + NB1 500 + NB2 500                = 1020 m
    // Closed   = JOIN_S 20 + L1 300 + L2 1000 + L3 300        = 1620 m
    expect(c.normalDistanceM).toBeCloseTo(1020, 6);
    expect(c.alternativeDistanceM).toBeCloseTo(1620, 6);
    expect(c.penaltyM).toBeCloseTo(600, 6);
  });

  it('walks downstream past the internal node to a real rejoin point', () => {
    const net = synthetic(ONE_WAY_PAIR);
    const c = net.engine.compute({ linkId: net.byId.get('NB1')! }).forward!.corridor!;
    expect(c.hopsDownstream).toBeGreaterThan(0);
    expect(c.truncated).toBe(false);
  });
});

describe('a genuine dead end is still reported as one', () => {
  it('flags SOLE_ACCESS when there is nowhere to walk to', () => {
    const net = synthetic([
      { id: 'MAIN', pts: [[0, 0], [100, 0]] },
      { id: 'SPUR', pts: [[100, 0], [200, 0]] },
    ]);
    const r = net.engine.compute({ linkId: net.byId.get('SPUR')! });
    expect(r.forward!.status).toBe('DISCONNECTED');
    expect(r.forward!.corridor!.status).not.toBe('OK');
    expect(r.forward!.qualityFlags).toContain('SOLE_ACCESS');
  });
});

describe('corridor is not computed when the endpoint measure succeeds', () => {
  it('leaves corridor null on an ordinary two-way loop', () => {
    const net = synthetic([
      { id: 'S', pts: [[0, 0], [100, 0]] },
      { id: 'E', pts: [[100, 0], [100, 100]] },
      { id: 'N', pts: [[100, 100], [0, 100]] },
      { id: 'W', pts: [[0, 100], [0, 0]] },
    ]);
    const r = net.engine.compute({ linkId: net.byId.get('S')! });
    expect(r.forward!.status).toBe('OK');
    expect(r.forward!.corridor).toBeNull();
  });

  it('can be switched off entirely for batch runs', () => {
    const net = synthetic([{ id: 'A', pts: [[0, 0], [100, 0]] }]);
    const r = net.engine.compute({ linkId: 0, computeCorridor: false });
    expect(r.forward!.status).toBe('DISCONNECTED');
    expect(r.forward!.corridor).toBeNull();
  });
});
