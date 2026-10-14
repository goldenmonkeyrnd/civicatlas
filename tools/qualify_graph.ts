import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import type { Area, Facility, RoadGraph } from '../src/outreach';
import { createDefaultScenario, runSimulation, serializeScenario, parseScenario } from '../src/simulation';

const county = process.argv[2] ?? '24510';
const areas = JSON.parse(readFileSync('data/outreach/areas.json', 'utf8')) as Area[];
const facilities = JSON.parse(readFileSync('data/outreach/facilities.json', 'utf8')) as Facility[];
const revision = (JSON.parse(readFileSync('data/outreach/catalog.json','utf8')) as {revision:string}).revision;
const source = process.argv[3] ?? `data/outreach/graphs/${county}.json`;
const content = readFileSync(source);
const graph = JSON.parse((source.endsWith('.gz') ? gunzipSync(content) : content).toString()) as RoadGraph;
const local = areas.filter(a => a.level === 'blockGroup' && a.county === county);
const locations = facilities.filter(f => f.county === county || f.baseline && graph.facilityNodes[f.id] != null);
const scenario = createDefaultScenario(graph, revision);
const result = runSimulation({graph,areas:local,facilities:locations,scenario,suggest:true});
const replay = runSimulation({graph,areas:local,facilities:locations,scenario:parseScenario(serializeScenario(result.scenario)),suggest:false});
if (JSON.stringify(result.areas) !== JSON.stringify(replay.areas)) throw new Error('Replayed access results differ');
if (result.proposed.covered < result.baseline.covered) throw new Error('Added sites reduced coverage');
const edge = result.flows.reduce((a,b)=>a.demand>b.demand?a:b,{edgeId:-1,demand:0});
const closed = edge.edgeId < 0 ? [] : [edge.edgeId];
if (edge.edgeId >= 0) {
  const [u,v] = graph.edges[edge.edgeId];
  graph.edges.forEach(([a,b],i)=>{if(a===v&&b===u)closed.push(i);});
}
const closure = runSimulation({graph,areas:local,facilities:locations,scenario:{...result.scenario,closedEdgeIds:closed},suggest:false});
if (closure.proposed.covered > result.proposed.covered) throw new Error('A closure increased fixed-site coverage');
if (closure.flows.some(f=>closed.includes(f.edgeId))) throw new Error('Flow traversed a closed road');
const report = {county,dataRevision:revision,graphRevision:graph.revision,nodes:graph.nodes.length,edges:graph.edges.length,elapsedMs:result.elapsedMs,baseline:result.baseline,proposed:result.proposed,suggested:result.suggested.map(s=>({...s,name:locations.find(f=>f.id===s.id)?.name})),closure:{edges:closed,proposed:closure.proposed},replay:'PASS'};
mkdirSync('artifacts/qualification',{recursive:true});
writeFileSync(`artifacts/qualification/graph-${county}.json`,JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify(report,null,2));
