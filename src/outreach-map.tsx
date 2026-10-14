import { useEffect, useMemo, useRef, useState } from 'react';
import * as maplibregl from 'maplibre-gl';
import mapLibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';
import type { FeatureCollection, Geometry, LineString, Point } from 'geojson';
import type { Area, Catalog, CommuteData, Facility, Indicator, RoadGraph, Scenario, SimulationResult } from './outreach';
import 'maplibre-gl/dist/maplibre-gl.css';

// Bundle the module worker and its shared imports for both Vite development and release assets.
maplibregl.setWorkerUrl(mapLibreWorkerUrl);

const empty: FeatureCollection = { type: 'FeatureCollection', features: [] };
const integers = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
const colors = ['#e2edf3', '#b7d4e4', '#79acd0', '#4381bc', '#2452bd'];
export function useReducedMotion() {
  const [reduced, setReduced] = useState(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  useEffect(() => { const query = window.matchMedia('(prefers-reduced-motion: reduce)'); const update = () => setReduced(query.matches); query.addEventListener('change', update); return () => query.removeEventListener('change', update); }, []);
  return reduced;
}
export interface OutreachMapProps {
  catalog: Catalog;
  areas: Area[];
  facilities: Facility[];
  indicator: Indicator;
  county: string;
  graph: RoadGraph | null;
  scenario: Scenario | null;
  result: SimulationResult | null;
  selected: string | null;
  geometryLevel: 'blockGroup' | 'tract';
  commutes: CommuteData | null;
  addingSite: boolean;
  closingRoad: boolean;
  animationPaused: boolean;
  animationEpoch: number;
  focusRoadEdgeId: number | null;
  onArea: (geoid: string) => void;
  onFacility: (id: string) => void;
  onLocation: (lon: number, lat: number) => void;
  onRoad: (edgeId: number) => void;
  onFailure: (message: string) => void;
}
export default function OutreachMap(props: OutreachMapProps) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const current = useRef(props); current.current = props;
  const [ready, setReady] = useState(false);
  const [boundaries, setBoundaries] = useState<FeatureCollection | null>(null);
  const [mapError, setMapError] = useState<string | null>(null);
  const [hover, setHover] = useState('Select a neighborhood to inspect its evidence');
  const reduced = useReducedMotion();
  const firstFit = useRef(true);
  const flowState = useRef<{ result: SimulationResult | null; epoch: number; strength: number }>({ result: null, epoch: -1, strength: 0 });
  const values = useMemo(() => props.areas.map((a) => a.values[props.indicator.id]?.value).filter((v): v is number => typeof v === 'number').sort((a, b) => a - b), [props.areas, props.indicator.id]);
  const stops = useMemo(() => [0, .25, .5, .75, 1].map((q) => values[Math.floor(q * Math.max(0, values.length - 1))] ?? 0), [values]);
  useEffect(() => {
    const controller = new AbortController(); setBoundaries(null);
    const path = props.geometryLevel === 'tract' ? props.catalog.assets.tracts : props.catalog.assets.blockGroups;
    fetch(path, { signal: controller.signal }).then(async (response) => { if (!response.ok) throw new Error(`Neighborhood boundaries could not load (HTTP ${response.status}).`); const value = await response.json() as FeatureCollection; if (value.type !== 'FeatureCollection' || !Array.isArray(value.features)) throw new Error('The boundary file has an invalid format.'); return value; }).then(setBoundaries).catch((error: unknown) => { if (!controller.signal.aborted) props.onFailure(error instanceof Error ? error.message : 'Neighborhood boundaries could not load.'); });
    return () => controller.abort();
  }, [props.catalog.assets.blockGroups, props.catalog.assets.tracts, props.geometryLevel]);
  useEffect(() => {
    if (!container.current) return;
    try {
      const instance = new maplibregl.Map({ container: container.current, center: [-76.95, 39.0], zoom: 6.7, minZoom: 5, maxZoom: 17, maxBounds: [[-80.2, 37.5], [-74.2, 40.8]], attributionControl: false, style: { version: 8, sources: {}, layers: [{ id: 'background', type: 'background', paint: { 'background-color': '#dfeef4' } }] } });
      map.current = instance;
      instance.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
      instance.addControl(new maplibregl.AttributionControl({ compact: true, customAttribution: ['<a href="https://www.census.gov/">U.S. Census Bureau</a>', '<a href="https://www.openstreetmap.org/copyright">© OpenStreetMap contributors</a>'] }), 'bottom-right');
      instance.on('load', () => {
        instance.addSource('counties', { type: 'geojson', data: '/data/maryland-counties.geojson' });
        instance.addLayer({ id: 'county-fill', type: 'fill', source: 'counties', paint: { 'fill-color': '#f5f7f7', 'fill-opacity': .95 } });
        instance.addLayer({ id: 'county-outline', type: 'line', source: 'counties', paint: { 'line-color': '#6a8798', 'line-width': .8 } });
        for (const id of ['areas', 'roads', 'closedRoads', 'flows', 'locations', 'commutes']) instance.addSource(id, { type: 'geojson', data: empty });
        instance.addLayer({ id: 'area-fill', type: 'fill', source: 'areas', paint: { 'fill-color': '#bed5e5', 'fill-opacity': .88 } });
        instance.addLayer({ id: 'area-outline', type: 'line', source: 'areas', paint: { 'line-color': '#ffffff', 'line-width': .6, 'line-opacity': .8 } });
        instance.addLayer({ id: 'selected-area', type: 'line', source: 'areas', filter: ['==', ['get', 'geoid'], ''], paint: { 'line-color': '#f4b844', 'line-width': 3 } });
        instance.addLayer({ id: 'roads', type: 'line', source: 'roads', minzoom: 10, paint: { 'line-color': '#657b8a', 'line-opacity': .3, 'line-width': ['interpolate', ['linear'], ['zoom'], 10, .3, 15, 1.5] } });
        instance.addLayer({ id: 'closed-roads', type: 'line', source: 'closedRoads', paint: { 'line-color': '#b5413c', 'line-width': 4, 'line-dasharray': [2, 1] } });
        instance.addLayer({ id: 'road-hit', type: 'line', source: 'roads', minzoom: 10, paint: { 'line-color': '#ffffff', 'line-width': 14, 'line-opacity': .001 } });
        instance.addLayer({ id: 'commute-lines', type: 'line', source: 'commutes', paint: { 'line-color': '#6755a7', 'line-opacity': .25, 'line-width': ['interpolate', ['linear'], ['get', 'jobs'], 0, .5, 1000, 3] } });
        instance.addLayer({ id: 'flow-halo', type: 'line', source: 'flows', paint: { 'line-color': '#fff8df', 'line-opacity': .7, 'line-width': ['+', 2, ['*', 4, ['get', 'q']]] } });
        instance.addLayer({ id: 'flow-lines', type: 'line', source: 'flows', paint: { 'line-color': '#dc9630', 'line-opacity': .95, 'line-width': ['+', .5, ['*', 3.5, ['get', 'q']]], 'line-dasharray': [3, 1] } });
        instance.addLayer({ id: 'location-halo', type: 'circle', source: 'locations', paint: { 'circle-radius': ['case', ['get', 'selected'], 12, 8], 'circle-color': '#ffffff', 'circle-opacity': .9 } });
        instance.addLayer({ id: 'location-points', type: 'circle', source: 'locations', paint: { 'circle-radius': ['case', ['get', 'selected'], 8, 5], 'circle-color': ['match', ['get', 'role'], 'baseline', '#202b33', 'proposed', '#dc9630', '#ffffff'], 'circle-stroke-color': ['match', ['get', 'role'], 'baseline', '#202b33', 'proposed', '#a76616', '#2452bd'], 'circle-stroke-width': 2 } });
        instance.on('click', (event) => {
          const p = current.current;
          if (p.addingSite) { p.onLocation(event.lngLat.lng, event.lngLat.lat); return; }
          if (p.closingRoad) { const roads = instance.queryRenderedFeatures(event.point, { layers: ['road-hit'] }); if (roads[0]) p.onRoad(Number(roads[0].properties?.edgeId)); return; }
          const locations = instance.queryRenderedFeatures(event.point, { layers: ['location-points', 'location-halo'] });
          if (locations[0]) { p.onFacility(String(locations[0].properties?.id)); return; }
          const areas = instance.queryRenderedFeatures(event.point, { layers: ['area-fill'] });
          if (areas[0]) p.onArea(String(areas[0].properties?.geoid));
        });
        instance.on('mousemove', (event) => {
          const p = current.current;
          const locations = instance.queryRenderedFeatures(event.point, { layers: ['location-points'] });
          const areas = instance.queryRenderedFeatures(event.point, { layers: ['area-fill'] });
          instance.getCanvas().style.cursor = p.addingSite ? 'crosshair' : locations.length || areas.length || p.closingRoad ? 'pointer' : '';
          if (locations[0]) setHover(String(locations[0].properties?.name));
          else if (p.closingRoad) { const roads = instance.queryRenderedFeatures(event.point, { layers: ['road-hit'] }); const closed = roads[0] && (p.scenario?.closedEdgeIds.includes(Number(roads[0].properties?.edgeId)) || p.scenario?.closedEdgeIds.includes(Number(roads[0].properties?.reverseId))); setHover(roads[0] ? `${roads[0].properties?.name || 'Unnamed road'} · click to ${closed ? 'reopen' : 'close'}` : 'Zoom in and select a road to change its availability'); }
          else if (areas[0]) { const f = areas[0].properties; setHover(`${f?.name} · ${f?.value == null ? 'Not reported' : integers.format(Number(f.value))} ${p.indicator.unit === 'percent' ? '%' : p.indicator.unit === 'dollars' ? 'dollars' : ''}`); }
        });
        setReady(true);
      });
      instance.on('error', (event) => { if (!event.error.message.includes('WebGL')) current.current.onFailure(`A map layer could not load: ${event.error.message} Neighborhood records remain available in the tables.`); if (event.error.message.includes('WebGL')) { setMapError('The map needs WebGL. All neighborhood values and scenario controls are available in the tables below.'); current.current.onFailure('The map needs WebGL; use the equivalent tables.'); } });
      return () => { instance.remove(); map.current = null; };
    } catch (error) {
      const message = `The map could not start. ${error instanceof Error ? error.message : ''} Use the neighborhood and facility tables below.`;
      setMapError(message); current.current.onFailure(message);
    }
  }, []);
  useEffect(() => {
    if (!ready || !map.current || !boundaries) return;
    const byId = new Map(props.areas.map((a) => [a.geoid, a]));
    const data: FeatureCollection = { ...boundaries, features: boundaries.features.map((f) => { const id = String(f.properties?.GEOID ?? f.properties?.geoid ?? ''); const area = byId.get(id); return { ...f, properties: { ...f.properties, geoid: id, name: area?.name ?? `Census area ${id}`, value: area?.values[props.indicator.id]?.value ?? null } }; }) };
    (map.current.getSource('areas') as maplibregl.GeoJSONSource).setData(data);
    const distinct = [...new Set(stops)];
    const fill: maplibregl.ExpressionSpecification = distinct.length > 1 ? ['case', ['==', ['get', 'value'], null], '#dce2e7', ['interpolate', ['linear'], ['get', 'value'], ...distinct.flatMap((s, i) => [s, colors[Math.round(i / (distinct.length - 1) * 4)]])]] : ['case', ['==', ['get', 'value'], null], '#dce2e7', '#79acd0'];
    map.current.setPaintProperty('area-fill', 'fill-color', fill);
  }, [ready, boundaries, props.areas, props.indicator.id, stops]);
  useEffect(() => { if (ready && map.current) map.current.setFilter('selected-area', ['==', ['get', 'geoid'], props.selected ?? '']); }, [ready, props.selected]);
  useEffect(() => {
    if (!ready || !map.current) return;
    const countyAreas = props.areas.filter((a) => a.county === props.county); if (!countyAreas.length) return;
    const points = countyAreas.map((a) => [a.lon, a.lat] as [number, number]);
    const bounds = new maplibregl.LngLatBounds(points[0], points[0]); for (const point of points) bounds.extend(point);
    const instance = map.current;
    const timer = window.setTimeout(() => instance.fitBounds(bounds, { padding: { top: 64, bottom: 60, left: 35, right: 55 }, maxZoom: 12, duration: reduced ? 0 : firstFit.current ? 2000 : 900 }), firstFit.current && !reduced ? 800 : 0);
    firstFit.current = false;
    return () => window.clearTimeout(timer);
  }, [ready, props.county, props.areas, reduced]);
  useEffect(() => {
    if (!ready || !map.current || props.focusRoadEdgeId === null || !props.graph) return;
    const edge = props.graph.edges[props.focusRoadEdgeId]; if (!edge) return;
    const shape = props.graph.shapes[props.focusRoadEdgeId] ?? [props.graph.nodes[edge[0]], props.graph.nodes[edge[1]]];
    const bounds = new maplibregl.LngLatBounds(shape[0], shape[0]); for (const point of shape) bounds.extend(point);
    map.current.fitBounds(bounds, { padding: 95, maxZoom: 15, duration: reduced ? 0 : 900 });
  }, [ready, props.graph, props.focusRoadEdgeId, reduced]);
  useEffect(() => {
    if (!ready || !map.current) return;
    const graph = props.graph;
    const data: FeatureCollection<LineString> = { type: 'FeatureCollection', features: [] };
    if (graph) {
      const exactAdjacentReverse = (firstId: number, secondId: number): boolean => {
        const a = graph.edges[firstId]; const b = graph.edges[secondId]; if (!a || !b || a[0] !== b[1] || a[1] !== b[0] || a[2] !== b[2]) return false;
        const first = graph.shapes[firstId] ?? [graph.nodes[a[0]], graph.nodes[a[1]]]; const second = graph.shapes[secondId] ?? [graph.nodes[b[0]], graph.nodes[b[1]]];
        return first.length === second.length && first.every((point, index) => point[0] === second[second.length - 1 - index][0] && point[1] === second[second.length - 1 - index][1]);
      };
      for (let edgeId = 0; edgeId < graph.edges.length; edgeId++) {
        const edge = graph.edges[edgeId]; const reverseId = exactAdjacentReverse(edgeId, edgeId + 1) ? edgeId + 1 : -1;
        data.features.push({ type: 'Feature', geometry: { type: 'LineString', coordinates: graph.shapes[edgeId] ?? [graph.nodes[edge[0]], graph.nodes[edge[1]]] }, properties: { edgeId, reverseId, name: graph.names[edgeId] ?? '' } });
        // The engine retains both directions; the context map draws identical physical geometry once.
        if (reverseId !== -1) edgeId++;
      }
    }
    (map.current.getSource('roads') as maplibregl.GeoJSONSource).setData(data);
  }, [ready, props.graph]);
  useEffect(() => {
    if (!ready || !map.current) return;
    const graph = props.graph;
    const data: FeatureCollection<LineString> = { type: 'FeatureCollection', features: graph ? (props.scenario?.closedEdgeIds ?? []).map((edgeId) => {
      const edge = graph.edges[edgeId];
      return { type: 'Feature', geometry: { type: 'LineString', coordinates: graph.shapes[edgeId] ?? [graph.nodes[edge[0]], graph.nodes[edge[1]]] }, properties: { edgeId } };
    }) : [] };
    (map.current.getSource('closedRoads') as maplibregl.GeoJSONSource).setData(data);
  }, [ready, props.graph, props.scenario?.closedEdgeIds]);
  useEffect(() => {
    if (!ready || !map.current) return;
    const selected = new Set(props.scenario?.hubIds ?? []);
    const points = props.facilities.filter((f) => f.status === 'verified' && (f.baseline || f.candidate)).map((f) => ({ id: f.id, name: f.name, lon: f.lon, lat: f.lat, role: f.baseline ? 'baseline' : selected.has(f.id) ? 'proposed' : 'candidate', selected: selected.has(f.id) }));
    for (const f of props.scenario?.hypotheticalSites ?? []) points.push({ ...f, role: 'proposed', selected: true });
    const data: FeatureCollection<Point> = { type: 'FeatureCollection', features: points.map((p) => ({ type: 'Feature', geometry: { type: 'Point', coordinates: [p.lon, p.lat] }, properties: p })) };
    (map.current.getSource('locations') as maplibregl.GeoJSONSource).setData(data);
  }, [ready, props.facilities, props.scenario?.hubIds, props.scenario?.hypotheticalSites]);
  useEffect(() => {
    if (!ready || !map.current) return;
    const data: FeatureCollection<LineString> = { type: 'FeatureCollection', features: props.commutes?.flows.map((flow) => ({ type: 'Feature', geometry: { type: 'LineString', coordinates: [flow.from, flow.to] }, properties: { jobs: flow.jobs } })) ?? [] };
    (map.current.getSource('commutes') as maplibregl.GeoJSONSource).setData(data);
  }, [ready, props.commutes]);
  useEffect(() => {
    if (!ready || !map.current) return;
    const graph = props.graph; const flows = props.result?.flows ?? [];
    const maximum = flows.reduce((max, flow) => Math.max(max, flow.demand), 1);
    const data: FeatureCollection<Geometry> = { type: 'FeatureCollection', features: graph ? flows.map((flow) => {
      const edge = graph.edges[flow.edgeId];
      return { type: 'Feature', geometry: { type: 'LineString', coordinates: graph.shapes[flow.edgeId] ?? [graph.nodes[edge[0]], graph.nodes[edge[1]]] }, properties: { q: flow.demand / maximum, demand: flow.demand } };
    }) : [] };
    // Geometry is published once per calculated result. Only paint changes during playback.
    (map.current.getSource('flows') as maplibregl.GeoJSONSource).setData(data);
  }, [ready, props.graph, props.result]);
  useEffect(() => {
    if (!ready || !map.current) return;
    if (flowState.current.result !== props.result || flowState.current.epoch !== props.animationEpoch) flowState.current = { result: props.result, epoch: props.animationEpoch, strength: 0 };
    let iteration = 0;
    const tick = () => {
      if (!map.current) return;
      // With fixed Q and D_0=0, every edge shares D_t=Q*g_t; g_next=.8g+.2.
      // Advancing one scalar therefore gives exactly the same per-edge EMA.
      if (reduced) flowState.current.strength = 1;
      else if (!props.animationPaused) flowState.current.strength = .8 * flowState.current.strength + .2;
      const strength = flowState.current.strength;
      map.current.setPaintProperty('flow-halo', 'line-width', ['+', 2, ['*', 4, strength, ['get', 'q']]]);
      map.current.setPaintProperty('flow-lines', 'line-width', ['+', .5, ['*', 3.5, strength, ['get', 'q']]]);
      if (!reduced && !props.animationPaused) map.current.setPaintProperty('flow-lines', 'line-dasharray', [[3, 1], [2, 1, 1, 1], [1, 1, 2, 1]][iteration++ % 3]);
    };
    tick(); if (reduced || props.animationPaused || !props.result?.flows.length) return;
    const timer = window.setInterval(tick, 200); return () => window.clearInterval(timer);
  }, [ready, props.result, props.animationEpoch, props.animationPaused, reduced]);
  return <div className={`outreach-map ${props.addingSite ? 'adding-site' : ''}`}>
    <div ref={container} className="map-surface" aria-label="Maryland neighborhood map; equivalent values and controls follow in the tables" />
    {mapError && <div className="map-failure" role="status"><strong>Explore through the tables</strong><p>{mapError}</p></div>}
    {!ready && !mapError && <div className="map-starting" role="status">Drawing Maryland’s neighborhoods…</div>}
    <div className="map-titleplate"><span>CivicAtlas / Maryland</span><strong>{props.catalog.counties.find((c) => c.geoid === props.county)?.name}</strong><small>{props.indicator.label} · {props.indicator.period}</small></div>
    <div className="map-scale"><span>{props.indicator.label}</span><div className="shading-scale" /><div><small>{integers.format(stops[0])}{props.indicator.unit === 'percent' ? '%' : ''}</small><small>{integers.format(stops[4])}{props.indicator.unit === 'percent' ? '%' : ''}</small></div><small>Gray: unavailable · shading across loaded areas</small></div>
    <div className="map-readout-bar" aria-live="polite"><span className="live-dot" />{props.addingSite ? 'Select a point on the map to add a hypothetical outreach site' : hover}</div>
  </div>;
}
