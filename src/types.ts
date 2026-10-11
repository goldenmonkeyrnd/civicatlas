export type DisclosureStatus = 'available' | 'suppressed' | 'missing';

export interface Observation {
  industryCode: string;
  industryName: string;
  employment: number | null;
  establishments: number | null;
  annualPay: number | null;
  status: DisclosureStatus;
  sourceId: string | null;
  employmentShare?: number | null;
}

export interface CountySummary {
  fips: string;
  name: string;
  total: Observation;
}

export interface CountyOverview {
  revision: string;
  year: 2024;
  ownership: 'private';
  counties: CountySummary[];
}

export interface CountyDetail {
  revision: string;
  county: { fips: string; name: string };
  total: Observation;
  sectors: Observation[];
}

export interface SourceRecord {
  id: string;
  url: string;
  retrievedAt: string;
  sha256: string;
  raw: Record<string, unknown>;
  definitions: Record<string, unknown>;
  transformation: string;
  year: 2024;
  ownership: 'private';
}

export type Resource<T> =
  | { state: 'loading' }
  | { state: 'loaded'; data: T }
  | { state: 'error'; message: string };
