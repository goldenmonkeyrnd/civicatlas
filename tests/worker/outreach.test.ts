import { afterEach, beforeEach, expect, test } from 'bun:test';
import { Database } from 'bun:sqlite';
import worker, { type Env } from '../../worker/index';
import type { AreaResponse, Catalog, SourceResponse } from '../../src/outreach';

let db: Database;
let env: Env;
beforeEach(async () => {
  db = new Database(':memory:');
  db.exec(await Bun.file('data/outreach/seed.sql').text());
  const adapter = { prepare(sql: string) {
    let args: (string | number | null)[] = [];
    const statement = {
      bind(...params: (string | number | null)[]) { args = params; return statement; },
      async first() { return db.query(sql).get(...args); },
      async all() { return { success: true, results: db.query(sql).all(...args) }; },
    }; return statement;
  }} as unknown as D1Database;
  env = { DB: adapter, OUTREACH_DB: adapter, ASSETS: { fetch: async () => new Response('asset') } as unknown as Fetcher };
});
afterEach(() => db.close());
const get = (path: string) => worker.fetch(new Request(`https://atlas.goldenmonkey.dev${path}`), env);

test('actual outreach publication covers all Maryland geographies', async () => {
  const catalog = await (await get('/api/v2/catalog')).json() as Catalog;
  expect(catalog.counts.blockGroups).toBe(4079);
  expect(catalog.counts.tracts).toBe(1475);
  expect(catalog.counties).toHaveLength(24);
  expect((await get('/api/v2/health')).status).toBe(200);
  const areas = await (await get('/api/v2/areas?county=24510&level=blockGroup')).json() as AreaResponse;
  expect(areas.revision).toBe(catalog.revision);
  expect(areas.areas.length).toBe(catalog.counties.find(c => c.geoid === '24510')!.blockGroups);
  expect(areas.areas.every(a => a.geoid.startsWith('24510') && a.level === 'blockGroup')).toBe(true);
});

test('source inspection traces selected neighborhood estimates to pinned raw fields', async () => {
  const areas = await (await get('/api/v2/areas?county=24510&level=blockGroup')).json() as AreaResponse;
  const selected = areas.areas.find(a => a.values.households.status === 'available')!;
  const value = selected.values.households;
  const result = await (await get(`/api/v2/sources/${value.sourceId}?geoid=${selected.geoid}`)).json() as SourceResponse;
  expect(result.source.sha256).toMatch(/^[a-f0-9]{64}$/);
  expect(result.area!.geoid).toBe(selected.geoid);
  expect(Object.keys(result.area!.raw).length).toBeGreaterThan(0);
  expect(result.area!.values.households).toEqual(value);
  expect(value.moe).not.toBeNull();
});

test('invalid, duplicate and arbitrary public queries fail without changing data', async () => {
  expect((await get('/api/v2/areas?county=24510&level=block')).status).toBe(400);
  expect((await get('/api/v2/areas?county=24510&county=24005')).status).toBe(400);
  expect((await get('/api/v2/areas?sql=DROP%20TABLE%20outreach_areas')).status).toBe(400);
  expect((await get('/api/v2/areas?county=24002')).status).toBe(404);
  expect((await get('/api/v2/sources/not-found')).status).toBe(404);
  expect((await get('/api/v2/catalog?geoid=24510')).status).toBe(400);
  expect((db.query('SELECT COUNT(*) AS count FROM outreach_areas').get() as {count:number}).count).toBe(5578);
});

test('incomplete release and disconnected database produce explicit errors', async () => {
  db.query("DELETE FROM outreach_areas WHERE level='tract'").run();
  expect((await get('/api/v2/health')).status).toBe(503);
  env.OUTREACH_DB = undefined;
  expect((await get('/api/v2/catalog')).status).toBe(503);
});

test('immutable map asset paths serve bounded compressed JSON and reject unknown assets', async () => {
  const fixture = '{"revision":"roads-test"}';
  const compressed = Bun.gzipSync(fixture);
  env.ASSETS = { fetch: async (r: Request) => {
    expect(new URL(r.url).pathname).toEndWith('.json.gz');
    return new Response(new Uint8Array(compressed), { headers: { 'Content-Type': 'application/gzip', 'Content-Length': String(compressed.length) } });
  }} as unknown as Fetcher;
  const response = await get('/data/outreach/md-outreach-2024-test/graphs/24510.json');
  expect(response.headers.get('Content-Encoding')).toBe('gzip');
  expect(response.headers.get('Content-Type')).toBe('application/json');
  expect(response.headers.get('Cache-Control')).toContain('immutable');
  expect(response.headers.get('Content-Length')).toBeNull();
  expect(new TextDecoder().decode(Bun.gunzipSync(new Uint8Array(await response.arrayBuffer())))).toBe(fixture);
  expect((await get('/data/outreach/anything/seed.sql')).status).toBe(404);
});
