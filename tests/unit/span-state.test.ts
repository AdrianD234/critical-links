/**
 * The outage editor's ordering contracts.
 *
 * These are the failures a map cannot show you. A stale response landing last
 * looks exactly like a correct one; a pinned corridor silently dropped looks
 * like the editor working. So the reducer is tested directly, and the map is
 * left to be a renderer.
 *
 * The case that matters most is `TestStaleResponses`: dragging a handle issues
 * a request per position, their answers arrive out of order by design, and the
 * only thing standing between that and a confidently wrong number on screen is
 * the sequence check.
 */

import { describe, expect, it } from 'vitest';

import {
  EMPTY_SPAN,
  closedLengthM,
  corridorChoices,
  handleAmbiguity,
  handleRef,
  isRequestable,
  isUnusuallyLong,
  spanReducer,
  type SpanState,
} from '../../apps/web/src/span/spanState.js';
import type {
  CorridorResult,
  OutageAnalysis,
  SnapHandle,
  SnapResult,
  SpanCandidate,
} from '../../apps/web/src/api/outage.js';

function handle(linkId: number, fraction: number, roadName = 'Main Road'): SnapHandle {
  return {
    linkId,
    amdsId: `{amds-${linkId}}`,
    closureGroupId: `{amds-${linkId}}`,
    roadName,
    roadNumber: null,
    distanceAlongM: fraction * 400,
    fraction,
    linkLengthM: 400,
    x: 1_700_000 + linkId,
    y: 5_400_000,
    lon: 174.7 + linkId / 1000,
    lat: -41.3,
    offsetM: 3,
    forwardAllowed: true,
    reverseAllowed: true,
    oneway: 2,
    stableKey: `key-${linkId}-${fraction}`,
  };
}

function snapped(
  h: SnapHandle,
  opts: { equivalent?: SnapHandle[]; alternatives?: SnapHandle[] } = {},
): SnapResult {
  const alternatives = opts.alternatives ?? [];
  return {
    snapshotId: 'snap-1',
    found: true,
    handle: h,
    candidates: [h, ...(opts.equivalent ?? []), ...alternatives],
    equivalentHosts: opts.equivalent ?? [],
    hostLinkIds: [h.linkId, ...(opts.equivalent ?? []).map((e) => e.linkId)],
    alternatives,
    ambiguous: alternatives.length > 0,
    ambiguityReason: alternatives.length
      ? 'two carriageways of Main Road are within 6 m of this click but 20 m apart. Choose which one the outage is on.'
      : null,
    snapModelVersion: '1.0.0',
  };
}

const NOT_FOUND: SnapResult = {
  snapshotId: 'snap-1',
  found: false,
  handle: null,
  candidates: [],
  equivalentHosts: [],
  hostLinkIds: [],
  alternatives: [],
  ambiguous: false,
  ambiguityReason: null,
  snapModelVersion: '1.0.0',
};

function candidate(id: string, lengthM: number, roads = 'Main Road'): SpanCandidate {
  return {
    candidateId: id,
    origin: 'shortest',
    lengthM,
    roads,
    linkIds: [1, 2],
    steps: [],
    evidence: {
      routeDesignationContinuous: true,
      roadNameContinuous: true,
      roadChanges: 0,
      codes: ['ROAD_NAME_CONTINUES'],
    },
  };
}

function corridorResult(
  candidates: SpanCandidate[],
  ambiguous = false,
): CorridorResult {
  return {
    snapshotId: 'snap-1',
    found: candidates.length > 0,
    corridor: candidates[0] ?? null,
    candidates,
    ambiguous,
    ambiguityReason: ambiguous ? 'Two corridors are equally well evidenced.' : null,
    corridorModelVersion: '1.0.0',
    attribution: 'AMDS',
    limitations: [],
  };
}

function analysisResult(corridorId: string, closedLength = 185.4): OutageAnalysis {
  return {
    snapshotId: 'snap-1',
    engine: 'v2-outage-span',
    algorithm: 'outage-span-v1',
    algorithmVersion: '1.0.0-dev',
    stability: 'foundation - disabled by default.',
    processingVersion: '2.0.0',
    codeProcessingVersion: '2.1.0',
    comparableToV1: false,
    comparableToV1Detail: 'V1 is retired.',
    handleA: handle(1, 0.5),
    handleB: handle(2, 0.5),
    corridor: candidate(corridorId, closedLength),
    corridorCandidates: [candidate(corridorId, closedLength)],
    corridorAmbiguous: false,
    corridorAmbiguityReason: null,
    closedLengthM: closedLength,
    measures: [
      {
        direction: 'a_to_b',
        status: 'OK',
        resolved: true,
        replacementDistanceM: 813.2,
        replacementTimeS: 60,
        addedDistanceM: 627.8,
        ratio: 4.385,
        detail: null,
        runtimeMs: 500,
      },
    ],
    headline: 'Replacement route found',
    isolation: null,
    isolationUnavailableReason: 'Gu cannot represent half a link.',
    sensitivity: null,
    sensitivityUnavailableReason: 'Not implemented in this foundation.',
    isSeparateFromCanonical: true,
    canonicalRouteSlot: 'canonical',
    qualityFlags: [],
    fingerprint: `fp-${corridorId}-${closedLength}`,
    runtimeMs: 1000,
    measurementCaveat: 'Structural only.',
    permalink: {
      snapshotId: 'snap-1',
      aLinkId: 1,
      aFraction: 0.5,
      bLinkId: 2,
      bFraction: 0.5,
      corridorId,
      directionMode: 'both',
      profile: 'car',
      metric: 'distance',
    },
    attribution: 'AMDS',
    limitations: [],
  };
}

/** A span with both handles placed and a corridor previewed. */
function withPreview(): SpanState {
  let s = spanReducer(EMPTY_SPAN, { type: 'place', which: 'a', snap: snapped(handle(1, 0.25)) });
  s = spanReducer(s, { type: 'place', which: 'b', snap: snapped(handle(2, 0.75)) });
  s = spanReducer(s, { type: 'request-issued', seq: 1 });
  s = spanReducer(s, {
    type: 'corridor-received',
    seq: 1,
    result: corridorResult([candidate('c1', 185.4)]),
  });
  return s;
}

describe('placing handles', () => {
  it('needs both before anything is asked of the server', () => {
    const s = spanReducer(EMPTY_SPAN, {
      type: 'place',
      which: 'a',
      snap: snapped(handle(1, 0.25)),
    });

    expect(s.status).toBe('placing');
    expect(isRequestable(s)).toBe(false);
  });

  it('asks for a corridor as soon as the second lands', () => {
    let s = spanReducer(EMPTY_SPAN, { type: 'place', which: 'a', snap: snapped(handle(1, 0.25)) });
    s = spanReducer(s, { type: 'place', which: 'b', snap: snapped(handle(2, 0.75)) });

    expect(s.status).toBe('corridor-pending');
    expect(isRequestable(s)).toBe(true);
  });

  it('reports a click that reached no road, without losing the other handle', () => {
    let s = spanReducer(EMPTY_SPAN, { type: 'place', which: 'a', snap: snapped(handle(1, 0.25)) });
    s = spanReducer(s, { type: 'place', which: 'b', snap: NOT_FOUND });

    expect(s.a).not.toBeNull();
    expect(s.b).toBeNull();
    expect(s.error).toMatch(/no road/i);
  });

  it('carries every equivalent host through to the request', () => {
    // A crossroads: the same coordinate on two roads. Which one hosts the
    // handle decides which road the outage runs along, so both must survive.
    const s = spanReducer(EMPTY_SPAN, {
      type: 'place',
      which: 'a',
      snap: snapped(handle(1, 0.5, 'Church Street'), {
        equivalent: [handle(9, 0.5, 'Queen Street')],
      }),
    });

    expect(handleRef(s.a!).equivalentHosts).toEqual([{ linkId: 9, fraction: 0.5 }]);
  });
});

describe('handle ambiguity', () => {
  it('is raised for rivals at a different place', () => {
    const s = spanReducer(EMPTY_SPAN, {
      type: 'place',
      which: 'a',
      snap: snapped(handle(1, 0.5), { alternatives: [handle(7, 0.5)] }),
    });

    expect(handleAmbiguity(s)).toHaveLength(1);
    expect(handleAmbiguity(s)[0].ambiguityReason).toMatch(/carriageways/);
  });

  it('is not raised for equivalent hosts at the same place', () => {
    const s = spanReducer(EMPTY_SPAN, {
      type: 'place',
      which: 'a',
      snap: snapped(handle(1, 0.5), { equivalent: [handle(9, 0.5)] }),
    });

    expect(handleAmbiguity(s)).toHaveLength(0);
  });
});

describe('dragging', () => {
  it('moves the handle without asking for anything', () => {
    let s = withPreview();
    s = spanReducer(s, { type: 'drag-start', which: 'a' });
    s = spanReducer(s, { type: 'drag-move', which: 'a', snap: snapped(handle(1, 0.4)) });

    expect(s.dragging).toBe('a');
    expect(s.a!.handle.fraction).toBe(0.4);
    // Still whatever it was; a drag does not itself issue a request.
    expect(s.pendingSeq).toBe(1);
  });

  it('marks the drawn preview as no longer current', () => {
    let s = withPreview();
    expect(s.previewStale).toBe(false);

    s = spanReducer(s, { type: 'drag-move', which: 'a', snap: snapped(handle(1, 0.4)) });

    expect(s.previewStale).toBe(true);
  });

  it('keeps the last good position when the pointer leaves the network', () => {
    let s = withPreview();
    s = spanReducer(s, { type: 'drag-start', which: 'a' });
    s = spanReducer(s, { type: 'drag-move', which: 'a', snap: NOT_FOUND });

    expect(s.a!.handle.fraction).toBe(0.25);
    expect(s.error).toBeNull();
  });

  it('asks for a fresh corridor only when the pointer is released', () => {
    let s = withPreview();
    s = spanReducer(s, { type: 'drag-start', which: 'a' });
    s = spanReducer(s, { type: 'drag-move', which: 'a', snap: snapped(handle(1, 0.4)) });
    expect(s.status).not.toBe('corridor-pending');

    s = spanReducer(s, { type: 'drag-end' });

    expect(s.dragging).toBeNull();
    expect(s.status).toBe('corridor-pending');
  });

  it('drops a pinned corridor, because the pin no longer describes these handles', () => {
    // Sent back with moved handles the server answers 409. Clearing it turns a
    // guaranteed error into a fresh choice.
    let s = withPreview();
    expect(s.corridorId).toBe('c1');

    s = spanReducer(s, { type: 'drag-move', which: 'b', snap: snapped(handle(2, 0.6)) });

    expect(s.corridorId).toBeNull();
  });
});

describe('stale responses', () => {
  it('applies the newest request', () => {
    let s = withPreview();
    s = spanReducer(s, { type: 'request-issued', seq: 7 });
    s = spanReducer(s, {
      type: 'analysis-received',
      seq: 7,
      result: analysisResult('c1', 185.4),
    });

    expect(s.status).toBe('ready');
    expect(s.analysis!.closedLengthM).toBe(185.4);
  });

  it('discards an older answer that lands after a newer one', () => {
    // The drag race, stated exactly: request 7 answers, then request 6's
    // answer arrives. Without the sequence check the screen would show 999 m.
    let s = withPreview();
    s = spanReducer(s, { type: 'request-issued', seq: 7 });
    s = spanReducer(s, {
      type: 'analysis-received',
      seq: 7,
      result: analysisResult('c1', 185.4),
    });

    const after = spanReducer(s, {
      type: 'analysis-received',
      seq: 6,
      result: analysisResult('c1', 999),
    });

    expect(after.analysis!.closedLengthM).toBe(185.4);
    expect(after).toBe(s);
  });

  it('discards a stale corridor the same way', () => {
    let s = withPreview();
    s = spanReducer(s, { type: 'request-issued', seq: 4 });

    const after = spanReducer(s, {
      type: 'corridor-received',
      seq: 3,
      result: corridorResult([candidate('old', 5000)]),
    });

    expect(after).toBe(s);
  });

  it('discards a stale failure, so an old error cannot blank a good result', () => {
    let s = withPreview();
    s = spanReducer(s, { type: 'request-issued', seq: 9 });
    s = spanReducer(s, {
      type: 'analysis-received',
      seq: 9,
      result: analysisResult('c1'),
    });

    const after = spanReducer(s, {
      type: 'request-failed',
      seq: 8,
      message: 'network died',
    });

    expect(after.status).toBe('ready');
    expect(after.error).toBeNull();
  });

  it('records a current failure', () => {
    let s = withPreview();
    s = spanReducer(s, { type: 'request-issued', seq: 5 });
    s = spanReducer(s, { type: 'request-failed', seq: 5, message: 'timeout' });

    expect(s.status).toBe('error');
    expect(s.error).toBe('timeout');
  });
});

describe('corridor choice', () => {
  it('offers alternatives only when the evidence does not separate them', () => {
    let s = withPreview();
    expect(corridorChoices(s)).toEqual([]);

    s = spanReducer(s, { type: 'request-issued', seq: 2 });
    s = spanReducer(s, {
      type: 'corridor-received',
      seq: 2,
      result: corridorResult([candidate('n', 400), candidate('s', 400)], true),
    });

    expect(corridorChoices(s)).toHaveLength(2);
  });

  it('follows the ranking when nothing is pinned', () => {
    const s = withPreview();
    expect(s.corridorId).toBe('c1');
  });

  it('honours an explicit choice', () => {
    let s = spanReducer(EMPTY_SPAN, { type: 'place', which: 'a', snap: snapped(handle(1, 0.25)) });
    s = spanReducer(s, { type: 'place', which: 'b', snap: snapped(handle(2, 0.75)) });
    s = spanReducer(s, { type: 'request-issued', seq: 1 });
    s = spanReducer(s, {
      type: 'corridor-received',
      seq: 1,
      result: corridorResult([candidate('north', 400), candidate('south', 400)], true),
    });

    s = spanReducer(s, { type: 'choose-corridor', candidateId: 'south' });

    expect(s.corridorId).toBe('south');
    expect(s.status).toBe('analysis-pending');
  });

  it('ignores a choice that is not on offer', () => {
    // A stale click must not put the editor into a state the server refuses.
    const s = withPreview();
    const after = spanReducer(s, { type: 'choose-corridor', candidateId: 'nope' });

    expect(after).toBe(s);
  });

  it('says so when no corridor connects the two points', () => {
    let s = spanReducer(EMPTY_SPAN, { type: 'place', which: 'a', snap: snapped(handle(1, 0.25)) });
    s = spanReducer(s, { type: 'place', which: 'b', snap: snapped(handle(2, 0.75)) });
    s = spanReducer(s, { type: 'request-issued', seq: 1 });
    s = spanReducer(s, { type: 'corridor-received', seq: 1, result: corridorResult([]) });

    expect(s.status).toBe('error');
    expect(s.error).toMatch(/no road connects/i);
  });
});

describe('scenario and direction', () => {
  it('re-measures on a direction change but keeps the corridor', () => {
    // Direction changes which arcs are closed, not which road is closed.
    let s = withPreview();
    s = spanReducer(s, { type: 'set-direction', direction: 'a_to_b' });

    expect(s.direction).toBe('a_to_b');
    expect(s.corridorId).toBe('c1');
    expect(s.status).toBe('analysis-pending');
  });

  it('re-selects the corridor on a vehicle change', () => {
    // A different vehicle can use a different set of links, so the corridor
    // itself may differ.
    let s = withPreview();
    s = spanReducer(s, { type: 'set-scenario', vehicle: 'heavy', metric: 'distance' });

    expect(s.corridorId).toBeNull();
    expect(s.status).toBe('corridor-pending');
  });

  it('keeps the corridor when only the metric changes', () => {
    let s = withPreview();
    s = spanReducer(s, { type: 'set-scenario', vehicle: 'car', metric: 'time' });

    expect(s.corridorId).toBe('c1');
  });

  it('ignores a no-op change', () => {
    const s = withPreview();
    expect(spanReducer(s, { type: 'set-direction', direction: 'both' })).toBe(s);
  });
});

describe('restoring a shared span', () => {
  it('starts from the pinned corridor rather than re-ranking', () => {
    const a = { handle: handle(1, 0.25), equivalentHosts: [], alternatives: [], ambiguous: false, ambiguityReason: null };
    const b = { handle: handle(2, 0.75), equivalentHosts: [], alternatives: [], ambiguous: false, ambiguityReason: null };

    const s = spanReducer(EMPTY_SPAN, {
      type: 'restore',
      a,
      b,
      corridorId: 'shared-corridor',
      direction: 'a_to_b',
      vehicle: 'heavy',
      metric: 'time',
    });

    expect(s.corridorId).toBe('shared-corridor');
    expect(s.direction).toBe('a_to_b');
    expect(s.vehicle).toBe('heavy');
    expect(s.metric).toBe('time');
    expect(s.status).toBe('analysis-pending');
  });

  it('clears everything, keeping the reader’s scenario', () => {
    let s = withPreview();
    s = spanReducer(s, { type: 'set-scenario', vehicle: 'heavy', metric: 'time' });
    s = spanReducer(s, { type: 'clear' });

    expect(s.a).toBeNull();
    expect(s.b).toBeNull();
    expect(s.status).toBe('empty');
    expect(s.vehicle).toBe('heavy');
    expect(s.metric).toBe('time');
  });
});

describe('length reporting', () => {
  it('uses the corridor while only a preview exists', () => {
    expect(closedLengthM(withPreview())).toBe(185.4);
  });

  it('uses the analysis once it has one', () => {
    let s = withPreview();
    s = spanReducer(s, { type: 'request-issued', seq: 2 });
    s = spanReducer(s, { type: 'analysis-received', seq: 2, result: analysisResult('c1', 190) });

    expect(closedLengthM(s)).toBe(190);
  });

  it('remarks on an unusually long span without altering it', () => {
    let s = spanReducer(EMPTY_SPAN, { type: 'place', which: 'a', snap: snapped(handle(1, 0)) });
    s = spanReducer(s, { type: 'place', which: 'b', snap: snapped(handle(2, 1)) });
    s = spanReducer(s, { type: 'request-issued', seq: 1 });
    s = spanReducer(s, {
      type: 'corridor-received',
      seq: 1,
      result: corridorResult([candidate('long', 452_657)]),
    });

    expect(isUnusuallyLong(s)).toBe(true);
    // Reported as it is - never capped or truncated.
    expect(closedLengthM(s)).toBe(452_657);
  });

  it('does not remark on an ordinary one', () => {
    expect(isUnusuallyLong(withPreview())).toBe(false);
  });
});
