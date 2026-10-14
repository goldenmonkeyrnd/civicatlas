"""Verify current DSS public service addresses independently of obsolete map classes."""
from __future__ import annotations
import argparse
import gzip
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from pathlib import Path
from pipeline.build import canonical_json
from pipeline.constants import ROOT
from pipeline.outreach_acquire import entry
from pipeline.outreach_build import norm,text_only
from pipeline.outreach_verify import geocode

OFFICES=(
 ('24001','Allegany County DSS','1 Frederick Street, Cumberland, MD 21502','1 Frederick Street'),
 ('24009','Calvert County DSS','200 Duke Street, Prince Frederick, MD 20678','200 Duke Street'),
 ('24011','Caroline County DSS','207 South Third Street, Denton, MD 21629','207 South Third Street'),
 ('24013','Carroll County DSS','1232 Tech Court, Westminster, MD 21157','1232 Tech Court'),
 ('24015','Cecil County DSS','170 East Main Street, Elkton, MD 21921','170 East Main Street'),
 ('24017','Charles County DSS','200 Kent Avenue, La Plata, MD 20646','200 Kent Avenue'),
 ('24019','Dorchester County DSS','2737 Dorchester Square, Cambridge, MD 21613','2737 Dorchester Square'),
 ('24021','Frederick County DSS','1888 North Market Street, Frederick, MD 21701','1888 North Market Street'),
 ('24023','Garrett County DSS','12578 Garrett Highway, Oakland, MD 21550','12578 Garrett Highway'),
 ('24027','Howard County DSS · Family Investment','9780 Patuxent Woods Drive, Columbia, MD 21046','9780 Patuxent Woods Drive'),
 ('24031','Montgomery DHHS · Rockville public service office','1401 Rockville Pike, Rockville, MD 20852','1401 Rockville Pike'),
 ('24031','Montgomery DHHS · Mid-County public service office','1301 Piccard Drive, Rockville, MD 20850','1301 Piccard Drive'),
 ('24031','Montgomery DHHS · Upcounty public service office','12900 Middlebrook Road, Germantown, MD 20874','12900 Middlebrook Rd.'),
 ('24035',"Queen Anne's County DSS",'125 Comet Drive, Centreville, MD 21617','125 Comet Drive'),
 ('24037',"St. Mary's County DSS · Leonardtown",'23110 Leonard Hall Drive, Leonardtown, MD 20650','23110 Leonard Hall Dr'),
 ('24037',"St. Mary's County DSS · Great Mills",'20331 Point Lookout Road, Great Mills, MD 20634','20331 Point Lookout Road'),
 ('24039','Somerset County DSS','30397 Mt. Vernon Road, Princess Anne, MD 21853','30397 Mt. Vernon Road'),
 ('24041','Talbot County DSS','301 Bay Street, Easton, MD 21601','301 Bay Street'),
 ('24043','Washington County DSS','122 North Potomac Street, Hagerstown, MD 21740','122 North Potomac Street'),
)


def acquire(raw: Path) -> None:
    manifest=json.loads((raw/'manifest.json').read_text())
    if any(s['id']=='dhs-current-coordinates' for s in manifest['sources']):raise ValueError('Current DSS office coordinate evidence already pinned')
    for county,name,address,proof in OFFICES:
        document=text_only(gzip.decompress((raw/f'dhs-office-{county}.html.gz').read_bytes()).decode())
        if norm(proof) not in norm(document):raise ValueError(f'Current DSS office address unproven: {name}')
    with ThreadPoolExecutor(max_workers=3) as pool:points=list(pool.map(geocode,[item[2] for item in OFFICES]))
    rows=[]
    for index,((county,name,address,proof),point) in enumerate(zip(OFFICES,points,strict=True)):
        rows.append({'id':f'dhs-current-{county}-{index}','county':county,'name':name,'address':address,'sourceId':f'dhs-office-{county}','proof':proof,'point':point})
        print(county,name,'geocoder matched' if 'lon' in point else 'unresolved',flush=True)
    payload=(canonical_json(rows)+'\n').encode();retrieved=datetime.now(timezone.utc).isoformat(timespec='seconds')
    manifest['sources'].append(entry(raw,'dhs-current-coordinates.json.gz',payload,'dhs-current-coordinates','https://geocoding.geo.census.gov/geocoder/',retrieved,rows=len(rows)))
    manifest['sources'].sort(key=lambda s:s['id']);(raw/'manifest.json').write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n')


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--raw',type=Path,default=ROOT/'data/outreach/raw');args=parser.parse_args();acquire(args.raw)


if __name__=='__main__':main()
