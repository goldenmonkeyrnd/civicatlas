import { describe, expect, test } from 'bun:test';
import type { Area, Facility, RoadGraph, Scenario } from '../../src/outreach';
import { createDefaultScenario, decodeScenario, encodeScenario, parseScenario, runSimulation, serializeScenario, validateScenario } from '../../src/simulation';

const graph: RoadGraph = {
  version: 1, revision: 'roads-1', dataRevision: 'data-1', county: '24510',
  nodes: [[-76.61,39.29],[-76.60,39.29],[-76.59,39.29],[-76.58,39.29],[-76.60,39.30]],
  edges: [[0,1,1],[1,2,1],[2,3,1],[4,1,1]],
  shapes: [], names: [], areaNodes: { a:0, b:1, c:4, isolated:3, unmatched:null },
  facilityNodes: { baseline:2, alpha:1, beta:4, closed:3 }, sourceId: 'osm', limitations: [],
};
function area(geoid:string, demand:number|null):Area {
  return { geoid, level:'blockGroup', county:'24510', tract:'24510000100', name:geoid, lon:-76.60, lat:39.29,
    values:{ households:{ value:demand, moe:null, numerator:null, denominator:null, status:demand===null?'missing':'available',sourceId:'acs' },
      noInternet:{ value:demand===null?null:demand/2,moe:4,numerator:9999,denominator:demand,status:demand===null?'missing':'available',sourceId:'acs' } },raw:{} };
}
function facility(id:string, baseline=false):Facility {
  return { id,name:id,kind:baseline?'benefits':'library',county:'24510',address:'Sample',lon:-76.60,lat:39.29,
    status:id==='closed'?'closed':'verified',baseline,candidate:!baseline,sourceId:'dhs',verificationUrl:'https://example.gov',checkedAt:'2026-10-14',note:'' };
}
const areas = [area('a',100),area('b',50),area('c',40),area('isolated',10),area('unmatched',20),area('missing',null)];
const facilities = [facility('baseline',true),facility('alpha'),facility('beta'),facility('closed')];
function scenario(updates:Partial<Scenario>={}):Scenario { return { ...createDefaultScenario(graph),radiusMiles:1,additions:1,...updates }; }
function run(updates:Partial<Scenario>={},suggest=false){return runSimulation({graph,areas,facilities,scenario:scenario(updates),suggest});}

describe('directed road coverage',()=>{
  test('travels from household to assistance site and distinguishes unavailable states',()=>{
    const result = run();
    expect(result.baseline).toEqual({total:220,covered:50,beyond:140,disconnected:10,unmatched:20,missingAreas:1});
    expect(result.areas.find(a=>a.geoid==='a')?.baselineMiles).toBe(2);
    expect(result.areas.find(a=>a.geoid==='isolated')?.state).toBe('disconnected');
    expect(result.areas.find(a=>a.geoid==='unmatched')?.state).toBe('unmatched');
    expect(result.areas.find(a=>a.geoid==='missing')?.state).toBe('missing');
  });
  test('adding sites cannot reduce coverage and counts each household once',()=>{
    const before=run(),after=run({hubIds:['alpha','beta']});
    expect(after.proposed.covered).toBe(190);
    expect(after.proposed.total).toBe(before.proposed.total);
    expect(after.proposed.covered).toBeGreaterThanOrEqual(before.proposed.covered);
  });
  test('closures only remove graph connectivity and reopening restores coverage',()=>{
    const before=run({radiusMiles:3}),closed=run({radiusMiles:3,closedEdgeIds:[1]});
    expect(before.proposed.covered).toBe(190);
    expect(closed.proposed.covered).toBe(0);
    expect(closed.proposed.disconnected).toBe(200);
    expect(run({radiusMiles:3}).proposed).toEqual(before.proposed);
  });
  test('geometric crossing creates no new junction',()=>{
    const crossing={...graph,nodes:[[-76.61,39.29],[-76.59,39.31],[-76.61,39.31],[-76.59,39.29]] as [number,number][],edges:[[0,1,1],[2,3,1]] as [number,number,number][],areaNodes:{a:0},facilityNodes:{baseline:3}};
    const result=runSimulation({graph:crossing,areas:[area('a',100)],facilities:[facility('baseline',true)],scenario:scenario(),suggest:false});
    expect(result.proposed.disconnected).toBe(100);
  });
  test('flow follows directed shortest paths with source demand conserved',()=>{
    const result=run({radiusMiles:3});
    expect(result.flows).toEqual([{edgeId:0,demand:100},{edgeId:1,demand:190},{edgeId:3,demand:40}]);
    expect(result.flows.some(flow=>flow.edgeId===2)).toBe(false);
  });
  test('demand uses household count values with independent numerator metadata',()=>{
    const result=run({measure:'noInternet',radiusMiles:3});
    expect(result.proposed.total).toBe(110);
    expect(result.proposed.covered).toBe(95);
  });
});

describe('deterministic suggestions and scenario contracts',()=>{
  test('greedy selection chooses greatest additional demand with stable ties',()=>{
    const result=run({},true);
    expect(result.suggested).toEqual([{id:'alpha',incremental:140}]);
    expect(result.scenario.hubIds).toEqual(['alpha']);
    expect(run({},true).areas).toEqual(result.areas);
    const tieGraph={...graph,facilityNodes:{...graph.facilityNodes,beta:1}};
    const tied=runSimulation({graph:tieGraph,areas,facilities:[...facilities].reverse(),scenario:scenario(),suggest:true});
    expect(tied.suggested[0].id).toBe('alpha');
  });
  test('retains manual choices and never suggests baseline or closed sites',()=>{
    const result=run({hubIds:['beta'],additions:2},true);
    expect(result.scenario.hubIds).toEqual(['alpha','beta']);
    expect(result.suggested).toEqual([{id:'alpha',incremental:100}]);
  });
  test('round trips explicit revisions, closures, and scenario selection',()=>{
    const input=scenario({hubIds:['beta','alpha'],closedEdgeIds:[3,0]});
    const normalized=validateScenario(input);
    expect(parseScenario(serializeScenario(input))).toEqual(normalized);
    expect(decodeScenario(encodeScenario(input))).toEqual(normalized);
  });
  test('rejects stale revisions and malformed values with specific errors',()=>{
    expect(()=>run({graphRevision:'old'})).toThrow('road release');
    expect(()=>run({dataRevision:'old'})).toThrow('data release');
    expect(()=>run({radiusMiles:Infinity})).toThrow('between 1 and 10');
    expect(()=>run({closedEdgeIds:[99]})).toThrow('99');
    expect(()=>run({hubIds:['closed']})).toThrow('verified candidate');
    expect(()=>run({measure:'income'})).toThrow('household demand measure');
    expect(()=>parseScenario('{bad')).toThrow('valid JSON');
    expect(()=>validateScenario({...scenario(),surprise:true})).toThrow('Unknown scenario field');
  });
  test('hypothetical site snaps only to real nodes within 500 metres',()=>{
    const result=run({hypotheticalSites:[{id:'hypothetical-a',name:'Pop-up',lon:-76.61,lat:39.29}]});
    expect(result.proposed.covered).toBe(150);
    expect(()=>run({hypotheticalSites:[{id:'hypothetical-far',name:'Far away',lon:-75,lat:38}]})).toThrow('500');
  });
});
