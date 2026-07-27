/**
 * Known-answer tests for the routing and detour engine.
 *
 * Every expected number here is derivable on paper from the fixture geometry.
 * That is the point: these check the engine against arithmetic rather than
 * against a previous run of itself.
 */

import { describe, expect, it } from 'vitest';

import { synthetic, type SpecLink } from '../fixtures/synthetic.js';
import { Router } from '../../packages/core/src/routing.js';
import { detourCacheKey } from '../../packages/core/src/cache.js';

const SQ: SpecLink[] = [
  { id: 'S', pts: [[0, 0], [100, 0]] },
  { id: 'E', pts: [[100, 0], [100, 100]] },
  { id: 'N', pts: [[100, 100], [0, 100]] },
  { id: 'W', pts: [[0, 100], [0, 0]] },
];

describe('1. isolated edge with no detour', () => {
  it('reports DISCONNECTED, not an error and not a zero', () => {
    const net = synthetic([{ id: 'A', pts: [[0, 0], [100, 0]] }]);
    const r = net.engine.compute({ linkId: net.byId.get('A')! });
    expect(r.forward!.status).toBe('DISCONNECTED');
    expect(r.reverse!.status).toBe('DISCONNECTED');
    expect(r.forward!.alternativeDistanceM).toBeNull();
    expect(r.forward!.detourRatioVsLink).toBeNull();
  });
});

describe('2. square loop with a known alternative distance', () => {
  it('detours the long way round for exactly 300 m', () => {
    const net = synthetic(SQ);
    const r = net.engine.compute({ linkId: net.byId.get('S')! });
    const f = r.forward!;
    expect(f.status).toBe('OK');
    expect(f.selectedLinkLengthM).toBeCloseTo(100, 6);
    expect(f.alternativeDistanceM).toBeCloseTo(300, 6);
    expect(f.addedDistanceVsLinkM).toBeCloseTo(200, 6);
    expect(f.detourRatioVsLink).toBeCloseTo(3, 6);
    // The intact shortest path between S's own endpoints is S itself.
    expect(f.normalPathDistanceM).toBeCloseTo(100, 6);
    expect(f.networkPenaltyM).toBeCloseTo(200, 6);
    expect(f.routeLinkIds).toHaveLength(3);
  });

  it('is symmetric on a fully two-way loop', () => {
    const net = synthetic(SQ);
    const r = net.engine.compute({ linkId: net.byId.get('S')! });
    expect(r.reverse!.alternativeDistanceM).toBeCloseTo(300, 6);
  });
});

describe('3. triangle with a known shortest replacement path', () => {
  it('replaces the 500 m hypotenuse with 400 + 300', () => {
    const net = synthetic([
      { id: 'AB', pts: [[0, 0], [400, 0]] },
      { id: 'AC', pts: [[0, 0], [0, 300]] },
      { id: 'BC', pts: [[400, 0], [0, 300]] },
    ]);
    const r = net.engine.compute({ linkId: net.byId.get('BC')! });
    const f = r.forward!;
    expect(f.selectedLinkLengthM).toBeCloseTo(500, 6);
    expect(f.alternativeDistanceM).toBeCloseTo(700, 6);
    expect(f.detourRatioVsLink).toBeCloseTo(1.4, 6);
  });
});

describe('4. directed one-way network: forward and reverse differ', () => {
  // X is two-way. The only return path is a one-way loop usable B->A only.
  const NET: SpecLink[] = [
    { id: 'X', pts: [[0, 0], [100, 0]] },
    { id: 'BC', pts: [[100, 0], [100, 100]], oneway: true },
    { id: 'CD', pts: [[100, 100], [0, 100]], oneway: true },
    { id: 'DA', pts: [[0, 100], [0, 0]], oneway: true },
  ];

  it('has no forward alternative but a 300 m reverse alternative', () => {
    const net = synthetic(NET);
    const r = net.engine.compute({ linkId: net.byId.get('X')! });
    expect(r.forward!.status).toBe('DISCONNECTED');
    expect(r.reverse!.status).toBe('OK');
    expect(r.reverse!.alternativeDistanceM).toBeCloseTo(300, 6);
  });

  it('generates one arc for a one-way link', () => {
    const net = synthetic(NET);
    expect(net.graph.arcsOfLink(net.byId.get('BC')!)).toHaveLength(1);
  });
});

describe('5. a two-way link is represented by two arcs', () => {
  it('produces exactly two opposed arcs', () => {
    const net = synthetic([{ id: 'A', pts: [[0, 0], [100, 0]] }]);
    const arcs = net.graph.arcsOfLink(0);
    expect(arcs).toHaveLength(2);
    expect(net.graph.arcFrom[arcs[0]]).toBe(net.graph.arcTo[arcs[1]]);
    expect(net.graph.arcTo[arcs[0]]).toBe(net.graph.arcFrom[arcs[1]]);
  });
});

describe('6. parallel carriageways are not merged by proximity', () => {
  // A divided highway: northbound and southbound carriageways 8 m apart, in
  // separate closure groups because AMDS models them as separate assets. A
  // parallel local road provides the only lawful northbound alternative -
  // you cannot drive north up the southbound carriageway, and the engine must
  // not pretend otherwise.
  const NET: SpecLink[] = [
    { id: 'NB', pts: [[0, 0], [500, 0]], oneway: true, closureGroup: 'NB' },
    { id: 'SB', pts: [[500, 8], [0, 8]], oneway: true, closureGroup: 'SB' },
    { id: 'X1', pts: [[0, 0], [0, 8]] },
    { id: 'X2', pts: [[500, 0], [500, 8]] },
    { id: 'LR1', pts: [[0, 0], [0, -50]] },
    { id: 'LR2', pts: [[0, -50], [500, -50]] },
    { id: 'LR3', pts: [[500, -50], [500, 0]] },
  ];

  it('closes only the selected carriageway, not its neighbour 8 m away', () => {
    const net = synthetic(NET);
    const r = net.engine.compute({ linkId: net.byId.get('NB')! });
    expect(r.removedLinkIds).toEqual([net.byId.get('NB')!]);
    expect(r.removedArcIds).toHaveLength(1); // NB is one-way
  });

  it('detours via the parallel local road, not up the opposing carriageway', () => {
    const net = synthetic(NET);
    const r = net.engine.compute({ linkId: net.byId.get('NB')! });
    expect(r.forward!.status).toBe('OK');
    // 50 down + 500 across + 50 up
    expect(r.forward!.alternativeDistanceM).toBeCloseTo(600, 6);
    expect(r.forward!.routeLinkIds).not.toContain(net.byId.get('SB')!);
  });

  it('leaves the opposing carriageway fully usable after the closure', () => {
    const net = synthetic(NET);
    const nbArc = net.graph.arcsOfLink(net.byId.get('NB')!);
    const router = new Router(net.graph);
    const sb = net.links[net.byId.get('SB')!];
    const res = router.route({
      sourceNode: sb.sourceNode,
      targetNode: sb.targetNode,
      metric: 'distance',
      profile: 'car',
      excludedArcs: Int32Array.from(nbArc),
    });
    expect(res.status).toBe('OK');
    expect(res.distanceM).toBeCloseTo(500, 6);
  });
});

describe('7. grade-separated crossing produces no connection', () => {
  it('does not node two polylines that merely cross in plan view', () => {
    const net = synthetic([
      { id: 'OVER', pts: [[0, 50], [100, 50]] },
      { id: 'UNDER', pts: [[50, 0], [50, 100]] },
    ]);
    // Four endpoints, four nodes. A crossing node would give five.
    expect(net.graph.nodeCount).toBe(4);
    expect(net.graph.componentCount).toBe(2);

    const router = new Router(net.graph);
    const res = router.route({
      sourceNode: net.links[0].sourceNode,
      targetNode: net.links[1].targetNode,
      metric: 'distance',
      profile: 'car',
    });
    expect(res.status).toBe('DISCONNECTED');
  });
});

describe('8. prohibited turn', () => {
  const NET: SpecLink[] = [
    { id: 'AB', pts: [[0, 0], [100, 0]] },
    { id: 'BC', pts: [[100, 0], [200, 0]] },
    { id: 'BD', pts: [[100, 0], [100, 100]] },
    { id: 'DC', pts: [[100, 100], [200, 0]] },
  ];
  const BAN = [{ seq: ['AB', 'BC'], vehicle: true, heavy: true, emergency: false }];

  it('routes straight through when no restriction applies', () => {
    const net = synthetic(NET);
    const router = new Router(net.graph);
    const res = router.route({
      sourceNode: net.links[net.byId.get('AB')!].sourceNode,
      targetNode: net.links[net.byId.get('BC')!].targetNode,
      metric: 'distance',
      profile: 'car',
    });
    expect(res.distanceM).toBeCloseTo(200, 6);
  });

  it('forces the longer legal path when the turn is banned for cars', () => {
    const net = synthetic(NET, BAN);
    const router = new Router(net.graph);
    const res = router.route({
      sourceNode: net.links[net.byId.get('AB')!].sourceNode,
      targetNode: net.links[net.byId.get('BC')!].targetNode,
      metric: 'distance',
      profile: 'car',
    });
    expect(res.status).toBe('OK');
    // 100 + 100 + hypot(100,100)
    expect(res.distanceM).toBeCloseTo(200 + Math.hypot(100, 100), 6);
  });

  it('lets an exempt profile take the banned turn', () => {
    const net = synthetic(NET, BAN);
    const router = new Router(net.graph);
    const res = router.route({
      sourceNode: net.links[net.byId.get('AB')!].sourceNode,
      targetNode: net.links[net.byId.get('BC')!].targetNode,
      metric: 'distance',
      profile: 'emergency',
    });
    expect(res.distanceM).toBeCloseTo(200, 6);
  });
});

describe('9. access-restricted road excluded for the car profile', () => {
  const NET: SpecLink[] = [
    { id: 'PRIVATE', pts: [[0, 0], [100, 0]], modeVehicle: false, modeEmergency: true },
    { id: 'L1', pts: [[0, 0], [0, 100]] },
    { id: 'L2', pts: [[0, 100], [100, 100]] },
    { id: 'L3', pts: [[100, 100], [100, 0]] },
  ];

  it('sends a car the long way', () => {
    const net = synthetic(NET);
    const router = new Router(net.graph);
    const res = router.route({
      sourceNode: net.links[0].sourceNode,
      targetNode: net.links[0].targetNode,
      metric: 'distance',
      profile: 'car',
    });
    expect(res.distanceM).toBeCloseTo(300, 6);
  });

  it('lets an emergency vehicle use it', () => {
    const net = synthetic(NET);
    const router = new Router(net.graph);
    const res = router.route({
      sourceNode: net.links[0].sourceNode,
      targetNode: net.links[0].targetNode,
      metric: 'distance',
      profile: 'emergency',
    });
    expect(res.distanceM).toBeCloseTo(100, 6);
  });
});

describe('10. mode restriction by vehicle class', () => {
  it('excludes a link the heavy profile may not use', () => {
    const net = synthetic([
      { id: 'LIGHT_ONLY', pts: [[0, 0], [100, 0]], modeVehicleHeavy: false },
      { id: 'L1', pts: [[0, 0], [0, 100]] },
      { id: 'L2', pts: [[0, 100], [100, 100]] },
      { id: 'L3', pts: [[100, 100], [100, 0]] },
    ]);
    const router = new Router(net.graph);
    const car = router.route({
      sourceNode: net.links[0].sourceNode,
      targetNode: net.links[0].targetNode,
      metric: 'distance',
      profile: 'car',
    });
    const heavy = router.route({
      sourceNode: net.links[0].sourceNode,
      targetNode: net.links[0].targetNode,
      metric: 'distance',
      profile: 'heavy',
    });
    expect(car.distanceM).toBeCloseTo(100, 6);
    expect(heavy.distanceM).toBeCloseTo(300, 6);
  });
});

describe('11. cul-de-sac', () => {
  it('has no replacement path for the terminal link', () => {
    const net = synthetic([
      { id: 'MAIN', pts: [[0, 0], [100, 0]] },
      { id: 'SPUR', pts: [[100, 0], [180, 0]] },
    ]);
    const r = net.engine.compute({ linkId: net.byId.get('SPUR')! });
    expect(r.forward!.status).toBe('DISCONNECTED');
  });
});

describe('12. disconnected component', () => {
  it('counts components and refuses to route between them', () => {
    const net = synthetic([
      { id: 'MAINLAND', pts: [[0, 0], [100, 0]] },
      { id: 'ISLAND', pts: [[9000, 9000], [9100, 9000]] },
    ]);
    expect(net.graph.componentCount).toBe(2);
    const router = new Router(net.graph);
    const res = router.route({
      sourceNode: net.links[0].sourceNode,
      targetNode: net.links[1].targetNode,
      metric: 'distance',
      profile: 'car',
    });
    expect(res.status).toBe('DISCONNECTED');
    expect(res.detail).toMatch(/component/);
  });
});

describe('13. a timeout is never reported as DISCONNECTED', () => {
  it('returns UNRESOLVED_TIMEOUT when the state budget is exhausted', () => {
    const net = synthetic(SQ);
    const r = net.engine.compute({
      linkId: net.byId.get('S')!,
      maxStatesExplored: 1,
    });
    expect(r.forward!.status).toBe('UNRESOLVED_TIMEOUT');
    expect(r.forward!.status).not.toBe('DISCONNECTED');
    expect(r.forward!.alternativeDistanceM).toBeNull();
    expect(r.forward!.errorDetail).toMatch(/state limit/);
  });

  it('still finds the answer with an adequate budget', () => {
    const net = synthetic(SQ);
    const r = net.engine.compute({ linkId: net.byId.get('S')! });
    expect(r.forward!.status).toBe('OK');
  });
});

describe('14. cache invalidation', () => {
  const base = {
    snapshotId: 'snap-a',
    linkId: 42,
    closureScope: 'physical' as const,
    directions: ['forward' as const],
    profile: 'car' as const,
    metric: 'distance' as const,
  };

  it('changes when the snapshot changes', () => {
    expect(detourCacheKey(base)).not.toBe(
      detourCacheKey({ ...base, snapshotId: 'snap-b' }),
    );
  });

  it('changes when the algorithm version changes', () => {
    expect(detourCacheKey(base)).not.toBe(
      detourCacheKey({ ...base, algorithmVersion: '9.9.9' }),
    );
  });

  it('changes with closure scope, profile, metric and direction', () => {
    const keys = new Set([
      detourCacheKey(base),
      detourCacheKey({ ...base, closureScope: 'directed' }),
      detourCacheKey({ ...base, profile: 'heavy' }),
      detourCacheKey({ ...base, metric: 'time' }),
      detourCacheKey({ ...base, directions: ['reverse'] }),
    ]);
    expect(keys.size).toBe(5);
  });

  it('is stable under direction ordering', () => {
    expect(
      detourCacheKey({ ...base, directions: ['forward', 'reverse'] }),
    ).toBe(detourCacheKey({ ...base, directions: ['reverse', 'forward'] }));
  });
});

describe('15. physical versus directed closure scope', () => {
  // L1 and L2 form one physical structure (a bridge) sharing a closure group.
  // A long bypass reaches C but not B directly.
  const NET: SpecLink[] = [
    { id: 'L1', pts: [[0, 0], [100, 0]], closureGroup: 'BRIDGE' },
    { id: 'L2', pts: [[100, 0], [200, 0]], closureGroup: 'BRIDGE' },
    { id: 'BY1', pts: [[0, 0], [0, -100]] },
    { id: 'BY2', pts: [[0, -100], [200, -100]] },
    { id: 'BY3', pts: [[200, -100], [200, 0]] },
  ];

  it('physical closure removes the whole group and isolates B', () => {
    const net = synthetic(NET);
    const r = net.engine.compute({
      linkId: net.byId.get('L1')!,
      closureScope: 'physical',
    });
    expect(r.removedLinkIds.sort()).toEqual(
      [net.byId.get('L1')!, net.byId.get('L2')!].sort(),
    );
    expect(r.removedArcIds).toHaveLength(4);
    expect(r.forward!.status).toBe('DISCONNECTED');
  });

  it('directed closure removes one arc and a route survives', () => {
    const net = synthetic(NET);
    const r = net.engine.compute({
      linkId: net.byId.get('L1')!,
      closureScope: 'directed',
    });
    expect(r.forward!.removedArcIds).toHaveLength(1);
    expect(r.forward!.status).toBe('OK');
    // 100 down + 200 across + 100 up + 100 back along L2 = 500
    expect(r.forward!.alternativeDistanceM).toBeCloseTo(500, 6);
  });

  it('reports how many arcs each scope removes', () => {
    const net = synthetic(NET);
    const phys = net.engine.compute({
      linkId: net.byId.get('L1')!,
      closureScope: 'physical',
    });
    const dir = net.engine.compute({
      linkId: net.byId.get('L1')!,
      closureScope: 'directed',
    });
    expect(phys.removedArcIds.length).toBeGreaterThan(dir.removedArcIds.length);
  });
});

describe('metric semantics', () => {
  it('network penalty differs from added-distance when the link is not the normal path', () => {
    // A 900 m link between two nodes that also have a 100 m shortcut.
    const net = synthetic([
      { id: 'LONG', pts: [[0, 0], [0, 450], [100, 450], [100, 0]] },
      { id: 'SHORT', pts: [[0, 0], [100, 0]] },
    ]);
    const r = net.engine.compute({ linkId: net.byId.get('LONG')! });
    const f = r.forward!;
    expect(f.selectedLinkLengthM).toBeCloseTo(1000, 6);
    expect(f.normalPathDistanceM).toBeCloseTo(100, 6); // the shortcut, not LONG
    expect(f.alternativeDistanceM).toBeCloseTo(100, 6);
    expect(f.addedDistanceVsLinkM).toBeCloseTo(-900, 6); // negative: link was a detour itself
    expect(f.networkPenaltyM).toBeCloseTo(0, 6); // closing it costs the network nothing
  });

  it('time routing can prefer a longer, faster path', () => {
    const net = synthetic([
      { id: 'SLOW', pts: [[0, 0], [1000, 0]], speedKph: 10 },
      { id: 'FASTA', pts: [[0, 0], [0, 300]], speedKph: 100 },
      { id: 'FASTB', pts: [[0, 300], [1000, 300]], speedKph: 100 },
      { id: 'FASTC', pts: [[1000, 300], [1000, 0]], speedKph: 100 },
    ]);
    const router = new Router(net.graph);
    const byDist = router.route({
      sourceNode: net.links[0].sourceNode,
      targetNode: net.links[0].targetNode,
      metric: 'distance',
      profile: 'car',
    });
    const byTime = router.route({
      sourceNode: net.links[0].sourceNode,
      targetNode: net.links[0].targetNode,
      metric: 'time',
      profile: 'car',
    });
    expect(byDist.distanceM).toBeCloseTo(1000, 6); // SLOW is shortest
    expect(byTime.distanceM).toBeCloseTo(1600, 6); // but the loop is quicker
    expect(byTime.timeS!).toBeLessThan(byDist.timeS!);
  });
});

describe('clipped-extract honesty', () => {
  it('flags a DISCONNECTED result as unverified when the network was clipped', () => {
    const net = synthetic([{ id: 'A', pts: [[0, 0], [100, 0]] }], [], {
      clipped: true,
    });
    const r = net.engine.compute({ linkId: 0 });
    expect(r.forward!.status).toBe('DISCONNECTED');
    expect(r.forward!.qualityFlags).toContain(
      'DISCONNECTED_UNVERIFIED_OUTSIDE_EXTRACT',
    );
  });
});
