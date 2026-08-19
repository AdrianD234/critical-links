/**
 * The two-point outage editor's state, as a pure reducer.
 *
 * All of the interaction's hard parts are decisions about ordering, and none of
 * them need a map to test:
 *
 *   - a drag produces a stream of positions, and analysing each one would put
 *     a one-second national search behind every mouse move;
 *   - the answers to those requests arrive out of order, and an older one
 *     landing last is confidently wrong output that looks entirely normal;
 *   - a corridor id is only valid for the handles it was chosen for, so moving
 *     a handle invalidates a pin that a permalink may be carrying;
 *   - Back and Forward must restore the span that was shared, not re-derive
 *     one that happens to rank first today.
 *
 * So the reducer is the contract and the map is a renderer. Everything below is
 * exercised by `tests/unit/span-state.test.ts` with no browser involved.
 *
 * WHAT `seq` IS FOR
 * -----------------
 * Every request carries the sequence number it was issued with, and a response
 * is applied only when it is still the newest one issued. Cancellation via
 * `AbortSignal` is the first line of defence and is not sufficient on its own:
 * an abort races the response, and a request that has already resolved cannot
 * be called back. The check here is what makes late arrival harmless rather
 * than merely unlikely.
 */

import type {
  CorridorResult,
  DirectionMode,
  HandleId,
  OutageAnalysis,
  SnapHandle,
  SnapResult,
  SpanCandidate,
  SpanHandleRef,
} from '../api/outage.js';
import type { Metric, Vehicle } from '../api/scenario.js';

/**
 * One placed handle: where it is, and every link that could legitimately host
 * it. `equivalentHosts` travels with it to corridor selection - see
 * `SnapResult`.
 */
export interface PlacedHandle {
  handle: SnapHandle;
  equivalentHosts: SnapHandle[];
  /** Rivals at a DIFFERENT place. Non-empty means the user may need to choose. */
  alternatives: SnapHandle[];
  ambiguous: boolean;
  ambiguityReason: string | null;
}

export type SpanStatus =
  | 'empty'
  | 'placing'
  | 'corridor-pending'
  | 'preview'
  | 'analysis-pending'
  | 'ready'
  | 'error';

export interface SpanState {
  a: PlacedHandle | null;
  b: PlacedHandle | null;
  direction: DirectionMode;
  vehicle: Vehicle;
  metric: Metric;

  /** Pinned corridor. Set by an explicit choice or restored from a permalink. */
  corridorId: string | null;
  corridor: CorridorResult | null;
  analysis: OutageAnalysis | null;

  /** Which handle is under the pointer, if any. */
  dragging: HandleId | null;
  /**
   * True while the drawn preview describes an older position than the handles
   * do. The map keeps drawing it - a preview that vanishes on every mouse move
   * is worse than one briefly out of date - but it is labelled rather than
   * presented as current.
   */
  previewStale: boolean;

  status: SpanStatus;
  error: string | null;
  /** The newest request issued. A response with any other value is stale. */
  pendingSeq: number;
  /** The last sequence whose result was applied. */
  appliedSeq: number;
}

export const EMPTY_SPAN: SpanState = {
  a: null,
  b: null,
  direction: 'both',
  vehicle: 'car',
  metric: 'distance',
  corridorId: null,
  corridor: null,
  analysis: null,
  dragging: null,
  previewStale: false,
  status: 'empty',
  error: null,
  pendingSeq: 0,
  appliedSeq: 0,
};

export type SpanAction =
  | { type: 'place'; which: HandleId; snap: SnapResult }
  | { type: 'drag-start'; which: HandleId }
  | { type: 'drag-move'; which: HandleId; snap: SnapResult }
  | { type: 'drag-end' }
  | { type: 'set-direction'; direction: DirectionMode }
  | { type: 'set-scenario'; vehicle: Vehicle; metric: Metric }
  | { type: 'choose-corridor'; candidateId: string }
  | { type: 'request-issued'; seq: number }
  | { type: 'corridor-received'; seq: number; result: CorridorResult }
  | { type: 'analysis-received'; seq: number; result: OutageAnalysis }
  | { type: 'request-failed'; seq: number; message: string }
  | { type: 'restore'; a: PlacedHandle; b: PlacedHandle; corridorId: string;
      direction: DirectionMode; vehicle: Vehicle; metric: Metric }
  | { type: 'clear' };

function placed(snap: SnapResult): PlacedHandle | null {
  if (!snap.found || !snap.handle) return null;
  return {
    handle: snap.handle,
    equivalentHosts: snap.equivalentHosts,
    alternatives: snap.alternatives,
    ambiguous: snap.ambiguous,
    ambiguityReason: snap.ambiguityReason,
  };
}

/**
 * Moving a handle invalidates a pinned corridor.
 *
 * The pin names a corridor that was generated for particular handle positions.
 * Sent back with different ones the server answers 409 - correctly, because it
 * cannot know the client meant "the corresponding corridor" rather than "this
 * exact one". Clearing it here turns a guaranteed error into a fresh choice.
 */
function movedHandles(s: SpanState): Partial<SpanState> {
  return { corridorId: null, previewStale: true };
}

function bothPlaced(s: SpanState): boolean {
  return s.a !== null && s.b !== null;
}

export function spanReducer(state: SpanState, action: SpanAction): SpanState {
  switch (action.type) {
    case 'place': {
      const handle = placed(action.snap);
      if (!handle) {
        return { ...state, status: state.status === 'empty' ? 'empty' : state.status,
                 error: 'No road within reach of that click.' };
      }
      const next: SpanState = {
        ...state,
        ...movedHandles(state),
        [action.which]: handle,
        error: null,
      } as SpanState;
      next.status = bothPlaced(next) ? 'corridor-pending' : 'placing';
      return next;
    }

    case 'drag-start':
      return { ...state, dragging: action.which };

    case 'drag-move': {
      const handle = placed(action.snap);
      // A drag that leaves the network keeps the last good position rather
      // than dropping the handle. The pointer is still down; there is nowhere
      // sensible to put it and nothing to gain from clearing the span.
      if (!handle) return state;
      return {
        ...state,
        ...movedHandles(state),
        [action.which]: handle,
        error: null,
      } as SpanState;
    }

    case 'drag-end':
      if (state.dragging === null) return state;
      return {
        ...state,
        dragging: null,
        status: bothPlaced(state) ? 'corridor-pending' : 'placing',
      };

    case 'set-direction':
      if (action.direction === state.direction) return state;
      return {
        ...state,
        direction: action.direction,
        // The corridor does not change with direction - only which arcs of it
        // are closed - so the pin survives and the preview stays current.
        status: bothPlaced(state) ? 'analysis-pending' : state.status,
      };

    case 'set-scenario':
      if (action.vehicle === state.vehicle && action.metric === state.metric) {
        return state;
      }
      return {
        ...state,
        vehicle: action.vehicle,
        metric: action.metric,
        // Vehicle changes which links are usable, so the corridor itself may
        // differ. Re-select rather than re-measure.
        corridorId: action.vehicle === state.vehicle ? state.corridorId : null,
        status: bothPlaced(state) ? 'corridor-pending' : state.status,
      };

    case 'choose-corridor': {
      const chosen = state.corridor?.candidates.find(
        (c) => c.candidateId === action.candidateId,
      );
      // Choosing something that is not on offer is a bug in the caller, not a
      // state to enter. Ignored rather than pinned, so a stale click cannot
      // put the editor into a state the server will refuse.
      if (!chosen) return state;
      return {
        ...state,
        corridorId: action.candidateId,
        status: 'analysis-pending',
      };
    }

    case 'request-issued':
      return { ...state, pendingSeq: action.seq, error: null };

    case 'corridor-received': {
      if (action.seq !== state.pendingSeq) return state; // stale
      if (!action.result.found || !action.result.corridor) {
        return {
          ...state,
          corridor: action.result,
          appliedSeq: action.seq,
          previewStale: false,
          status: 'error',
          error: 'No road connects these two points in this network.',
        };
      }
      return {
        ...state,
        corridor: action.result,
        appliedSeq: action.seq,
        previewStale: false,
        // An explicit pin survives; otherwise follow the ranking.
        corridorId: state.corridorId ?? action.result.corridor.candidateId,
        status: 'analysis-pending',
      };
    }

    case 'analysis-received': {
      if (action.seq !== state.pendingSeq) return state; // stale
      /* A restored span arrives as an analysis with no handles placed - the
       * URL stores positions, and /analysis returns them resolved in full.
       * Adopting them here is what puts the A and B markers on the map after
       * a reload; without it the numbers appear and the handles do not, and
       * the span cannot be adjusted. Never overwrites a handle the user
       * placed: equivalent hosts and ambiguity live only on those. */
      const adopt = (h: SnapHandle): PlacedHandle => ({
        handle: h,
        equivalentHosts: [],
        alternatives: [],
        ambiguous: false,
        ambiguityReason: null,
      });
      return {
        ...state,
        a: state.a ?? adopt(action.result.handleA),
        b: state.b ?? adopt(action.result.handleB),
        analysis: action.result,
        appliedSeq: action.seq,
        previewStale: false,
        corridorId: action.result.corridor.candidateId,
        status: 'ready',
      };
    }

    case 'request-failed':
      if (action.seq !== state.pendingSeq) return state; // stale
      return {
        ...state,
        appliedSeq: action.seq,
        status: 'error',
        error: action.message,
      };

    case 'restore':
      return {
        ...EMPTY_SPAN,
        a: action.a,
        b: action.b,
        corridorId: action.corridorId,
        direction: action.direction,
        vehicle: action.vehicle,
        metric: action.metric,
        status: 'analysis-pending',
      };

    case 'clear':
      return { ...EMPTY_SPAN, vehicle: state.vehicle, metric: state.metric };

    default:
      return state;
  }
}

/** The reference a request needs, including every equivalent host. */
export function handleRef(p: PlacedHandle): SpanHandleRef {
  return {
    linkId: p.handle.linkId,
    fraction: p.handle.fraction,
    equivalentHosts: p.equivalentHosts.map((h) => ({
      linkId: h.linkId,
      fraction: h.fraction,
    })),
  };
}

/** True when the span is complete enough to ask the server about. */
export function isRequestable(s: SpanState): s is SpanState & {
  a: PlacedHandle;
  b: PlacedHandle;
} {
  return s.a !== null && s.b !== null;
}

/**
 * Is either handle a genuine choice the user should settle?
 *
 * Only `alternatives` count. Equivalent hosts are the same place and are
 * resolved by corridor selection, not by asking.
 */
export function handleAmbiguity(s: SpanState): PlacedHandle[] {
  return [s.a, s.b].filter((h): h is PlacedHandle => h !== null && h.ambiguous);
}

/**
 * The corridors worth offering as a choice.
 *
 * Empty unless the server said the evidence does not separate them: a list of
 * alternatives shown when one is clearly best invites second-guessing a
 * decision that was not close.
 */
export function corridorChoices(s: SpanState): SpanCandidate[] {
  if (!s.corridor?.ambiguous) return [];
  return s.corridor.candidates;
}

/** How long the outage is, from the best information currently held. */
export function closedLengthM(s: SpanState): number | null {
  if (s.analysis && !s.previewStale) return s.analysis.closedLengthM;
  const chosen = s.corridor?.candidates.find(
    (c) => c.candidateId === s.corridorId,
  );
  return chosen?.lengthM ?? s.corridor?.corridor?.lengthM ?? null;
}

/**
 * Beyond this, a span is long enough to be worth remarking on.
 *
 * Two handles far apart on one route will happily close hundreds of
 * kilometres, and that is a legitimate thing to ask. It is NOT silently capped
 * or truncated - the length is shown as it is, and this only decides whether
 * to say "that is a very long closure" beside it.
 */
export const LONG_SPAN_M = 50_000;

export function isUnusuallyLong(s: SpanState): boolean {
  const length = closedLengthM(s);
  return length !== null && length > LONG_SPAN_M;
}
