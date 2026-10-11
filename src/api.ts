import { useEffect, useState } from 'react';
import type { FeatureCollection, Geometry } from 'geojson';
import type { CountyDetail, CountyOverview, Observation, Resource, SourceRecord } from './types';

function object(value: unknown, label: string): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new Error(`The service returned an invalid ${label}.`);
  }
  return value as Record<string, unknown>;
}

function string(value: unknown, label: string): string {
  if (typeof value !== 'string' || value.length === 0) throw new Error(`The service returned an invalid ${label}.`);
  return value;
}

function nullableNumber(value: unknown, label: string): number | null {
  if (value === null) return null;
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) {
    throw new Error(`The service returned an invalid ${label}.`);
  }
  return value;
}

function fips(value: unknown): string {
  const result = string(value, 'county identifier');
  if (!/^24\d{3}$/.test(result)) throw new Error('The service returned an invalid Maryland county identifier.');
  return result;
}

function observation(value: unknown): Observation {
  const record = object(value, 'observation');
  const status = record.status;
  if (status !== 'available' && status !== 'suppressed' && status !== 'missing') {
    throw new Error('The service returned an invalid disclosure status.');
  }
  const employment = nullableNumber(record.employment, 'employment value');
  const establishments = nullableNumber(record.establishments, 'establishment value');
  const annualPay = nullableNumber(record.annualPay, 'annual pay');
  if (status !== 'available' && [employment, establishments, annualPay].some((value) => value !== null)) {
    throw new Error('The service returned a number for an unavailable observation.');
  }
  const sourceId = record.sourceId === null ? null : string(record.sourceId, 'source identifier');
  const result: Observation = {
    industryCode: string(record.industryCode, 'industry code'),
    industryName: string(record.industryName, 'industry name'),
    employment, establishments, annualPay, status, sourceId,
  };
  if ('employmentShare' in record) {
    result.employmentShare = nullableNumber(record.employmentShare, 'employment share');
    if (result.employmentShare !== null && result.employmentShare > 1) throw new Error('The service returned an invalid employment share.');
    if (status !== 'available' && result.employmentShare !== null) throw new Error('An unavailable observation has a share.');
  }
  return result;
}

export function parseOverview(value: unknown): CountyOverview {
  const record = object(value, 'county overview');
  if (record.year !== 2024 || record.ownership !== 'private' || !Array.isArray(record.counties)) {
    throw new Error('The service returned an unexpected data snapshot.');
  }
  const counties = record.counties.map((value) => {
    const county = object(value, 'county');
    const total = observation(county.total);
    if (total.industryCode !== '10') throw new Error('The service returned an industry observation as a county total.');
    return { fips: fips(county.fips), name: string(county.name, 'county name'), total };
  });
  if (counties.length !== 24 || new Set(counties.map((county) => county.fips)).size !== 24 ||
      !counties.some((county) => county.fips === '24510') || !counties.some((county) => county.fips === '24005')) {
    throw new Error('The snapshot must contain all 24 Maryland jurisdictions, including Baltimore city and Baltimore County.');
  }
  return { revision: string(record.revision, 'snapshot revision'), year: 2024, ownership: 'private', counties };
}

export function parseDetail(value: unknown, expectedFips: string, expectedRevision: string): CountyDetail {
  const record = object(value, 'county detail');
  const county = object(record.county, 'county identity');
  if (fips(county.fips) !== expectedFips || string(record.revision, 'snapshot revision') !== expectedRevision) {
    throw new Error('The data snapshot changed while you were exploring. Reload the page to use one consistent snapshot.');
  }
  if (!Array.isArray(record.sectors)) throw new Error('The service returned an invalid industry list.');
  const sectors = record.sectors.map((value) => {
    const sector = observation(value);
    if (sector.industryCode === '10' || sector.employmentShare === undefined) throw new Error('The service returned an incomplete industry observation.');
    return sector;
  });
  if (new Set(sectors.map((sector) => sector.industryCode)).size !== sectors.length) throw new Error('The service returned duplicate industries.');
  return {
    revision: expectedRevision,
    county: { fips: expectedFips, name: string(county.name, 'county name') },
    total: observation(record.total), sectors,
  };
}

export function parseSource(value: unknown): SourceRecord {
  const record = object(value, 'source record');
  if (record.year !== 2024 || record.ownership !== 'private') throw new Error('The service returned an unexpected source snapshot.');
  const url = string(record.url, 'source URL');
  const parsedUrl = new URL(url);
  if (parsedUrl.protocol !== 'https:' || parsedUrl.hostname !== 'data.bls.gov') throw new Error('The service returned an invalid source URL.');
  const sha256 = string(record.sha256, 'source hash');
  if (!/^[a-f0-9]{64}$/i.test(sha256)) throw new Error('The service returned an invalid source hash.');
  const retrievedAt = string(record.retrievedAt, 'retrieval date');
  if (!Number.isFinite(Date.parse(retrievedAt))) throw new Error('The service returned an invalid retrieval date.');
  return {
    id: string(record.id, 'source identifier'), url, retrievedAt, sha256,
    raw: object(record.raw, 'original source fields'), definitions: object(record.definitions, 'definitions'),
    transformation: string(record.transformation, 'transformation'), year: 2024, ownership: 'private',
  };
}

export type CountyGeometry = FeatureCollection<Geometry, { GEOID: string; NAME: string }>;

export function parseGeometry(value: unknown): CountyGeometry {
  const record = object(value, 'county boundaries');
  if (record.type !== 'FeatureCollection' || !Array.isArray(record.features) || record.features.length !== 24) {
    throw new Error('The map must contain all 24 Maryland jurisdictions.');
  }
  const ids = new Set<string>();
  for (const item of record.features) {
    const feature = object(item, 'map feature');
    const properties = object(feature.properties, 'map properties');
    const id = fips(properties.GEOID);
    string(properties.NAME, 'map county name');
    if (ids.has(id)) throw new Error('The map contains a duplicate county.');
    ids.add(id);
    const geometry = object(feature.geometry, 'map geometry');
    if (feature.type !== 'Feature' || (geometry.type !== 'Polygon' && geometry.type !== 'MultiPolygon') || !Array.isArray(geometry.coordinates)) {
      throw new Error('The map contains an unsupported county geometry.');
    }
    const coordinatesAreValid = (coordinates: unknown): boolean => {
      if (!Array.isArray(coordinates) || coordinates.length === 0) return false;
      if (typeof coordinates[0] === 'number') {
        return coordinates.length >= 2 && coordinates.every((coordinate) => typeof coordinate === 'number' && Number.isFinite(coordinate)) &&
          Math.abs(coordinates[0]) <= 180 && Math.abs(coordinates[1]) <= 90;
      }
      return coordinates.every(coordinatesAreValid);
    };
    if (!coordinatesAreValid(geometry.coordinates)) throw new Error('The map contains invalid coordinates.');
  }
  return value as CountyGeometry;
}

export async function getJson<T>(url: string, parse: (value: unknown) => T, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal, headers: { Accept: 'application/json' } });
  if (!response.ok) {
    throw new Error(response.status === 429 ? 'The service is busy. Wait a moment, then try again.' : `Data could not be loaded (HTTP ${response.status}). Try again.`);
  }
  const contentType = (response.headers.get('Content-Type') ?? '').split(';')[0].trim();
  if (contentType !== 'application/json' && !/^application\/[a-z0-9.+-]+\+json$/i.test(contentType)) throw new Error('The service returned a page instead of data. Try again.');
  return parse(await response.json() as unknown);
}

export function useResource<T>(url: string, parse: (value: unknown) => T): { resource: Resource<T>; retry: () => void } {
  const [attempt, setAttempt] = useState(0);
  const [resource, setResource] = useState<Resource<T>>({ state: 'loading' });
  useEffect(() => {
    const controller = new AbortController();
    setResource({ state: 'loading' });
    getJson(url, parse, controller.signal).then((data) => setResource({ state: 'loaded', data })).catch((error: unknown) => {
      if (controller.signal.aborted) return;
      setResource({ state: 'error', message: error instanceof Error ? error.message : 'Data could not be loaded. Try again.' });
    });
    return () => controller.abort();
  }, [url, parse, attempt]);
  return { resource, retry: () => setAttempt((current) => current + 1) };
}
