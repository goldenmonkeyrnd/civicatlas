"""Pin current institutional office evidence and official facility geocoder matches."""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlencode

from pipeline.build import canonical_json
from pipeline.constants import ROOT
from pipeline.outreach_acquire import fetch,entry

SLUGS={'24001':'allegany-county','24003':'anne-arundel-county','24005':'baltimore-county','24009':'calvert-county','24011':'caroline-county','24013':'carroll-county','24015':'cecil-county','24017':'charles-county','24019':'dorchester-county','24021':'frederick-county','24023':'garrett-county','24025':'harford-county','24027':'howard-county','24029':'kent-county','24031':'montgomery-county','24033':'prince-georges-county','24035':'queen-annes-county','24037':'st-marys-county','24039':'somerset-county','24041':'talbot-county','24043':'washington-county','24045':'wicomico-county','24047':'worcester-county','24510':'baltimore-city'}


def geocode(address: str) -> dict[str,object]:
    url='https://geocoding.geo.census.gov/geocoder/locations/onelineaddress?'+urlencode({'address':address,'benchmark':'Public_AR_Current','format':'json'})
    result=json.loads(fetch(url))
    matches=result.get('result',{}).get('addressMatches',[])
    if not matches:return {'address':address,'url':url,'matches':[]}
    if len(matches)!=1:return {'address':address,'url':url,'reason':'Ambiguous official geocoder matches; no coordinate selected.','result':result}
    match=matches[0];point=match['coordinates']
    return {'address':address,'url':url,'matchedAddress':match['matchedAddress'],'lon':round(point['x'],7),'lat':round(point['y'],7),'result':result}


def supplement(raw: Path) -> None:
    from pipeline.outreach_build import text_only,norm
    manifest=json.loads((raw/'manifest.json').read_text());retrieved=datetime.now(timezone.utc).isoformat(timespec='seconds')
    if any(s['id']=='facility-coordinates' for s in manifest['sources']):raise ValueError('Facility verification already pinned')
    def office(pair: tuple[str,str]) -> dict[str,object]:
        county,slug=pair;url=f'https://dhs.maryland.gov/local-offices/{slug}/'
        payload=fetch(url)
        if 'Department of Human Services' not in payload.decode(errors='replace'):raise ValueError(f'Unexpected DHS page {url}')
        return entry(raw,f'dhs-office-{county}.html.gz',payload,f'dhs-office-{county}',url,retrieved)
    with ThreadPoolExecutor(max_workers=3) as pool:manifest['sources'].extend(pool.map(office,SLUGS.items()))
    directory=gzip.decompress((raw/'pratt-locations.html.gz').read_bytes()).decode();addresses={}
    for card in re.split(r'<div class="card" id="location\d+"',directory)[1:]:
        name=re.search(r'<h2>(.*?)</h2>',card,re.S);address=re.search(r'<div class="address">(.*?)</div>',card,re.S)
        if name and address:addresses[norm(text_only(name.group(1)).replace('Library','').replace('Branch','').strip())]=text_only(address.group(1))
    addresses['central']='400 Cathedral Street Baltimore, MD 21201'
    metadata={'dhs-new-mva':{'name':'Baltimore City DSS · MVA customer-service office','address':'5425 Reisterstown Road, Baltimore, MD 21215','county':'24510','evidenceId':'dhs-office-24510','url':'https://dhs.maryland.gov/local-offices/baltimore-city/'}}
    # Exact address appears on the current official county page and is absent from the older office map.
    addresses['dhs-new-mva']=metadata['dhs-new-mva']['address']
    with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(geocode,addresses.values()))
    coords={key:{**result,**metadata.get(key,{})} for key,result in zip(addresses,results,strict=True)}
    invalid=[key for key,result in coords.items() if 'lon' not in result]
    if invalid:print('Geocoder unresolved; retained for reconciliation:',','.join(invalid),flush=True)
    coords={key:value for key,value in coords.items() if 'lon' in value}
    payload=(canonical_json(coords)+'\n').encode();manifest['sources'].append(entry(raw,'facility-coordinates.json.gz',payload,'facility-coordinates','https://geocoding.geo.census.gov/geocoder/',retrieved,rows=len(coords)))
    manifest['sources'].sort(key=lambda e:e['id']);(raw/'manifest.json').write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n')
    print(f'Pinned 24 current DHS directories and {len(coords)} official geocoder matches.')


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--raw',type=Path,default=ROOT/'data/outreach/raw');args=parser.parse_args();supplement(args.raw)


if __name__=='__main__':main()
