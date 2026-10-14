/** Publication and replay contracts shared by the map, Worker and graph engine. */
export type AreaLevel = 'county' | 'tract' | 'blockGroup';
export type ValueStatus = 'available' | 'missing' | 'suppressed';

export interface Indicator {
  id: string;
  label: string;
  theme: 'people' | 'income' | 'housing' | 'access' | 'work';
  unit: 'count' | 'percent' | 'dollars';
  universe: string;
  definition: string;
  sourceId: string;
  period: string;
  demandEligible: boolean;
}

export interface AreaValue {
  value: number | null;
  moe: number | null;
  numerator: number | null;
  denominator: number | null;
  status: ValueStatus;
  sourceId: string;
}

export interface Area {
  geoid: string;
  level: AreaLevel;
  county: string;
  tract: string | null;
  name: string;
  lon: number;
  lat: number;
  values: Record<string, AreaValue>;
  raw: Record<string, Record<string, string>>;
}

export interface Facility {
  id: string;
  name: string;
  kind: 'library' | 'benefits';
  county: string;
  address: string;
  lon: number;
  lat: number;
  status: 'verified' | 'closed' | 'unresolved';
  baseline: boolean;
  candidate: boolean;
  sourceId: string;
  verificationUrl: string;
  checkedAt: string;
  note: string;
}

export interface DataSource {
  id: string;
  title: string;
  publisher: string;
  url: string;
  period: string;
  retrievedAt: string;
  sha256: string;
  transformation: string;
  license: string;
}

export interface ReconciliationItem {
  id: string;
  kind: 'corrected' | 'excluded' | 'unmatched' | 'coverage';
  label: string;
  detail: string;
  sourceId: string;
}

export interface Catalog {
  version: 1;
  revision: string;
  title: string;
  period: string;
  counties: { geoid: string; name: string; blockGroups: number; tracts: number }[];
  indicators: Indicator[];
  sources: DataSource[];
  reconciliation: ReconciliationItem[];
  counts: { blockGroups: number; tracts: number; facilities: number };
  assets: { blockGroups: string; tracts: string; graphPattern: string; commutePattern: string };
  limitations: string[];
}

export interface AreaResponse { revision: string; areas: Area[] }
export interface FacilityResponse { revision: string; facilities: Facility[] }
export interface SourceResponse { revision: string; source: DataSource; area?: Area }
export interface CountyHistory {
  revision: string;
  county: string;
  observations: { year: number; employment: number | null; annualPay: number | null; establishments: number | null; status: ValueStatus; sourceId: string }[];
}

export interface CommuteData {
  revision: string;
  year: number;
  county: string;
  totalJobs: number;
  flows: { home: string; work: string; jobs: number; from: [number, number]; to: [number, number] }[];
  omittedJobs: number;
  limitation: string;
}

/** Each edge index is its stable closure identifier. Length is in road miles. */
export interface RoadGraph {
  version: 1;
  revision: string;
  dataRevision: string;
  county: string;
  nodes: [number, number][];
  edges: [number, number, number][];
  shapes: [number, number][][];
  names: string[];
  areaNodes: Record<string, number | null>;
  facilityNodes: Record<string, number | null>;
  sourceId: string;
  limitations: string[];
}

export interface HypotheticalSite { id: string; name: string; lon: number; lat: number }
export interface Scenario {
  version: 1;
  dataRevision: string;
  graphRevision: string;
  county: string;
  measure: string;
  radiusMiles: number;
  additions: number;
  hubIds: string[];
  hypotheticalSites: HypotheticalSite[];
  closedEdgeIds: number[];
}

export interface AreaAccess {
  geoid: string;
  demand: number | null;
  baselineMiles: number | null;
  proposedMiles: number | null;
  hubId: string | null;
  state: 'covered' | 'beyond' | 'disconnected' | 'unmatched' | 'missing';
}
export interface Coverage {
  total: number;
  covered: number;
  beyond: number;
  disconnected: number;
  unmatched: number;
  missingAreas: number;
}
export interface SimulationResult {
  scenario: Scenario;
  baseline: Coverage;
  proposed: Coverage;
  suggested: { id: string; incremental: number }[];
  areas: AreaAccess[];
  flows: { edgeId: number; demand: number }[];
  elapsedMs: number;
}
export type SimulationRequest = { type: 'run'; requestId: number; graph: RoadGraph; areas: Area[]; facilities: Facility[]; scenario: Scenario; suggest: boolean };
export type SimulationReply = { type: 'result'; requestId: number; result: SimulationResult } | { type: 'error'; requestId: number; message: string };
