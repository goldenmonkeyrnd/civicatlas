import type {
  Area, AreaAccess, Coverage, Facility, HypotheticalSite, RoadGraph,
  Scenario, SimulationResult,
} from './outreach';

/** Road access is measured from an area's snapped point toward a service site. */
export const HYPOTHETICAL_SNAP_METRES = 500;
const DISTANCE_EPSILON = 1e-10;
const MAX_SELECTED_SITES = 10;
export const HOUSEHOLD_DEMAND_MEASURES = ['households', 'noVehicle', 'noInternet'] as const;
const SCENARIO_FIELDS = ['version', 'dataRevision', 'graphRevision', 'county', 'measure', 'radiusMiles', 'additions', 'hubIds', 'hypotheticalSites', 'closedEdgeIds'];

export class ScenarioError extends Error {
  constructor(message: string) { super(message); this.name = 'ScenarioError'; }
}
export interface ScenarioValidationContext {
  graph?: RoadGraph;
  dataRevision?: string;
  facilities?: Facility[];
  /** The catalog's demandEligible indicator identifiers. */
  measures?: readonly string[];
}
export interface SimulationInput {
  graph: RoadGraph;
  areas: Area[];
  facilities: Facility[];
  scenario: Scenario;
  suggest: boolean;
}

function record(value: unknown, name: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new ScenarioError(`${name} must be an object.`);
  return value as Record<string, unknown>;
}
function textField(value: unknown, name: string): string {
  if (typeof value !== 'string' || value.length < 1 || value.length > 160 || /[\u0000-\u001f]/.test(value)) {
    throw new ScenarioError(`${name} must be non-empty text of at most 160 characters.`);
  }
  return value;
}
function boundedNumber(value: unknown, name: string, minimum: number, maximum: number, integer = false): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < minimum || value > maximum || (integer && !Number.isInteger(value))) {
    throw new ScenarioError(`${name} must be ${integer ? 'an integer' : 'a finite number'} between ${minimum} and ${maximum}.`);
  }
  return value;
}
function unique<T>(values: T[], name: string): T[] {
  if (new Set(values).size !== values.length) throw new ScenarioError(`${name} contains duplicate identifiers.`);
  return values;
}
function stringList(value: unknown, name: string): string[] {
  if (!Array.isArray(value) || value.length > MAX_SELECTED_SITES) throw new ScenarioError(`${name} must contain at most ${MAX_SELECTED_SITES} identifiers.`);
  return unique(value.map((entry) => textField(entry, name)), name).sort();
}
function siteFromUnknown(input: unknown): HypotheticalSite {
  const site = record(input, 'Hypothetical site');
  const extra = Object.keys(site).find((key) => !['id', 'name', 'lon', 'lat'].includes(key));
  if (extra) throw new ScenarioError(`Unknown hypothetical site field: ${extra}.`);
  return {
    id: textField(site.id, 'Hypothetical site id'), name: textField(site.name, 'Hypothetical site name'),
    lon: boundedNumber(site.lon, 'Hypothetical longitude', -180, 180),
    lat: boundedNumber(site.lat, 'Hypothetical latitude', -90, 90),
  };
}

/** Validate imported scenarios before touching a graph; canonicalize ordering for replay. */
export function validateScenario(input: unknown, context: ScenarioValidationContext = {}): Scenario {
  const value = record(input, 'Scenario');
  const extra = Object.keys(value).find((key) => !SCENARIO_FIELDS.includes(key));
  if (extra) throw new ScenarioError(`Unknown scenario field: ${extra}.`);
  if (value.version !== 1) throw new ScenarioError('This scenario version is unsupported. Use a version 1 scenario.');
  const county = textField(value.county, 'County');
  if (!/^24\d{3}$/.test(county)) throw new ScenarioError('County must be a five-digit Maryland county GEOID.');
  if (!Array.isArray(value.hypotheticalSites) || value.hypotheticalSites.length > MAX_SELECTED_SITES) {
    throw new ScenarioError(`Hypothetical sites must contain at most ${MAX_SELECTED_SITES} sites.`);
  }
  const hypotheticalSites = value.hypotheticalSites.map(siteFromUnknown).sort((a, b) => a.id.localeCompare(b.id, 'en'));
  unique(hypotheticalSites.map((site) => site.id), 'Hypothetical sites');
  const hubIds = stringList(value.hubIds, 'Selected sites');
  if (hubIds.length + hypotheticalSites.length > MAX_SELECTED_SITES) throw new ScenarioError(`Choose at most ${MAX_SELECTED_SITES} additional sites.`);
  if (!Array.isArray(value.closedEdgeIds) || value.closedEdgeIds.length > 10000) throw new ScenarioError('Road closures must be an array of at most 10000 directed edge identifiers.');
  const closedEdgeIds = unique(value.closedEdgeIds.map((id) => boundedNumber(id, 'Closed edge identifier', 0, Number.MAX_SAFE_INTEGER, true)), 'Road closures').sort((a, b) => a - b);
  const scenario: Scenario = {
    version: 1, dataRevision: textField(value.dataRevision, 'Data revision'), graphRevision: textField(value.graphRevision, 'Road revision'),
    county, measure: textField(value.measure, 'Household measure'),
    radiusMiles: boundedNumber(value.radiusMiles, 'Road distance', 1, 10),
    additions: boundedNumber(value.additions, 'Additional sites', 1, MAX_SELECTED_SITES, true),
    hubIds, hypotheticalSites, closedEdgeIds,
  };
  if (!(HOUSEHOLD_DEMAND_MEASURES as readonly string[]).includes(scenario.measure)) throw new ScenarioError(`${scenario.measure} is not an available household demand measure. Choose households, noVehicle, or noInternet.`);
  const { graph, facilities, measures } = context;
  const dataRevision = context.dataRevision ?? graph?.dataRevision;
  if (dataRevision !== undefined && scenario.dataRevision !== dataRevision) throw new ScenarioError('This scenario uses a different data release. Load its pinned release or create a new scenario.');
  if (graph && scenario.graphRevision !== graph.revision) throw new ScenarioError('This scenario uses a different road release. Load its pinned road graph or create a new scenario.');
  if (graph && scenario.county !== graph.county) throw new ScenarioError('The selected county does not match this road graph.');
  if (graph) {
    for (const id of closedEdgeIds) if (id >= graph.edges.length) throw new ScenarioError(`Road closure ${id} does not exist in this road release.`);
  }
  if (measures && !measures.includes(scenario.measure)) throw new ScenarioError(`${scenario.measure} is not an available household demand measure.`);
  if (facilities) {
    const byId = new Map(facilities.map((facility) => [facility.id, facility]));
    for (const id of hubIds) {
      const facility = byId.get(id);
      if (!facility || facility.status !== 'verified' || !facility.candidate || facility.baseline || facility.county !== scenario.county) {
        throw new ScenarioError(`Selected site ${id} is not a verified candidate in this county.`);
      }
      if (graph && graph.facilityNodes[id] == null) throw new ScenarioError(`Selected site ${id} could not be matched to the road graph.`);
    }
    for (const site of hypotheticalSites) if (byId.has(site.id)) throw new ScenarioError(`Hypothetical site ${site.id} conflicts with an existing facility identifier.`);
  }
  for (const site of hypotheticalSites) if (hubIds.includes(site.id)) throw new ScenarioError(`Hypothetical site ${site.id} must appear only in hypotheticalSites.`);
  return scenario;
}

export function createDefaultScenario(graph: RoadGraph, dataRevision = graph.dataRevision, measure = 'households'): Scenario {
  return { version: 1, dataRevision, graphRevision: graph.revision, county: graph.county, measure, radiusMiles: 3, additions: 3, hubIds: [], hypotheticalSites: [], closedEdgeIds: [] };
}
export function serializeScenario(scenario: Scenario): string { return JSON.stringify(validateScenario(scenario), null, 2); }
export function parseScenario(json: string, context: ScenarioValidationContext = {}): Scenario {
  if (typeof json !== 'string' || json.length > 200000) throw new ScenarioError('Scenario JSON must be text of at most 200000 characters.');
  let input: unknown;
  try { input = JSON.parse(json); } catch { throw new ScenarioError('The scenario is not valid JSON.'); }
  return validateScenario(input, context);
}
export function encodeScenario(scenario: Scenario): string {
  const bytes = new TextEncoder().encode(JSON.stringify(validateScenario(scenario)));
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
export function decodeScenario(encoded: string, context: ScenarioValidationContext = {}): Scenario {
  if (typeof encoded !== 'string' || encoded.length > 270000 || !/^[A-Za-z0-9_-]+$/.test(encoded)) throw new ScenarioError('The shared scenario code is invalid.');
  let json: string;
  try {
    const binary = atob(encoded.replace(/-/g, '+').replace(/_/g, '/'));
    json = new TextDecoder('utf-8', { fatal: true }).decode(Uint8Array.from(binary, (character) => character.charCodeAt(0)));
  } catch { throw new ScenarioError('The shared scenario code could not be decoded.'); }
  return parseScenario(json, context);
}

interface GraphIndex { incoming: number[][]; }
const graphIndexes = new WeakMap<RoadGraph, GraphIndex>();
function graphIndex(graph: RoadGraph): GraphIndex {
  const existing = graphIndexes.get(graph);
  if (existing) return existing;
  if (graph.version !== 1 || graph.nodes.length === 0) throw new ScenarioError('This road graph has no supported road nodes.');
  const incoming: number[][] = Array.from({ length: graph.nodes.length }, () => []);
  for (let node = 0; node < graph.nodes.length; node++) {
    const point = graph.nodes[node];
    if (!Array.isArray(point) || point.length !== 2 || !Number.isFinite(point[0]) || !Number.isFinite(point[1]) || Math.abs(point[0]) > 180 || Math.abs(point[1]) > 90) {
      throw new ScenarioError(`Road node ${node} has invalid coordinates.`);
    }
  }
  for (let id = 0; id < graph.edges.length; id++) {
    const edge = graph.edges[id];
    if (!Array.isArray(edge) || edge.length !== 3 || !Number.isInteger(edge[0]) || !Number.isInteger(edge[1]) || edge[0] < 0 || edge[1] < 0 || edge[0] >= graph.nodes.length || edge[1] >= graph.nodes.length || !Number.isFinite(edge[2]) || edge[2] <= 0) {
      throw new ScenarioError(`Road edge ${id} has invalid endpoints or distance.`);
    }
    incoming[edge[1]].push(id);
  }
  for (const [kind, mappings] of [['Area', graph.areaNodes], ['Facility', graph.facilityNodes]] as const) {
    for (const [id, node] of Object.entries(mappings)) {
      if (node != null && (!Number.isInteger(node) || node < 0 || node >= graph.nodes.length)) throw new ScenarioError(`${kind} ${id} has an invalid road-node mapping.`);
    }
  }
  const index = { incoming };
  graphIndexes.set(graph, index);
  return index;
}
interface HeapItem { node: number; distance: number; hubId: string; }
function earlier(a: HeapItem, b: HeapItem): boolean {
  return a.distance < b.distance || (a.distance === b.distance && (a.hubId < b.hubId || (a.hubId === b.hubId && a.node < b.node)));
}
class MinHeap {
  private items: HeapItem[] = [];
  push(item: HeapItem): void {
    const items = this.items;
    let index = items.length;
    items.push(item);
    while (index > 0) {
      const parent = (index - 1) >>> 1;
      if (!earlier(item, items[parent])) break;
      items[index] = items[parent]; index = parent;
    }
    items[index] = item;
  }
  pop(): HeapItem | undefined {
    const items = this.items;
    if (!items.length) return undefined;
    const first = items[0], last = items.pop()!;
    if (items.length) {
      let index = 0;
      while (index * 2 + 1 < items.length) {
        let child = index * 2 + 1;
        if (child + 1 < items.length && earlier(items[child + 1], items[child])) child++;
        if (!earlier(items[child], last)) break;
        items[index] = items[child]; index = child;
      }
      items[index] = last;
    }
    return first;
  }
}
interface Hub { id: string; node: number; }
interface Routes { distances: Float64Array; hubIds: (string | null)[]; nextEdges: Int32Array; }
/** Reverse search computes area -> hub routes without reversing travel permissions. */
function routesToHubs(graph: RoadGraph, index: GraphIndex, hubs: Hub[], closures: Set<number>, maximumDistance = Infinity): Routes {
  const distances = new Float64Array(graph.nodes.length).fill(Infinity);
  const hubIds: (string | null)[] = Array(graph.nodes.length).fill(null);
  const nextEdges = new Int32Array(graph.nodes.length).fill(-1);
  const heap = new MinHeap();
  for (const hub of [...hubs].sort((a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0)) {
    if (distances[hub.node] === 0) continue;
    distances[hub.node] = 0; hubIds[hub.node] = hub.id;
    heap.push({ node: hub.node, distance: 0, hubId: hub.id });
  }
  let current: HeapItem | undefined;
  while ((current = heap.pop())) {
    const { node, distance, hubId } = current;
    if (distance !== distances[node] || hubIds[node] !== hubId) continue;
    for (const edgeId of index.incoming[node]) {
      if (closures.has(edgeId)) continue;
      const [from, , length] = graph.edges[edgeId];
      const proposed = distance + length;
      if (proposed > maximumDistance + DISTANCE_EPSILON) continue;
      const improvement = proposed < distances[from];
      const tie = proposed === distances[from];
      const previousHub = hubIds[from];
      const betterHub = tie && (previousHub === null || hubId < previousHub);
      if (improvement || betterHub) {
        distances[from] = proposed; hubIds[from] = hubId; nextEdges[from] = edgeId;
        heap.push({ node: from, distance: proposed, hubId });
      } else if (tie && hubId === previousHub && (nextEdges[from] < 0 || edgeId < nextEdges[from])) {
        nextEdges[from] = edgeId;
      }
    }
  }
  return { distances, hubIds, nextEdges };
}
function distanceMetres(a: [number, number], b: [number, number]): number {
  const radians = Math.PI / 180;
  const deltaLat = (b[1] - a[1]) * radians, deltaLon = (b[0] - a[0]) * radians;
  const value = Math.sin(deltaLat / 2) ** 2 + Math.cos(a[1] * radians) * Math.cos(b[1] * radians) * Math.sin(deltaLon / 2) ** 2;
  return 6371008.8 * 2 * Math.atan2(Math.sqrt(value), Math.sqrt(Math.max(0, 1 - value)));
}
export function snapHypotheticalSite(site: HypotheticalSite, graph: RoadGraph): number {
  let nearest = -1, distance = Infinity;
  for (let node = 0; node < graph.nodes.length; node++) {
    const metres = distanceMetres([site.lon, site.lat], graph.nodes[node]);
    if (metres < distance) { nearest = node; distance = metres; }
  }
  if (nearest < 0 || distance > HYPOTHETICAL_SNAP_METRES) {
    throw new ScenarioError(`${site.name} is more than ${HYPOTHETICAL_SNAP_METRES} metres from a mapped road node. Choose a point closer to a mapped street.`);
  }
  return nearest;
}
interface DemandArea { area: Area; demand: number | null; node: number | null; }
function demandAreas(areas: Area[], graph: RoadGraph, scenario: Scenario): DemandArea[] {
  const selected = areas.filter((area) => area.county === scenario.county && area.level === 'blockGroup').sort((a, b) => a.geoid < b.geoid ? -1 : a.geoid > b.geoid ? 1 : 0);
  unique(selected.map((area) => area.geoid), 'Study area block groups');
  return selected.map((area) => {
    const value = area.values[scenario.measure];
    // These three catalog measures are household counts; percentages are not demand.
    const estimate = value?.value;
    const demand = value?.status === 'available' && typeof estimate === 'number' && Number.isFinite(estimate) && estimate >= 0 ? estimate : null;
    return { area, demand, node: graph.areaNodes[area.geoid] ?? null };
  });
}
function classify(area: DemandArea, routes: Routes, radius: number): AreaAccess['state'] {
  if (area.demand === null) return 'missing';
  if (area.node === null) return 'unmatched';
  const distance = routes.distances[area.node];
  if (!Number.isFinite(distance)) return 'disconnected';
  return distance <= radius + DISTANCE_EPSILON ? 'covered' : 'beyond';
}
function coverage(areas: DemandArea[], routes: Routes, radius: number): Coverage {
  const result: Coverage = { total: 0, covered: 0, beyond: 0, disconnected: 0, unmatched: 0, missingAreas: 0 };
  for (const area of areas) {
    const state = classify(area, routes, radius);
    if (state === 'missing') { result.missingAreas++; continue; }
    const demand = area.demand!;
    result.total += demand;
    result[state] += demand;
  }
  return result;
}

export function runSimulation(input: SimulationInput): SimulationResult {
  const start = performance.now();
  const { graph, areas, facilities, suggest } = input;
  const index = graphIndex(graph);
  const scenario = validateScenario(input.scenario, { graph, facilities });
  const demands = demandAreas(areas, graph, scenario);
  if (!demands.length) throw new ScenarioError('No block-group observations are available for this county.');
  if (!demands.some((area) => Object.hasOwn(area.area.values, scenario.measure))) throw new ScenarioError(`Household measure ${scenario.measure} is not available in this county.`);
  const closures = new Set(scenario.closedEdgeIds);
  // Geographic access includes verified assistance sites across county borders
  // wherever the county-buffered graph can reach them. Candidate additions
  // remain part of the selected study county.
  const baselineHubs: Hub[] = facilities.filter((facility) => facility.status === 'verified' && facility.baseline && graph.facilityNodes[facility.id] != null).map((facility) => ({ id: facility.id, node: graph.facilityNodes[facility.id]! }));
  const selectedHubs: Hub[] = scenario.hubIds.map((id) => ({ id, node: graph.facilityNodes[id]! }));
  for (const site of scenario.hypotheticalSites) selectedHubs.push({ id: site.id, node: snapHypotheticalSite(site, graph) });
  const baselineRoutes = routesToHubs(graph, index, baselineHubs, closures);
  let proposedRoutes = routesToHubs(graph, index, [...baselineHubs, ...selectedHubs], closures);
  const suggested: SimulationResult['suggested'] = [];
  if (suggest) {
    const chosen = new Set([...baselineHubs, ...selectedHubs].map((hub) => hub.id));
    const candidates = facilities.filter((facility) => facility.county === scenario.county && facility.status === 'verified' && facility.candidate && !facility.baseline && !chosen.has(facility.id) && graph.facilityNodes[facility.id] != null).sort((a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
    // Candidate searches stop at the threshold and retain only reached areas. This
    // keeps statewide-sized graphs from multiplying memory by candidate count.
    const candidateAreas = new Map<string, number[]>();
    for (const candidate of candidates) {
      const routes = routesToHubs(graph, index, [{ id: candidate.id, node: graph.facilityNodes[candidate.id]! }], closures, scenario.radiusMiles);
      const reached: number[] = [];
      for (let areaIndex = 0; areaIndex < demands.length; areaIndex++) {
        const area = demands[areaIndex];
        if (area.demand !== null && area.node !== null && routes.distances[area.node] <= scenario.radiusMiles + DISTANCE_EPSILON) reached.push(areaIndex);
      }
      candidateAreas.set(candidate.id, reached);
    }
    while (selectedHubs.length < scenario.additions) {
      let best: Facility | null = null, incremental = 0;
      for (const candidate of candidates) {
        if (chosen.has(candidate.id)) continue;
        let added = 0;
        for (const areaIndex of candidateAreas.get(candidate.id)!) {
          const area = demands[areaIndex];
          if (proposedRoutes.distances[area.node!] > scenario.radiusMiles + DISTANCE_EPSILON) added += area.demand!;
        }
        if (added > incremental + DISTANCE_EPSILON) { best = candidate; incremental = added; }
      }
      if (!best) break;
      selectedHubs.push({ id: best.id, node: graph.facilityNodes[best.id]! }); chosen.add(best.id);
      scenario.hubIds.push(best.id); scenario.hubIds.sort();
      suggested.push({ id: best.id, incremental });
      proposedRoutes = routesToHubs(graph, index, [...baselineHubs, ...selectedHubs], closures);
    }
  }
  const flows = new Map<number, number>();
  const access: AreaAccess[] = demands.map((area) => {
    const state = classify(area, proposedRoutes, scenario.radiusMiles);
    const baselineDistance = area.node === null ? Infinity : baselineRoutes.distances[area.node];
    const proposedDistance = area.node === null ? Infinity : proposedRoutes.distances[area.node];
    if (state === 'covered' && area.demand! > 0 && area.node !== null) {
      let node = area.node, hops = 0;
      while (proposedRoutes.nextEdges[node] !== -1) {
        const edgeId = proposedRoutes.nextEdges[node];
        if (closures.has(edgeId)) throw new ScenarioError('The route unexpectedly includes a closed road. Recalculate this scenario.');
        flows.set(edgeId, (flows.get(edgeId) ?? 0) + area.demand!);
        node = graph.edges[edgeId][1];
        if (++hops > graph.nodes.length) throw new ScenarioError('The calculated route contains a cycle. Check the road release.');
      }
    }
    return { geoid: area.area.geoid, demand: area.demand, baselineMiles: Number.isFinite(baselineDistance) ? baselineDistance : null, proposedMiles: Number.isFinite(proposedDistance) ? proposedDistance : null, hubId: area.node === null ? null : proposedRoutes.hubIds[area.node], state };
  });
  return {
    scenario, baseline: coverage(demands, baselineRoutes, scenario.radiusMiles), proposed: coverage(demands, proposedRoutes, scenario.radiusMiles),
    suggested, areas: access, flows: [...flows.entries()].sort(([a], [b]) => a - b).map(([edgeId, demand]) => ({ edgeId, demand })), elapsedMs: performance.now() - start,
  };
}
