/*
 * The direction tag and the represented-network status.
 *
 * THREE KINDS OF STATUS, NEVER CONFLATED
 *
 *   ok       a replacement path exists — route teal
 *   finding  DISCONNECTED: the network genuinely has no alternative. This is a
 *            *result*, arguably the most important one the tool produces, and
 *            it must not look like an error. Amber.
 *   fault    the analysis failed — timeout, invalid graph, bad source data,
 *            transport error. Neutral charcoal plus the status code in
 *            monospace, because closure red is reserved for the closure.
 *
 * The wording is "represented-network alternative found", not "route found".
 * What was computed is a path through the network *as represented in this
 * snapshot*, and the snapshot is a clipped, pre-hardening extract.
 */

export type StatusKind = 'ok' | 'finding' | 'fault';

const FINDINGS = new Set(['DISCONNECTED']);

const FAULTS = new Set([
  'UNRESOLVED_TIMEOUT',
  'INVALID_GRAPH',
  'SOURCE_DATA_ERROR',
  'UNSUPPORTED_PROFILE',
  'API_ERROR',
]);

export function statusKindOf(status: string): StatusKind {
  if (status === 'OK') return 'ok';
  if (FINDINGS.has(status)) return 'finding';
  if (FAULTS.has(status)) return 'fault';
  /* An unrecognised code is a fault: showing it as success would be a
   * guess in the one direction that misleads. */
  return 'fault';
}

const LABELS: Record<string, string> = {
  OK: 'Represented-network alternative found',
  DISCONNECTED: 'No replacement path in the represented network',
  UNRESOLVED_TIMEOUT: 'Analysis did not finish',
  INVALID_GRAPH: 'Graph could not be evaluated',
  SOURCE_DATA_ERROR: 'Source data could not be used',
  UNSUPPORTED_PROFILE: 'Not supported for this vehicle profile',
  API_ERROR: 'Analysis service error',
};

export function statusLabel(status: string): string {
  return LABELS[status] ?? 'Unrecognised result status';
}

export default function ResultStatus({
  direction,
  status,
  meaning,
}: {
  direction: string;
  status: string;
  meaning?: string | null;
}) {
  const kind = statusKindOf(status);

  return (
    <div className="status-row">
      <span className="dir-tag">{direction}</span>
      <span
        className="status-pill"
        data-kind={kind}
        title={meaning || undefined}
      >
        {statusLabel(status)}
        {kind === 'fault' && <span className="code">{status}</span>}
      </span>
    </div>
  );
}
