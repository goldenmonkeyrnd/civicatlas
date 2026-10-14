"""Pin proven current-directory corrections and official address geocoder evidence."""
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

# Each correction was reconciled with a specific current directory page, not inferred from the old point.
CORRECTIONS=(
 ('library-4','24003','Busch Annapolis Library','1410 West Street, Annapolis, MD 21401','https://www.aacpl.net/locations','1410 West Street'),
 ('library-13','24003','Mountain Road Library','4115 Mountain Road, Pasadena, MD 21122','https://www.aacpl.net/locations','4115 Mountain Road'),
 ('library-16','24003','Severna Park Library','45 West McKinsey Road, Severna Park, MD 21146','https://www.aacpl.net/locations','45 West McKinsey Road'),
 ('library-56','24003','Discoveries: The Library at the Mall','2550 Annapolis Mall Road, Annapolis, MD 21401','https://www.aacpl.net/locations','2550 Annapolis Mall Road'),
 ('library-60','24003','Broadneck Library','1275 Green Holly Drive, Annapolis, MD 21409','https://www.aacpl.net/locations','1275 Green Holly Drive'),
 ('library-55','24005','Woodlawn Library · temporary location','6660 Security Boulevard, Suite 13, Woodlawn, MD 21207','https://www.bcpl.info/locations/woodlawn','6660 Security Boulevard'),
 ('library-61','24009','Twin Beaches Library','4100 5th Street, North Beach, MD 20714','https://calvertlibrary.info/about-us/locations/new-twin-beaches-branch/','4100 5th St'),
 ('library-134','24011','Greensboro Library','101 Cedar Lane, Greensboro, MD 21639','https://www.carolib.org/hours-and-locations/','101 Cedar Lane'),
 ('library-71','24015','Elkton Temporary Branch','228 South Bridge Street, Elkton, MD 21921','https://www.cecilcountylibrary.org/about/hours-locations/elkton','228 S Bridge Street'),
 ('library-75','24015','North East Branch','485 Mauldin Avenue, North East, MD 21901','https://www.cecilcountylibrary.org/about/hours-locations/north-east','485 Mauldin Avenue'),
 ('library-87','24021','Middletown Branch','31 East Green Street, Middletown, MD 21769','https://www.fcpl.org/branches-hours/middletown-branch-library','31 East Green Street'),
 ('library-124','24023','Grantsville Branch','102 Parkview Drive, Grantsville, MD 21536','https://www.relib.net/locations','102 Parkview Drive'),
 ('library-93','24023','Oakland Branch','6 North Second Street, Oakland, MD 21550','https://www.relib.net/locations','6 North Second Street'),
 ('library-100','24025','Darlington Branch','3535 Conowingo Road, Street, MD 21154','https://hcplonline.org/darlington.php','3535 Conowingo Road'),
 ('library-116','24027','Glenwood Branch','2350 MD-97, Cooksville, MD 21723','https://www.hclibrary.org/locations/glenwood-branch/','2350 MD-97'),
 ('library-117','24029','Rock Hall Branch','21258 Rock Hall Avenue, Rock Hall, MD 21661','https://kentcountylibrary.org/about-kcpl/operating-info-and-accessibility-features/','21258 Rock Hall Ave'),
 ('library-125','24029','North County Branch','119 North Main Street, Galena, MD 21635','https://kentcountylibrary.org/about-kcpl/operating-info-and-accessibility-features/','119 N. Main Street'),
 ('library-149','24031','Wheaton Library','11701 Georgia Avenue, Silver Spring, MD 20902','https://www.montgomerycountymd.gov/montgomery-county-public-libraries/library-locations/wheaton-library','11701 Georgia Ave'),
 ('library-150','24033','Baden Branch','13601 Baden Westwood Road, Brandywine, MD 20613','https://www.pgcmls.info/branch/location/Baden','13601 Baden Westwood Rd'),
 ('library-158','24033','Hyattsville Branch','6530 Adelphi Road, Hyattsville, MD 20782','https://www.pgcmls.info/branch/location/Hyattsville','6530 Adelphi Road'),
 ('library-164','24033','Laurel Branch','507 7th Street, Laurel, MD 20707','https://www.pgcmls.info/branch/location/Laurel','507 7th Street'),
 ('library-171','24033','Accokeek Branch','15773 Livingston Road, Accokeek, MD 20607','https://www.pgcmls.info/branch/location/Accokeek','15773 Livingston Rd'),
 ('library-183','24035','Centreville Branch','121 Commerce Street, Centreville, MD 21617','https://www.qaclibrary.org/locations','121 Commerce Street'),
 ('library-170','24037','Leonardtown Branch','23630 Hayden Farm Lane, Leonardtown, MD 20650','https://www.stmalib.org/about-us/locations-and-hours/','23630 Hayden Farm Lane'),
 ('library-177','24039','Ewell Library','4005 Smith Island Road, Ewell, MD 21824','https://www.somelibrary.org/','4005 Smith Island Rd'),
 ('library-197','24041','St. Michaels Branch','106 Fremont Street, St. Michaels, MD 21663','https://www.tcfl.org/visit/?content=locations','106 Fremont St'),
 ('library-174','24043','Hancock War Memorial Library','231 Hancock Veterans Parkway, Hancock, MD 21750','https://www.washcolibrary.org/using-the-library/locations-hours',"231 Hancock Veteran's Pkwy"),
 ('library-184','24043','Smithsburg Library','95 Officer Nicholson Way, Smithsburg, MD 21783','https://www.washcolibrary.org/using-the-library/locations-hours','95 Officer Nicholson Way'),
 ('library-187','24047','Berlin Branch','13 Harrison Avenue, Berlin, MD 21811','https://worcesterlibrary.org/branches/berlin','13 Harrison Avenue'),
 ('library-193','24047','Pocomoke Library · temporary location','401 5th Street, Unit 100, Pocomoke City, MD 21851','https://worcesterlibrary.org/branches/pocomoke','401 5th Street'),
)

# Coordinate links embedded by the branch publishers resolve documented geocoder ambiguity.
PUBLISHER_POINTS={'library-75':(-75.948281,39.606383),'library-150':(-76.771561,38.660485),'library-197':(-76.2259722,38.7860005)}


def acquire(raw: Path) -> None:
    manifest=json.loads((raw/'manifest.json').read_text())
    if any(e['id']=='library-corrections' for e in manifest['sources']):raise ValueError('Library corrections already pinned')
    for identifier,county,name,address,url,proof in CORRECTIONS:
        docs=json.loads(gzip.decompress((raw/f'library-directory-{county}.json.gz').read_bytes()))
        doc=next((d for d in docs if d['url']==url),None)
        if doc is None or norm(proof) not in norm(text_only(doc.get('html',''))):raise ValueError(f'Unproven correction {identifier}: {url}')
    with ThreadPoolExecutor(max_workers=3) as pool:points=list(pool.map(geocode,[item[3] for item in CORRECTIONS]))
    rows=[]
    for (identifier,county,name,address,url,proof),point in zip(CORRECTIONS,points,strict=True):
        if 'lon' not in point and identifier in PUBLISHER_POINTS:
            lon,lat=PUBLISHER_POINTS[identifier]
            docs=json.loads(gzip.decompress((raw/f'library-directory-{county}.json.gz').read_bytes()))
            doc=next(d for d in docs if d['url']==url)
            if str(lon) not in doc['html'] or str(lat) not in doc['html']:raise ValueError('Publisher coordinate evidence drifted')
            point={**point,'lon':lon,'lat':lat,'coordinateMethod':'branch publisher map coordinates','coordinateUrl':url}
        rows.append({'id':identifier,'county':county,'name':name,'address':address,'verificationUrl':url,'sourceId':f'library-directory-{county}','proof':proof,'point':point})
        print(identifier,name,'geocoder matched' if 'lon' in point else 'geocoder unresolved',flush=True)
    retrieved=datetime.now(timezone.utc).isoformat(timespec='seconds');payload=(canonical_json(rows)+'\n').encode()
    manifest['sources'].append(entry(raw,'library-corrections.json.gz',payload,'library-corrections','https://geocoding.geo.census.gov/geocoder/',retrieved,rows=len(rows)))
    manifest['sources'].sort(key=lambda e:e['id']);(raw/'manifest.json').write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n')


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--raw',type=Path,default=ROOT/'data/outreach/raw');args=parser.parse_args();acquire(args.raw)


if __name__=='__main__':main()
