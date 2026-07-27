/**
 * Configuration, resolved from environment with discovered defaults.
 *
 * The AMDS ids below are not guesses: they were found by the discovery
 * pipeline and are recorded with evidence in docs/SOURCE_DISCOVERY.md. They
 * remain overridable because NZTA may republish the service.
 */

import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';

// Tiny .env loader - avoids a dependency for four variables.
function loadDotEnv(): void {
  const p = path.join(process.cwd(), '.env');
  if (!existsSync(p)) return;
  for (const line of readFileSync(p, 'utf8').split(/\r?\n/)) {
    const m = /^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$/.exec(line);
    if (!m) continue;
    const key = m[1];
    let val = m[2];
    if (
      (val.startsWith('"') && val.endsWith('"')) ||
      (val.startsWith("'") && val.endsWith("'"))
    ) {
      val = val.slice(1, -1);
    }
    if (process.env[key] === undefined) process.env[key] = val;
  }
}
loadDotEnv();

export const config = {
  amds: {
    /** ArcGIS Online item id of the public Feature Service. */
    itemId: process.env.AMDS_ITEM_ID ?? 'f955c118272b462e9ce757405890b87f',
    /** ArcGIS Online item id of the Experience Builder app (entry point). */
    experienceItemId:
      process.env.AMDS_EXPERIENCE_ITEM_ID ?? 'c720e30739154520bc7d7c0fbfb2b6e5',
    serviceUrl:
      process.env.AMDS_FEATURE_SERVICE_URL ??
      'https://services.arcgis.com/CXBb7LAjgIIdcsPt/arcgis/rest/services/AMDS_NetworkModel_PROD/FeatureServer',
    linkLayerId: Number(process.env.AMDS_LINK_LAYER_ID ?? 1),
    restrictedTurnTableId: Number(process.env.AMDS_RESTRICTED_TURN_TABLE_ID ?? 9),
    authorityTableId: Number(process.env.AMDS_AUTHORITY_TABLE_ID ?? 2),
    routeNameTableId: Number(process.env.AMDS_ROUTENAME_TABLE_ID ?? 13),
  },
  sharingApi: 'https://www.arcgis.com/sharing/rest',
  dataDir: path.resolve(process.env.DATA_DIR ?? './data'),
  apiPort: Number(process.env.API_PORT ?? 8787),
  applicationBaseUrl: process.env.APPLICATION_BASE_URL ?? 'http://localhost:5173',
  /** EPSG:2193. All analysis happens here. */
  analysisSrid: 2193,
};

/**
 * Licence and attribution. Captured verbatim from the ArcGIS item during
 * discovery and re-checked on every ingest; see docs/LICENSING.md.
 */
export const DEFAULT_ATTRIBUTION =
  'Contains data sourced from the NZTA Waka Kotahi AMDS Network Model, maintained by New Zealand Road Controlling Authorities, the Department of Conservation and NZTA.';
