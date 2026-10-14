import type { Area, Catalog, Facility, CountyHistory } from '../src/outreach';

interface AreaRow {
  geoid: string; level: Area['level']; county: string; tract: string | null;
  name: string; lon: number; lat: number; values_json: string; raw_json: string;
}
const reply = (value: unknown, status = 200) => Response.json(value, {
  status, headers: { 'Cache-Control': status === 200 ? 'public, max-age=300, no-transform' : 'no-store', 'X-Content-Type-Options': 'nosniff' },
});
const fail = (code: string, message: string, status: number) => reply({ error: { code, message } }, status);
const area = (r: AreaRow, raw = false): Area => ({
  geoid: r.geoid, level: r.level, county: r.county, tract: r.tract,
  name: r.name, lon: r.lon, lat: r.lat,
  values: JSON.parse(r.values_json) as Area['values'], raw: raw ? JSON.parse(r.raw_json) as Area['raw'] : {},
});

export async function outreach(request: Request, db: D1Database | undefined): Promise<Response> {
  if (request.method !== 'GET') return fail('METHOD_NOT_ALLOWED', 'Public data endpoints accept GET.', 405);
  if (!db) return fail('OUTREACH_UNAVAILABLE', 'The outreach release has not been connected. Please try again.', 503);
  const url = new URL(request.url);
  const path = url.pathname;
  const county = url.searchParams.get('county');
  if (county !== null && !/^24\d{3}$/.test(county)) return fail('INVALID_COUNTY', 'Choose a Maryland county identifier.', 400);
  const allowed: Record<string, string[]> = {
    '/api/v2/catalog': [], '/api/v2/health': [], '/api/v2/areas': ['county', 'level'], '/api/v2/facilities': ['county'],
  };
  const keys = path.startsWith('/api/v2/sources/') ? ['geoid'] : path.startsWith('/api/v2/history/') ? [] : allowed[path];
  if (!keys) return fail('NOT_FOUND', 'This data endpoint does not exist.', 404);
  if ([...url.searchParams.keys()].some(k => !keys.includes(k)) || [...url.searchParams.keys()].some(k => url.searchParams.getAll(k).length !== 1)) {
    return fail('UNSUPPORTED_QUERY', 'Use only the documented data filters, once each.', 400);
  }
  try {
    const fragments = await db.prepare('SELECT catalog_json FROM outreach_release ORDER BY id').all<{ catalog_json: string }>();
    if (!fragments.results.length) return fail('RELEASE_UNAVAILABLE', 'The outreach release is unavailable.', 503);
    const catalog = JSON.parse(fragments.results.map(r => r.catalog_json).join('')) as Catalog;
    if (county && !catalog.counties.some(c => c.geoid === county)) return fail('COUNTY_NOT_FOUND', 'This Maryland jurisdiction was not found.', 404);
    if (path === '/api/v2/catalog') return reply(catalog);
    if (path === '/api/v2/health') {
      const count = await db.prepare("SELECT level, COUNT(*) AS count FROM outreach_areas GROUP BY level").all<{ level: string; count: number }>();
      const counts = Object.fromEntries(count.results.map(r => [r.level, r.count]));
      if (counts.blockGroup !== catalog.counts.blockGroups || counts.tract !== catalog.counts.tracts || counts.county !== catalog.counties.length) {
        return fail('RELEASE_INCOMPLETE', 'The published release does not match its coverage manifest.', 503);
      }
      return reply({ status: 'ok', revision: catalog.revision, counts });
    }
    if (path === '/api/v2/areas') {
      const level = url.searchParams.get('level') ?? 'blockGroup';
      if (!['county', 'tract', 'blockGroup'].includes(level)) return fail('INVALID_LEVEL', 'Choose county, tract, or blockGroup.', 400);
      const statement = county
        ? db.prepare('SELECT geoid,level,county,tract,name,lon,lat,values_json FROM outreach_areas WHERE level = ? AND county = ? ORDER BY geoid').bind(level, county)
        : db.prepare('SELECT geoid,level,county,tract,name,lon,lat,values_json FROM outreach_areas WHERE level = ? ORDER BY geoid').bind(level);
      const rows = await statement.all<AreaRow>();
      return reply({ revision: catalog.revision, areas: rows.results.map(r => area(r)) });
    }
    if (path === '/api/v2/facilities') {
      const rows = await (county
        ? db.prepare('SELECT payload_json FROM outreach_facilities WHERE county = ? ORDER BY id').bind(county)
        : db.prepare('SELECT payload_json FROM outreach_facilities ORDER BY id')).all<{ payload_json: string }>();
      return reply({ revision: catalog.revision, facilities: rows.results.map(r => JSON.parse(r.payload_json) as Facility) });
    }
    if (path.startsWith('/api/v2/history/')) {
      const id = path.slice('/api/v2/history/'.length);
      if (!/^24\d{3}$/.test(id)) return fail('INVALID_COUNTY', 'Choose a Maryland county identifier.', 400);
      const row = await db.prepare('SELECT payload_json FROM outreach_history WHERE county = ?').bind(id).first<{ payload_json: string }>();
      if (!row) return fail('HISTORY_NOT_FOUND', 'This county history was not found.', 404);
      return reply({ ...JSON.parse(row.payload_json) as CountyHistory, revision: catalog.revision });
    }
    const id = path.slice('/api/v2/sources/'.length);
    if (!/^[A-Za-z0-9:_-]{1,100}$/.test(id)) return fail('INVALID_SOURCE', 'This source identifier is invalid.', 400);
    const source = catalog.sources.find(s => s.id === id);
    if (!source) return fail('SOURCE_NOT_FOUND', 'This public source was not found.', 404);
    const geoid = url.searchParams.get('geoid');
    if (!geoid) return reply({ revision: catalog.revision, source });
    if (!/^24\d{3}(?:\d{6}\d?)?$/.test(geoid)) return fail('INVALID_AREA', 'Use a Maryland county, tract, or block-group identifier.', 400);
    const record = await db.prepare('SELECT * FROM outreach_areas WHERE geoid = ?').bind(geoid).first<AreaRow>();
    if (!record) return fail('AREA_NOT_FOUND', 'This area was not found.', 404);
    return reply({ revision: catalog.revision, source, area: area(record, true) });
  } catch {
    console.error('outreach_query_unavailable');
    return fail('DATA_UNAVAILABLE', 'The outreach data service is unavailable. Please try again.', 503);
  }
}
