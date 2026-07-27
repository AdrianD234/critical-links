/**
 * OpenAPI 3.1 description of the detour API.
 *
 * Hand-written rather than generated so that the prose describing what each
 * number means, and what it does not mean, lives in the contract itself.
 */

export function openApiDocument() {
  return {
    openapi: '3.1.0',
    info: {
      title: 'NZ Road Criticality and Detour API',
      version: '0.1.0',
      description:
        'Structural road-network resilience over the NZTA AMDS Network Model. ' +
        'Returns shortest replacement paths for a closed road link. ' +
        'This is NOT a traffic assignment model: it does not predict traffic volumes ' +
        'on alternative routes.',
    },
    servers: [{ url: 'http://localhost:8787' }],
    paths: {
      '/health': {
        get: {
          summary: 'Liveness and loaded-snapshot summary',
          responses: { '200': { description: 'ok' } },
        },
      },
      '/api/v1/network/metadata': {
        get: {
          summary: 'Active snapshot provenance, graph totals and limitations',
          responses: { '200': { description: 'metadata' } },
        },
      },
      '/api/v1/network/snapshots': {
        get: {
          summary: 'List available snapshots',
          responses: { '200': { description: 'snapshot ids' } },
        },
      },
      '/api/v1/links/search': {
        get: {
          summary: 'Find links by road name, AMDS id, RCA or bounding box',
          parameters: [
            { name: 'name', in: 'query', schema: { type: 'string' }, description: 'Road-name substring, case insensitive' },
            { name: 'amdsId', in: 'query', schema: { type: 'string' }, description: 'AMDS identifier substring' },
            { name: 'rca', in: 'query', schema: { type: 'integer' }, description: 'AMDS assetOwnerOrganisation code (1 = NZTA)' },
            { name: 'bbox', in: 'query', schema: { type: 'string' }, description: 'minLon,minLat,maxLon,maxLat in WGS84' },
            { name: 'limit', in: 'query', schema: { type: 'integer', default: 50, maximum: 500 } },
          ],
          responses: { '200': { description: 'matching links' } },
        },
      },
      '/api/v1/links/{id}': {
        get: {
          summary: 'Link attributes, closure-group membership and geometry',
          parameters: [
            { name: 'id', in: 'path', required: true, schema: { type: 'string' }, description: 'AMDS id or internal link id' },
          ],
          responses: { '200': { description: 'link' }, '404': { description: 'unknown link' } },
        },
      },
      '/api/v1/links/{id}/detour': {
        get: {
          summary: 'Close a link and compute the replacement path',
          description:
            'Returns, per direction: status, distance and time metrics, the corridor ' +
            'measure where the endpoint measure is undefined, an isolation profile ' +
            'where nothing is stranded, route GeoJSON, quality flags and provenance. ' +
            'A DISCONNECTED status means no replacement path exists between the link\'s ' +
            'own endpoints; it is never returned for a timeout or an application error.',
          parameters: [
            { name: 'id', in: 'path', required: true, schema: { type: 'string' } },
            { name: 'metric', in: 'query', schema: { type: 'string', enum: ['distance', 'time'], default: 'distance' } },
            { name: 'vehicle', in: 'query', schema: { type: 'string', enum: ['car', 'heavy', 'emergency'], default: 'car' } },
            { name: 'closure_scope', in: 'query', schema: { type: 'string', enum: ['physical', 'directed'], default: 'physical' }, description: 'physical removes every link in the closure group; directed removes only the arc travelling in the direction under test' },
            { name: 'direction', in: 'query', schema: { type: 'string', enum: ['forward', 'reverse', 'both'], default: 'both' } },
            { name: 'geometry', in: 'query', schema: { type: 'string', enum: ['true', 'false'], default: 'true' } },
          ],
          responses: {
            '200': { description: 'detour result' },
            '400': { description: 'invalid parameter' },
            '404': { description: 'unknown link' },
          },
        },
      },
      '/api/v1/qa/summary': {
        get: {
          summary: 'Source-data and graph quality report for the active snapshot',
          responses: { '200': { description: 'qa report' } },
        },
      },
    },
    components: {
      schemas: {
        DetourStatus: {
          type: 'string',
          enum: [
            'OK',
            'DISCONNECTED',
            'UNRESOLVED_TIMEOUT',
            'INVALID_GRAPH',
            'SOURCE_DATA_ERROR',
            'UNSUPPORTED_PROFILE',
            'API_ERROR',
          ],
        },
      },
    },
  };
}
