import type { Area, AreaResponse, Catalog, CommuteData, CountyHistory, DataSource, Facility, FacilityResponse, RoadGraph, SourceResponse } from './outreach';
import { getJson } from './api';

const record = (value: unknown, name: string): Record<string, unknown> => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`Invalid ${name} in the published release.`);
  return value as Record<string, unknown>;
};
const text = (value: unknown, name: string): string => {
  if (typeof value !== 'string' || !value) throw new Error(`Missing ${name} in the published release.`);
  return value;
};
const number = (value: unknown, name: string): number => {
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new Error(`Invalid ${name} in the published release.`);
  return value;
};
const list = (value: unknown, name: string): unknown[] => {
  if (!Array.isArray(value)) throw new Error(`Invalid ${name} in the published release.`);
  return value;
};
const revision = (value: unknown, expected?: string): string => {
  const result = text(value, 'release revision');
  if (expected && expected !== result) throw new Error('The release changed while loading. Reload CivicAtlas to use one consistent data release.');
  return result;
};
const sameOrigin = (value: unknown): string => {
  const result = text(value, 'asset path');
  if (!result.startsWith('/') || result.startsWith('//') || result.includes('..')) throw new Error('The release contains an invalid asset path.');
  return result;
};
function source(value: unknown): DataSource {
  const s = record(value, 'source');
  for (const key of ['id', 'title', 'publisher', 'url', 'period', 'retrievedAt', 'transformation', 'license']) text(s[key], `source ${key}`);
  if (typeof s.sha256 !== 'string' || !/^[a-f0-9]{64}$/i.test(s.sha256)) throw new Error('The published source has no valid integrity hash.');
  if (!/^https?:\/\//.test(s.url as string)) throw new Error('The published source has an invalid URL.');
  return value as DataSource;
}
export function parseCatalog(value: unknown): Catalog {
  const c = record(value, 'catalog');
  if (c.version !== 1) throw new Error('This data release needs a newer CivicAtlas viewer.');
  revision(c.revision); text(c.title, 'title'); text(c.period, 'period');
  const counties = list(c.counties, 'counties').map((v) => { const row = record(v, 'county'); text(row.geoid, 'county GEOID'); text(row.name, 'county name'); number(row.blockGroups, 'block group count'); number(row.tracts, 'tract count'); return row; });
  if (counties.length !== 24 || new Set(counties.map((r) => r.geoid)).size !== 24 || !counties.some((r) => r.geoid === '24510')) throw new Error('The release must include all 24 Maryland jurisdictions.');
  list(c.indicators, 'indicators').forEach((v) => { const i = record(v, 'indicator'); for (const k of ['id', 'label', 'unit', 'universe', 'definition', 'sourceId', 'period']) text(i[k], k); if (typeof i.demandEligible !== 'boolean') throw new Error('An indicator is missing its planning eligibility.'); });
  list(c.sources, 'sources').forEach(source); list(c.reconciliation, 'reconciliation'); list(c.limitations, 'limitations');
  const counts = record(c.counts, 'release counts'); for (const k of ['blockGroups', 'tracts', 'facilities']) number(counts[k], k);
  const assets = record(c.assets, 'assets'); for (const k of ['blockGroups', 'tracts', 'graphPattern', 'commutePattern']) sameOrigin(assets[k]);
  return value as Catalog;
}
function area(value: unknown): Area {
  const a = record(value, 'area'); text(a.geoid, 'area GEOID'); text(a.county, 'county GEOID'); text(a.name, 'area name');
  if (!['county', 'tract', 'blockGroup'].includes(a.level as string)) throw new Error('Unsupported geographic level.');
  number(a.lon, 'longitude'); number(a.lat, 'latitude');
  const values = record(a.values, 'area values');
  for (const entry of Object.values(values)) {
    const v = record(entry, 'observation');
    if (!['available', 'missing', 'suppressed'].includes(v.status as string)) throw new Error('An observation is missing its disclosure status.');
    for (const k of ['value', 'moe', 'numerator', 'denominator']) if (v[k] !== null) number(v[k], k);
    if (v.status !== 'available' && v.value !== null) throw new Error('An unavailable observation has a numeric value.');
    text(v.sourceId, 'observation source');
  }
  record(a.raw, 'original source fields');
  return value as Area;
}
export function parseAreas(value: unknown, expected: string): AreaResponse {
  const r = record(value, 'area response'); revision(r.revision, expected);
  const areas = list(r.areas, 'areas').map(area);
  if (new Set(areas.map((a) => a.geoid)).size !== areas.length) throw new Error('The area response contains duplicate geographic identifiers.');
  return { revision: expected, areas };
}
export function parseFacilities(value: unknown, expected: string): FacilityResponse {
  const r = record(value, 'facility response'); revision(r.revision, expected);
  const facilities = list(r.facilities, 'facilities').map((value) => {
    const f = record(value, 'facility');
    for (const k of ['id', 'name', 'county', 'address', 'sourceId', 'verificationUrl', 'checkedAt']) text(f[k], k);
    number(f.lon, 'facility longitude'); number(f.lat, 'facility latitude');
    if (!['verified', 'closed', 'unresolved'].includes(f.status as string) || typeof f.baseline !== 'boolean' || typeof f.candidate !== 'boolean') throw new Error('A location has an invalid verification state.');
    if (f.status !== 'verified' && (f.candidate || f.baseline)) throw new Error('An unverified facility was included in planning.');
    return value as Facility;
  });
  return { revision: expected, facilities };
}
export function parseGraph(value: unknown, expected: string, county: string): RoadGraph {
  const r = record(value, 'street graph');
  if (r.version !== 1 || r.county !== county || r.dataRevision !== expected) throw new Error('The street network does not match this study area and release.');
  revision(r.revision); list(r.nodes, 'street nodes'); list(r.shapes, 'street shapes'); list(r.names, 'street names'); list(r.edges, 'street edges'); record(r.areaNodes, 'area connections'); record(r.facilityNodes, 'facility connections'); list(r.limitations, 'street limitations'); text(r.sourceId, 'street source');
  return value as RoadGraph;
}
export function parseSourceResponse(value: unknown, expected: string, id: string): SourceResponse {
  const r = record(value, 'source response'); revision(r.revision, expected);
  const s = source(r.source); if (s.id !== id) throw new Error('The response returned a different source.');
  return { revision: expected, source: s, ...(r.area ? { area: area(r.area) } : {}) };
}
export function parseHistory(value: unknown, expected: string, county: string): CountyHistory {
  const r = record(value, 'employment history'); revision(r.revision, expected);
  if (r.county !== county) throw new Error('Employment history belongs to another county.');
  list(r.observations, 'employment observations').forEach((v) => { const row = record(v, 'history observation'); number(row.year, 'employment year'); for (const key of ['employment', 'annualPay', 'establishments']) if (row[key] !== null) number(row[key], key); });
  return value as CountyHistory;
}
export function parseCommutes(value: unknown, expected: string, county: string): CommuteData {
  const r = record(value, 'home–work connections'); revision(r.revision, expected);
  if (r.county !== county) throw new Error('The home–work connections belong to another county.');
  number(r.year, 'commuting year'); number(r.totalJobs, 'job relationship count'); number(r.omittedJobs, 'omitted relationships'); list(r.flows, 'job connections'); text(r.limitation, 'commuting coverage');
  return value as CommuteData;
}
export const loadCatalog = (signal?: AbortSignal) => getJson('/api/v2/catalog', parseCatalog, signal);
export const assetForCounty = (pattern: string, county: string) => pattern.replace('{county}', county);
