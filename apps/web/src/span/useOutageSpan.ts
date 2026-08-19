/**
 * The editor's controller: the reducer wired to the network.
 *
 * `spanState.ts` decides WHAT should happen; this decides WHEN, and the two are
 * kept apart so the ordering rules stay testable without a browser.
 *
 * Three things happen here that the reducer cannot do on its own:
 *
 *   1. a request is issued with a sequence number, and the same number comes
 *      back with the answer, so the reducer can drop anything superseded;
 *   2. the previous request is aborted, which is the first line of defence and
 *      not the last - an abort races the response;
 *   3. nothing is issued while the pointer is down, and a short debounce after
 *      it lifts absorbs the last few positions of a drag.
 *
 * The corridor is fetched before the analysis on purpose. It costs about 25 ms
 * nationally against roughly a second for the measurement, so the red preview
 * of what is about to be closed appears immediately and the number follows.
 * Drawing the closure only once the analysis returned would leave the user
 * watching an unexplained pause with nothing selected on the map.
 */

import { useCallback, useEffect, useReducer, useRef } from 'react';

import * as outage from '../api/outage.js';
import type { DirectionMode, HandleId, SnapResult } from '../api/outage.js';
import type { Metric, Vehicle } from '../api/scenario.js';
import {
  EMPTY_SPAN,
  handleRef,
  isRequestable,
  spanReducer,
  type SpanState,
} from './spanState.js';

/**
 * How long to wait after the pointer lifts before asking the server.
 *
 * Short enough to feel immediate, long enough that releasing and immediately
 * nudging the handle again does not pay for two national searches. It is not a
 * throttle on the drag itself: nothing is requested during a drag at all.
 */
export const SETTLE_MS = 220;

export interface OutageSpanController {
  state: SpanState;
  place: (which: HandleId, snap: SnapResult) => void;
  dragStart: (which: HandleId) => void;
  dragMove: (which: HandleId, snap: SnapResult) => void;
  dragEnd: () => void;
  setDirection: (direction: DirectionMode) => void;
  chooseCorridor: (candidateId: string) => void;
  clear: () => void;
  restore: (span: {
    aLinkId: number;
    aFraction: number;
    bLinkId: number;
    bFraction: number;
    corridorId: string;
    direction: DirectionMode;
    vehicle: Vehicle;
    metric: Metric;
  }) => void;
}

export function useOutageSpan(
  vehicle: Vehicle,
  metric: Metric,
): OutageSpanController {
  const [state, dispatch] = useReducer(spanReducer, {
    ...EMPTY_SPAN,
    vehicle,
    metric,
  });

  /* One controller for the whole editor. Issuing a request aborts whatever was
   * in flight, whichever phase it was in. */
  const inFlight = useRef<AbortController | null>(null);
  const seq = useRef(0);

  useEffect(() => {
    dispatch({ type: 'set-scenario', vehicle, metric });
  }, [vehicle, metric]);

  /* Abort on unmount. A request that outlives the editor cannot be applied and
   * has nothing to tell anyone. */
  useEffect(() => () => inFlight.current?.abort(), []);

  const pending = state.status === 'corridor-pending' || state.status === 'analysis-pending';

  useEffect(() => {
    if (!pending || state.dragging !== null || !isRequestable(state)) return;

    const timer = window.setTimeout(() => {
      inFlight.current?.abort();
      const controller = new AbortController();
      inFlight.current = controller;

      const mine = ++seq.current;
      dispatch({ type: 'request-issued', seq: mine });

      const a = handleRef(state.a);
      const b = handleRef(state.b);

      const run = async () => {
        try {
          if (state.status === 'corridor-pending') {
            const result = await outage.corridor(
              a, b, state.vehicle, controller.signal,
            );
            dispatch({ type: 'corridor-received', seq: mine, result });
            return;
          }
          const result = await outage.analysis(
            a, b,
            {
              vehicle: state.vehicle,
              metric: state.metric,
              direction: state.direction,
              corridorId: state.corridorId,
              geometry: true,
            },
            controller.signal,
          );
          dispatch({ type: 'analysis-received', seq: mine, result });
        } catch (err) {
          // An abort is the expected path, not a failure: the user moved on.
          // Reporting it would put an error on screen for working as intended.
          if (controller.signal.aborted) return;
          dispatch({
            type: 'request-failed',
            seq: mine,
            message: err instanceof Error ? err.message : 'Request failed.',
          });
        }
      };
      void run();
    }, SETTLE_MS);

    return () => window.clearTimeout(timer);
    // `state` is read whole inside the effect; the dependencies below are the
    // things that decide whether a NEW request is owed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    pending,
    state.dragging,
    state.status,
    state.a?.handle.linkId,
    state.a?.handle.fraction,
    state.b?.handle.linkId,
    state.b?.handle.fraction,
    state.direction,
    state.vehicle,
    state.metric,
    state.corridorId,
  ]);

  return {
    state,
    place: useCallback(
      (which, snap) => dispatch({ type: 'place', which, snap }),
      [],
    ),
    dragStart: useCallback((which) => dispatch({ type: 'drag-start', which }), []),
    dragMove: useCallback(
      (which, snap) => dispatch({ type: 'drag-move', which, snap }),
      [],
    ),
    dragEnd: useCallback(() => dispatch({ type: 'drag-end' }), []),
    setDirection: useCallback(
      (direction) => dispatch({ type: 'set-direction', direction }),
      [],
    ),
    chooseCorridor: useCallback(
      (candidateId) => dispatch({ type: 'choose-corridor', candidateId }),
      [],
    ),
    clear: useCallback(() => dispatch({ type: 'clear' }), []),
    restore: useCallback((span) => {
      /* Restoration goes through `/analysis`, which takes exactly what the URL
       * stores and returns both handles in full - so nothing has to be
       * re-snapped, and a pinned corridor the server no longer offers comes
       * back as a 409 rather than as a different road. */
      inFlight.current?.abort();
      const controller = new AbortController();
      inFlight.current = controller;
      const mine = ++seq.current;

      const ref = (linkId: number, fraction: number) => ({
        linkId, fraction, equivalentHosts: [],
      });

      dispatch({ type: 'request-issued', seq: mine });
      void outage
        .analysis(
          ref(span.aLinkId, span.aFraction),
          ref(span.bLinkId, span.bFraction),
          {
            vehicle: span.vehicle,
            metric: span.metric,
            direction: span.direction,
            corridorId: span.corridorId,
            geometry: true,
          },
          controller.signal,
        )
        .then((result) => {
          dispatch({ type: 'analysis-received', seq: mine, result });
        })
        .catch((err: unknown) => {
          if (controller.signal.aborted) return;
          dispatch({
            type: 'request-failed',
            seq: mine,
            message:
              err instanceof Error
                ? err.message
                : 'This shared span could not be restored.',
          });
        });
    }, []),
  };
}
