import { outreach } from './outreach';

export interface Env {
  DB: D1Database;
  OUTREACH_DB?: D1Database;
  ASSETS: Fetcher;
  ENGAGEMENT?: AnalyticsEngineDataset;
}

type ObservationStatus = 'available' | 'suppressed' | 'missing';
interface ObservationRow {
  fips: string;
  industry_code: string;
  industry_name: string;
  employment: number | null;
  establishments: number | null;
  annual_pay: number | null;
  status: ObservationStatus;
  source_id: string | null;
}

const events = new Set(['conference_viewed', 'comparison_used', 'source_opened', 'github_clicked', 'resume_downloaded', 'walkthrough_started', 'email_clicked']);
const origins = new Set(['https://goldenmonkey.dev', 'https://atlas.goldenmonkey.dev', 'http://localhost:1313', 'http://127.0.0.1:1313', 'http://localhost:5173', 'http://127.0.0.1:5173']);
const definitions = {
  employment: 'Annual average employment covered by unemployment insurance. Workplace jobs, including full- and part-time jobs; not a count of residents or unique workers.',
  establishments: 'Annual average number of private-sector establishments.',
  annualPay: 'Average annual pay per covered job, in nominal 2024 dollars. Not household income or a starting salary.',
  employmentShare: 'Sector annual average employment divided by the same county private-sector annual average employment. Available only when both observations are disclosed and the denominator is positive.',
  suppression: 'BLS disclosure code N means withheld. Encoded zeros are represented as null; absent sector observations are separately marked missing.',
};

function json(value: unknown, status = 200, cache = false): Response {
  return Response.json(value, { status, headers: {
    'Cache-Control': cache ? 'public, max-age=3600, stale-while-revalidate=86400' : 'no-store',
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'strict-origin-when-cross-origin',
  }});
}

function error(code: string, message: string, status: number): Response {
  return json({ error: { code, message } }, status);
}

function metric(row: ObservationRow) {
  // Status controls disclosure independently of any numeric zero in the source.
  const disclosed = row.status === 'available';
  return {
    industryCode: row.industry_code,
    industryName: row.industry_name,
    employment: disclosed ? row.employment : null,
    establishments: disclosed ? row.establishments : null,
    annualPay: disclosed ? row.annual_pay : null,
    status: row.status,
    sourceId: row.source_id,
  };
}

async function revision(db: D1Database): Promise<string> {
  const result = await db.prepare("SELECT value FROM metadata WHERE key = 'dataset_revision'").first<{ value: string }>();
  if (!result?.value) throw new Error('Dataset revision is unavailable');
  return result.value;
}

async function telemetry(request: Request, env: Env): Promise<Response> {
  const origin = request.headers.get('Origin');
  const cors = new Headers({ 'Cache-Control': 'no-store', 'Vary': 'Origin' });
  if (origin && origins.has(origin)) {
    cors.set('Access-Control-Allow-Origin', origin);
    cors.set('Access-Control-Allow-Methods', 'POST, OPTIONS');
    cors.set('Access-Control-Allow-Headers', 'Content-Type');
  }
  if (!origin || !origins.has(origin)) return error('ORIGIN_NOT_ALLOWED', 'This event origin is not allowed.', 403);
  if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors });
  if (request.method !== 'POST') return error('METHOD_NOT_ALLOWED', 'Use POST for application events.', 405);
  const reply = (value: unknown, status: number) => {
    const response = json(value, status);
    cors.forEach((v, k) => response.headers.set(k, v));
    return response;
  };
  if (request.headers.get('Sec-GPC') === '1' || request.headers.get('DNT') === '1') return new Response(null, { status: 204, headers: cors });
  if (!request.headers.get('Content-Type')?.startsWith('application/json')) return reply({ error: { code: 'INVALID_CONTENT_TYPE', message: 'Events must use application/json.' } }, 415);
  if (Number(request.headers.get('Content-Length') ?? '0') > 1024) return reply({ error: { code: 'EVENT_TOO_LARGE', message: 'Events must be smaller than 1 KB.' } }, 413);
  // Read with an actual bound, including requests with an absent or inaccurate length.
  const reader = request.body?.getReader();
  if (!reader) return reply({ error: { code: 'INVALID_EVENT', message: 'An event body is required.' } }, 400);
  let size = 0;
  const chunks: Uint8Array[] = [];
  while (true) {
    const chunk = await reader.read();
    if (chunk.done) break;
    size += chunk.value.byteLength;
    if (size > 1024) {
      await reader.cancel();
      return reply({ error: { code: 'EVENT_TOO_LARGE', message: 'Events must be smaller than 1 KB.' } }, 413);
    }
    chunks.push(chunk.value);
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  let payload: unknown;
  try { payload = JSON.parse(new TextDecoder().decode(bytes)); }
  catch { return reply({ error: { code: 'INVALID_JSON', message: 'The event body must be valid JSON.' } }, 400); }
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return reply({ error: { code: 'INVALID_EVENT', message: 'Use an event, surface and campaign.' } }, 400);
  const body = payload as Record<string, unknown>;
  if (Object.keys(body).sort().join(',') !== 'campaign,event,surface' || typeof body.event !== 'string' || !events.has(body.event) || !['portfolio', 'atlas'].includes(String(body.surface)) || body.campaign !== 'nava-2026') {
    return reply({ error: { code: 'INVALID_EVENT', message: 'Only documented aggregate events are accepted.' } }, 400);
  }
  try {
    env.ENGAGEMENT?.writeDataPoint({ indexes: ['nava-2026'], blobs: [body.event, String(body.surface), 'nava-2026'], doubles: [1] });
  } catch {
    // Telemetry is best-effort; contact and data exploration remain usable.
    console.warn('engagement_write_unavailable');
  }
  return new Response(null, { status: 204, headers: cors });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname;
    if (path.startsWith('/data/outreach/')) {
      if (request.method !== 'GET' && request.method !== 'HEAD') return error('METHOD_NOT_ALLOWED', 'Map assets accept GET or HEAD.', 405);
      if (!/^\/data\/outreach\/[A-Za-z0-9_-]{1,100}\/(?:catalog\.json|facilities\.json|roads-manifest\.json|block-groups\.geojson|tracts\.geojson|(?:graphs|commutes)\/24\d{3}\.json)$/.test(path)) {
        return error('ASSET_NOT_FOUND', 'This published map asset was not found.', 404);
      }
      const target = new URL(request.url);
      target.pathname += '.gz'; target.search = '';
      const response = await env.ASSETS.fetch(new Request(target, request));
      if (!response.ok || response.headers.get('Content-Type')?.includes('text/html')) return error('ASSET_NOT_FOUND', 'This release asset is unavailable.', 404);
      const headers = new Headers(response.headers);
      headers.set('Content-Type', 'application/json'); headers.set('Content-Encoding', 'gzip');
      headers.set('Cache-Control', 'public, max-age=31536000, immutable, no-transform');
      headers.set('X-Content-Type-Options', 'nosniff');
      // Fetch decodes assets that carry Content-Encoding. Re-encode those;
      // extension-only gzip assets already contain their encoded bytes.
      const encodeBody = response.headers.has('Content-Encoding') ? 'automatic' : 'manual';
      headers.delete('Content-Length');
      return new Response(request.method === 'HEAD' ? null : response.body, { status: response.status, headers, encodeBody });
    }
    if (path === '/api/events') return telemetry(request, env);
    if (path.startsWith('/api/v2/')) return outreach(request, env.OUTREACH_DB);
    if (!path.startsWith('/api/')) return env.ASSETS.fetch(request);
    if (request.method !== 'GET') return error('METHOD_NOT_ALLOWED', 'Public data endpoints accept GET requests.', 405);
    if (url.search) return error('UNSUPPORTED_QUERY', 'This snapshot API does not accept query parameters.', 400);
    if (path !== '/api/health' && path !== '/api/v1/counties' && !path.startsWith('/api/v1/counties/') && !path.startsWith('/api/v1/sources/')) return error('NOT_FOUND', 'This API endpoint does not exist.', 404);
    try {
      if (path === '/api/health') {
        const [dataRevision, count] = await Promise.all([revision(env.DB), env.DB.prepare('SELECT COUNT(*) AS count FROM counties').first<{ count: number }>()]);
        if (count?.count !== 24) return error('DATASET_INCOMPLETE', 'The Maryland snapshot is not ready.', 503);
        return json({ status: 'ok', revision: dataRevision, year: 2024, ownership: 'private', countyCount: count.count });
      }
      const dataRevision = await revision(env.DB);
      if (path === '/api/v1/counties') {
        const result = await env.DB.prepare("SELECT c.fips, c.name, o.industry_code, o.industry_name, o.employment, o.establishments, o.annual_pay, o.status, o.source_id FROM counties c JOIN observations o ON o.fips = c.fips AND o.industry_code = '10' ORDER BY c.name").all<ObservationRow & { name: string }>();
        if (result.results.length !== 24) return error('DATASET_INCOMPLETE', 'The Maryland snapshot is incomplete.', 503);
        return json({ revision: dataRevision, year: 2024, ownership: 'private', counties: result.results.map(row => ({ fips: row.fips, name: row.name, total: metric(row) })) }, 200, true);
      }
      if (path.startsWith('/api/v1/counties/')) {
        const fips = path.slice('/api/v1/counties/'.length);
        if (!/^24\d{3}$/.test(fips)) return error('INVALID_FIPS', 'Use a five-character Maryland county FIPS identifier.', 400);
        const county = await env.DB.prepare('SELECT fips, name FROM counties WHERE fips = ?').bind(fips).first<{ fips: string; name: string }>();
        if (!county) return error('COUNTY_NOT_FOUND', 'This Maryland jurisdiction was not found.', 404);
        const result = await env.DB.prepare('SELECT * FROM observations WHERE fips = ? ORDER BY industry_code').bind(fips).all<ObservationRow>();
        const total = result.results.find(row => row.industry_code === '10');
        if (!total) return error('DATASET_INCOMPLETE', 'This county total is unavailable.', 503);
        const sectors = result.results.filter(row => row.industry_code !== '10').map(row => ({ ...metric(row), employmentShare: row.status === 'available' && total.status === 'available' && row.employment !== null && total.employment !== null && total.employment > 0 ? row.employment / total.employment : null }));
        return json({ revision: dataRevision, year: 2024, ownership: 'private', county, total: metric(total), sectors }, 200, true);
      }
      const id = path.slice('/api/v1/sources/'.length);
      if (!/^[a-zA-Z0-9:_-]{1,100}$/.test(id)) return error('INVALID_SOURCE', 'This source identifier is invalid.', 400);
      const source = await env.DB.prepare('SELECT * FROM sources WHERE id = ?').bind(id).first<{ id: string; url: string; retrieved_at: string; sha256: string; raw_json: string }>();
      if (!source) return error('SOURCE_NOT_FOUND', 'This source observation was not found.', 404);
      return json({ id: source.id, url: source.url, retrievedAt: source.retrieved_at, sha256: source.sha256, raw: JSON.parse(source.raw_json) as unknown, definitions, transformation: 'Pinned QCEW 2024 annual CSV. Filter private ownership 5; total aggregation level 71 and NAICS sector level 74. Preserve FIPS strings. Convert disclosed numeric fields; withhold observations with disclosure code N. Absent observations remain missing. Sector shares use the matching private-sector county total.', year: 2024, ownership: 'private' }, 200, true);
    } catch {
      console.error('data_query_unavailable');
      return error('DATA_UNAVAILABLE', 'The data service is unavailable. Please try again.', 503);
    }
  },
} satisfies ExportedHandler<Env>;
