/*
 * Quality flags, with what each one means.
 *
 * A bare flag like SPEED_ESTIMATED is a machine token. Shown alone it looks
 * like reassuring technical detail; explained, it is a caveat about a figure
 * on screen. The explanation is the point.
 */

const MEANINGS: Record<string, string> = {
  SPEED_ESTIMATED:
    'AMDS publishes no speed for this link. Travel times use an estimated ' +
    'speed derived from the road class, so every time figure is approximate.',
  CLIPPED_EXTRACT:
    'This snapshot covers a clipped area. Roads outside it are absent from ' +
    'the graph, so a replacement path that would leave the extract cannot be ' +
    'found and the link may appear more critical than it is.',
  NO_ROAD_NAME: 'The source feature carries no road name.',
  GEOMETRY_REPAIRED:
    'The source geometry was invalid and was repaired during ingest. The ' +
    'repaired shape is what the analysis measured.',
  SPLIT_AT_JUNCTION:
    'This link was split where another link ended on its interior, so the ' +
    'graph connects at the junction. The split does not change total length.',
  SHORT_LINK:
    'The link is short enough that snapping tolerances could affect which ' +
    'node it connects to.',
  RELIES_ON_UNRESOLVED_CROSSING:
    'This route drives through at least one crossing the classifier could ' +
    'not resolve — a point where two roads cross with no shared node and no ' +
    'evidence saying whether they meet at grade or one passes over the ' +
    'other. It was found on the sensitivity graph, which assumes they meet, ' +
    'and it is NOT the published answer. The provenance block lists each ' +
    'crossing with its coordinates, so the assumption can be checked against ' +
    'aerial photography.',
};

export default function QualityFlags({ flags }: { flags: string[] }) {
  if (!flags.length) {
    return (
      <p style={{ fontSize: 'var(--text-control)', color: 'var(--panel-muted)' }}>
        No quality flags were raised for this link or result.
      </p>
    );
  }

  return (
    <div style={{ display: 'grid', gap: 10 }}>
      {flags.map((f) => (
        <div key={f}>
          <div className="flags">
            <span className="flag">{f}</span>
          </div>
          <p
            style={{
              marginTop: 5,
              fontSize: 'var(--text-meta)',
              color: 'var(--panel-muted)',
              lineHeight: 'var(--leading-relaxed)',
            }}
          >
            {MEANINGS[f] ?? 'No description is recorded for this flag.'}
          </p>
        </div>
      ))}
    </div>
  );
}
