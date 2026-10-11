import { beforeEach, describe, expect, test } from 'bun:test';
import { Database } from 'bun:sqlite';
import worker, { type Env } from '../../worker/index';
import type { CountyOverview, CountyDetail, SourceRecord } from '../../src/types';
interface Health { status: string; countyCount: number; revision: string }
interface ApiFailure {error:{code:string;message:string}}

let db: Database;
let points: AnalyticsEngineDataPoint[];
let env: Env;
beforeEach(async () => {
  db = new Database(':memory:');
  db.exec(await Bun.file('data/processed/seed.sql').text());
  points = [];
  const adapter = { prepare(sql: string) {
    let args: (string | number | null)[] = [];
    const statement = {
      bind(...params: (string | number | null)[]) { args = params; return statement; },
      async first() { return db.query(sql).get(...args); },
      async all() { return { success: true, results: db.query(sql).all(...args) }; },
    };
    return statement;
  }} as unknown as D1Database;
  env = { DB: adapter, ASSETS: { fetch: async () => new Response('asset') } as unknown as Fetcher, ENGAGEMENT: { writeDataPoint: (value: AnalyticsEngineDataPoint) => points.push(value) } as unknown as AnalyticsEngineDataset };
});
const get = (path: string) => worker.fetch(new Request(`https://atlas.goldenmonkey.dev${path}`), env);
const post = (value: unknown, extra: HeadersInit = {}) => worker.fetch(new Request('https://atlas.goldenmonkey.dev/api/events', { method: 'POST', headers: { Origin: 'https://goldenmonkey.dev', 'Content-Type': 'application/json', ...extra }, body: JSON.stringify(value) }), env);

describe('the public snapshot API executes real SQLite queries', () => {
  test('health and overview contain all24 jurisdictions and actual revision', async () => {
    const health = await (await get('/api/health')).json() as Health;
    expect(health.status).toBe('ok'); expect(health.countyCount).toBe(24);
    const response = await get('/api/v1/counties');
    expect(response.headers.get('Cache-Control')).toContain('max-age=3600');
    const overview = await response.json() as CountyOverview;
    expect(overview.revision).toBe(health.revision); expect(overview.counties).toHaveLength(24);
    expect(overview.counties.find((c: {fips:string})=>c.fips==='24510')?.name).toBe('Baltimore city');
    expect(overview.counties.find((c: {fips:string})=>c.fips==='24005')?.name).toBe('Baltimore County');
  });
  test('detail preserves disclosure and computes shares against matching total', async () => {
    const detail = await (await get('/api/v1/counties/24510')).json() as CountyDetail;
    expect(detail.total.employment).toBe(270536);
    for (const sector of detail.sectors) {
      if (sector.status === 'available') expect(sector.employmentShare).toBe(sector.employment!/detail.total.employment!);
      else { expect(sector.employment).toBeNull(); expect(sector.employmentShare).toBeNull(); }
    }
    const suppressed = db.query("SELECT fips,industry_code FROM observations WHERE status='suppressed' LIMIT 1").get() as {fips:string;industry_code:string};
    const s = await (await get(`/api/v1/counties/${suppressed.fips}`)).json() as CountyDetail;
    const row = s.sectors.find((x:{industryCode:string})=>x.industryCode===suppressed.industry_code);
    expect(row!.status).toBe('suppressed'); expect(row!.employment).toBeNull(); expect(row!.annualPay).toBeNull();
  });
  test('a legitimate observed zero stays zero', async () => {
    db.query("UPDATE observations SET employment=0,status='available' WHERE fips='24510' AND industry_code='11'").run();
    const detail = await (await get('/api/v1/counties/24510')).json() as CountyDetail;
    const zero = detail.sectors.find((x:{industryCode:string})=>x.industryCode==='11');
    expect(zero!.employment).toBe(0); expect(zero!.employmentShare).toBe(0);
  });
  test('source points to the original public row with a source hash', async () => {
    const response = await get('/api/v1/sources/qcew-2024-24510-10');
    const source = await response.json() as SourceRecord;
    expect(response.status).toBe(200); expect(source.sha256).toMatch(/^[a-f0-9]{64}$/);
    expect(source.raw.area_fips).toBe('24510'); expect(source.raw.own_code).toBe('5');
    expect(source.definitions.employment).toContain('Workplace jobs');
  });
  test('invalid identifiers, arbitrary queries and unknown API routes fail explicitly', async () => {
    expect((await get('/api/v1/counties/2451')).status).toBe(400);
    expect((await get('/api/v1/counties/24002')).status).toBe(404);
    expect((await get('/api/v1/counties?sql=DROP%20TABLE%20counties')).status).toBe(400);
    const unknown = await get('/api/v1/not-real'); expect(unknown.status).toBe(404); expect(unknown.headers.get('Content-Type')).toContain('application/json');
    expect(db.query('SELECT COUNT(*) AS count FROM counties').get()).toEqual({count:24});
  });
  test('database failure produces a retryable service error, never mock data', async () => {
    env.DB = { prepare() { throw new Error('injected outage'); } } as unknown as D1Database;
    const response = await get('/api/v1/counties'); expect(response.status).toBe(503);
    expect((await response.json() as ApiFailure).error.code).toBe('DATA_UNAVAILABLE');
  });
});

describe('bounded anonymous telemetry', () => {
  test('accepts only documented labels and CORS origin', async () => {
    const r = await post({event:'email_clicked',surface:'portfolio',campaign:'nava-2026'});
    expect(r.status).toBe(204); expect(r.headers.get('Access-Control-Allow-Origin')).toBe('https://goldenmonkey.dev');
    expect(points).toEqual([{indexes:['nava-2026'],blobs:['email_clicked','portfolio','nava-2026'],doubles:[1]}]);
  });
  test('rejects identifying fields, unapproved events, oversized bodies and origins', async () => {
    expect((await post({event:'email_clicked',surface:'portfolio',campaign:'nava-2026',email:'private@example.com'})).status).toBe(400);
    expect((await post({event:'visitor_identified',surface:'portfolio',campaign:'nava-2026'})).status).toBe(400);
    expect((await post({event:'x'.repeat(1200),surface:'portfolio',campaign:'nava-2026'})).status).toBe(413);
    expect((await post({event:'email_clicked',surface:'portfolio',campaign:'nava-2026'},{Origin:'https://unapproved.example'})).status).toBe(403);
    expect(points).toHaveLength(0);
  });
  test('respects GPC and DNT and tolerates an unavailable analytics binding', async () => {
    const body={event:'email_clicked',surface:'portfolio',campaign:'nava-2026'};
    expect((await post(body,{'Sec-GPC':'1'})).status).toBe(204);
    expect((await post(body,{'DNT':'1'})).status).toBe(204); expect(points).toHaveLength(0);
    env.ENGAGEMENT={writeDataPoint(){throw new Error('outage');}} as unknown as AnalyticsEngineDataset;
    expect((await post(body)).status).toBe(204);
  });
});
