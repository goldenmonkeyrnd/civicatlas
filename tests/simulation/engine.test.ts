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

describe('route alternatives and known zero observations',()=>{
  test('a closed shortest road selects a real detour and reports beyond rather than disconnected',()=>{
    const detourGraph={...graph,edges:[...graph.edges,[0,4,2] as [number,number,number]]};
    const result=runSimulation({graph:detourGraph,areas,facilities,scenario:scenario({radiusMiles:3,closedEdgeIds:[0]}),suggest:false});
    const origin=result.areas.find(a=>a.geoid==='a')!;
    expect(origin.proposedMiles).toBe(4);
    expect(origin.state).toBe('beyond');
    expect(result.proposed.covered).toBe(90);
    expect(result.flows).toEqual([{edgeId:1,demand:90},{edgeId:3,demand:40}]);
  });
  test('equal distance chooses the same named hub regardless of input order',()=>{
    const tieGraph={...graph,facilityNodes:{...graph.facilityNodes,beta:2}};
    const input={graph:tieGraph,areas,facilities,scenario:scenario({hubIds:['beta'],radiusMiles:3}),suggest:false};
    const first=runSimulation(input);
    const second=runSimulation({...input,facilities:[...facilities].reverse(),areas:[...areas].reverse()});
    expect(first.areas).toEqual(second.areas);
    expect(first.areas.find(a=>a.geoid==='a')?.hubId).toBe('baseline');
  });
  test('observed zero remains available and is distinct from missing and suppressed',()=>{
    const zero=area('a',0), suppressed=area('b',50);
    suppressed.values.households.status='suppressed';
    const result=runSimulation({graph,areas:[zero,suppressed],facilities,scenario:scenario({radiusMiles:3}),suggest:false});
    expect(result.areas[0].demand).toBe(0);
    expect(result.areas[0].state).toBe('covered');
    expect(result.areas[1].demand).toBeNull();
    expect(result.areas[1].state).toBe('missing');
    expect(result.proposed).toEqual({total:0,covered:0,beyond:0,disconnected:0,unmatched:0,missingAreas:1});
    expect(result.flows).toEqual([]);
  });
  test('every directed closure subset on the fixture preserves coverage monotonicity',()=>{
    const original=run({radiusMiles:3});
    for(let mask=0;mask<(1<<graph.edges.length);mask++){
      const closedEdgeIds=graph.edges.map((_,id)=>id).filter(id=>mask&(1<<id));
      const result=run({radiusMiles:3,closedEdgeIds});
      expect(result.proposed.covered).toBeLessThanOrEqual(original.proposed.covered);
      const extended=run({radiusMiles:3,closedEdgeIds,hubIds:['alpha','beta']});
      expect(extended.proposed.covered).toBeGreaterThanOrEqual(result.proposed.covered);
      expect(extended.proposed.total).toBe(original.proposed.total);
      expect(result.flows.every(flow=>!closedEdgeIds.includes(flow.edgeId))).toBe(true);
    }
  });
});


describe('geographic baseline across administrative boundaries',()=>{
  test('nearby verified out-of-county assistance counts, while candidates stay in the study county',()=>{
    const outside={...facility('outside',true),county:'24005'};
    const outsideCandidate={...facility('outside-candidate'),county:'24005'};
    const localGraph={...graph,facilityNodes:{...graph.facilityNodes,outside:0,'outside-candidate':4}};
    const allFacilities=[...facilities,outside,outsideCandidate];
    const input={graph:localGraph,areas,facilities:allFacilities,scenario:scenario(),suggest:true};
    const result=runSimulation(input);
    expect(result.baseline.covered).toBe(150);
    expect(result.areas.find(area=>area.geoid==='a')?.baselineMiles).toBe(0);
    expect(result.suggested).toEqual([{id:'alpha',incremental:40}]);
    expect(result.proposed.covered).toBe(190);
    expect(()=>runSimulation({...input,scenario:scenario({hubIds:['outside-candidate']})})).toThrow('verified candidate');
  });
  test('closed, unresolved and unmapped out-of-county sites do not create baseline access',()=>{
    const external=[{...facility('outside',true),county:'24005',status:'closed' as const},{...facility('unresolved',true),county:'24005',status:'unresolved' as const},{...facility('unmapped',true),county:'24005'}];
    const localGraph={...graph,facilityNodes:{...graph.facilityNodes,outside:0,unresolved:0,unmapped:null}};
    const result=runSimulation({graph:localGraph,areas,facilities:[...facilities,...external],scenario:scenario(),suggest:false});
    expect(result.baseline.covered).toBe(50);
  });
});
