/*
 * What the snapshot records about the selected link.
 *
 * Attributes are shown as they are stored, including the ones that are absent.
 * `speedKph: null` with a source of `estimated` is a more useful thing to see
 * than a silently substituted default, because it explains why the added-time
 * figure carries an "estimated" tag.
 */

import { inlineMetres } from '../lib/format.js';
import type { LinkSummary } from '../api/types.js';

export default function LinkAttributes({ link }: { link: LinkSummary }) {
  const rows: [string, string][] = [
    ['Length', inlineMetres(link.lengthM)],
    ['Direction', link.oneway ? 'One-way' : 'Two-way'],
    [
      'Represented travel',
      [
        link.forwardAllowed ? 'forward' : null,
        link.reverseAllowed ? 'reverse' : null,
      ]
        .filter(Boolean)
        .join(', ') || 'none',
    ],
    ['Road controlling authority', link.rca ?? '—'],
    ['Asset type', link.modelAssetTypeName ?? '—'],
    ['Surface', link.surfaceTypeName ?? '—'],
    [
      'Speed',
      link.speedKph === null
        ? `Not published (${link.speedSource})`
        : `${link.speedKph} km/h (${link.speedSource})`,
    ],
    ['Heavy vehicles', link.modeVehicleHeavy ? 'Permitted' : 'Not permitted'],
    ['Emergency access', link.modeEmergency ? 'Yes' : 'No'],
    ['Lifeline route', link.lifeLineRoute ? 'Yes' : 'No'],
    ['In analysis area', link.inAnalysisArea ? 'Yes' : 'No'],
  ];

  return (
    <dl className="kv">
      {rows.map(([k, v]) => (
        <div key={k} style={{ display: 'contents' }}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}
