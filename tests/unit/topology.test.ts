/**
 * Junction splitting: the rule that decides whether two roads meet.
 *
 * The whole model turns on the distinction tested here. Splitting where a road
 * ENDS on another road is what makes the network connected at all; refusing to
 * split where two roads merely CROSS is what preserves every overbridge,
 * tunnel and grade-separated interchange in the country.
 */

import { describe, expect, it } from 'vitest';

import { splitAtJunctions } from '../../packages/core/src/topology.js';
import { buildGraph } from '../../packages/core/src/graph.js';
import { DetourEngine } from '../../packages/core/src/detour.js';
import { synthetic, type SpecLink } from '../fixtures/synthetic.js';

function split(spec: SpecLink[], opts?: Parameters<typeof splitAtJunctions>[2]) {
  const net = synthetic(spec);
  const res = splitAtJunctions(net.links, net.geometry, opts);
  const built = buildGraph(res.links, res.geometry, []);
  return { res, graph: built.graph, links: res.links };
}

describe('T-junction: a road ending on another road', () => {
  const T: SpecLink[] = [
    { id: 'THROUGH', pts: [[0, 0], [200, 0]] },
    { id: 'SIDE', pts: [[100, 0], [100, 100]] },
  ];

  it('is disconnected before splitting', () => {
    const net = synthetic(T);
    expect(net.graph.componentCount).toBe(2);
  });

  it('splits the through road at the junction', () => {
    const { res } = split(T);
    expect(res.parentsSplit).toBe(1);
    expect(res.cutsMade).toBe(1);
    expect(res.linkCount).toBe(3); // two halves of THROUGH, plus SIDE
  });

  it('produces a single connected component', () => {
    const { graph } = split(T);
    expect(graph.componentCount).toBe(1);
  });

  it('preserves total length exactly', () => {
    const { links } = split(T);
    const through = links.filter((l) => l.closureGroupId === 'THROUGH');
    const total = through.reduce((a, l) => a + l.lengthM, 0);
    expect(total).toBeCloseTo(200, 9);
    expect(through.map((l) => l.lengthM).sort((a, b) => a - b)).toEqual([100, 100]);
  });

  it('keeps both halves in one closure group under the parent id', () => {
    const { links } = split(T);
    const through = links.filter((l) => l.closureGroupId === 'THROUGH');
    expect(through).toHaveLength(2);
    expect(through.map((l) => l.amdsId).sort()).toEqual(['THROUGH#0', 'THROUGH#1']);
  });

  it('closes the whole parent road when one piece is closed', () => {
    const { res, graph, links } = split(T);
    const engine = new DetourEngine(graph, links, {
      snapshotId: 't',
      coreLink: null,
      clipped: false,
    });
    const piece = links.findIndex((l) => l.amdsId === 'THROUGH#0');
    const out = engine.compute({ linkId: piece, closureScope: 'physical' });
    expect(out.removedAmdsIds.sort()).toEqual(['THROUGH#0', 'THROUGH#1']);
    expect(res.nearMisses).toHaveLength(0);
  });
});

describe('grade separation: two roads crossing with neither ending', () => {
  const X: SpecLink[] = [
    { id: 'OVER', pts: [[0, 50], [100, 50]] },
    { id: 'UNDER', pts: [[50, 0], [50, 100]] },
  ];

  it('makes no cut - neither link has an endpoint on the other', () => {
    const { res } = split(X);
    expect(res.cutsMade).toBe(0);
    expect(res.parentsSplit).toBe(0);
    expect(res.linkCount).toBe(2);
  });

  it('leaves the overbridge and the road beneath unconnected', () => {
    const { graph } = split(X);
    expect(graph.componentCount).toBe(2);
    expect(graph.nodeCount).toBe(4);
  });
});

describe('interchange: a ramp ending on a motorway is a real connection', () => {
  it('splits the motorway where the ramp merges but not where a bridge passes over', () => {
    const { res, graph } = split([
      { id: 'MOTORWAY', pts: [[0, 0], [1000, 0]] },
      { id: 'RAMP', pts: [[400, 0], [500, 120]] },
      { id: 'OVERBRIDGE', pts: [[800, -60], [800, 60]] },
    ]);
    // One cut, from the ramp only. The overbridge crosses mid-span with both
    // its endpoints clear of the motorway, so it must not cut anything.
    expect(res.cutsMade).toBe(1);
    // MOTORWAY -> 2 pieces, plus RAMP and OVERBRIDGE.
    expect(res.linkCount).toBe(4);
    // Motorway + ramp are one component; the overbridge stays separate.
    expect(graph.componentCount).toBe(2);
  });
});

describe('near misses are reported, not silently connected', () => {
  it('does not join an endpoint that is 1 m away from the through road', () => {
    const { res, graph } = split([
      { id: 'THROUGH', pts: [[0, 0], [200, 0]] },
      { id: 'SIDE', pts: [[100, 1], [100, 100]] },
    ]);
    expect(res.cutsMade).toBe(0);
    expect(graph.componentCount).toBe(2);
    expect(res.nearMisses.length).toBeGreaterThan(0);
    expect(res.nearMisses[0].distanceM).toBeCloseTo(1, 6);
  });

  it('does join it when the tolerance is widened deliberately', () => {
    const { res, graph } = split(
      [
        { id: 'THROUGH', pts: [[0, 0], [200, 0]] },
        { id: 'SIDE', pts: [[100, 1], [100, 100]] },
      ],
      { splitToleranceM: 1.5 },
    );
    expect(res.cutsMade).toBe(1);
    expect(graph.componentCount).toBe(1);
  });
});

describe('multiple junctions on one road', () => {
  it('cuts once per distinct junction, in order along the line', () => {
    const { res, links } = split([
      { id: 'MAIN', pts: [[0, 0], [300, 0]] },
      { id: 'S1', pts: [[100, 0], [100, 50]] },
      { id: 'S2', pts: [[200, 0], [200, 50]] },
    ]);
    expect(res.cutsMade).toBe(2);
    const pieces = links.filter((l) => l.closureGroupId === 'MAIN');
    expect(pieces).toHaveLength(3);
    expect(pieces.map((p) => p.lengthM)).toEqual([100, 100, 100]);
  });

  it('does not cut twice when two side roads end at the same point', () => {
    const { res, links } = split([
      { id: 'MAIN', pts: [[0, 0], [300, 0]] },
      { id: 'S1', pts: [[150, 0], [150, 50]] },
      { id: 'S2', pts: [[150, 0], [150, -50]] },
    ]);
    expect(res.cutsMade).toBe(1);
    expect(links.filter((l) => l.closureGroupId === 'MAIN')).toHaveLength(2);
  });
});

describe('a junction at an existing vertex', () => {
  it('splits at an interior vertex without duplicating it', () => {
    const { res, links, graph } = split([
      { id: 'MAIN', pts: [[0, 0], [100, 0], [200, 0]] },
      { id: 'SIDE', pts: [[100, 0], [100, 80]] },
    ]);
    expect(res.cutsMade).toBe(1);
    const pieces = links.filter((l) => l.closureGroupId === 'MAIN');
    expect(pieces.map((p) => p.lengthM)).toEqual([100, 100]);
    expect(graph.componentCount).toBe(1);
  });

  it('makes no cut when the side road meets the through road end-to-end', () => {
    const { res, graph } = split([
      { id: 'A', pts: [[0, 0], [100, 0]] },
      { id: 'B', pts: [[100, 0], [200, 0]] },
    ]);
    expect(res.cutsMade).toBe(0);
    expect(graph.componentCount).toBe(1);
  });
});
