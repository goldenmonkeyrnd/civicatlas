"""Pin current public library directories for statewide candidate verification."""
from __future__ import annotations
import argparse
import gzip
import hashlib
import html
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urljoin,urlsplit

from pipeline.build import canonical_json
from pipeline.constants import ROOT
from pipeline.outreach_acquire import fetch,entry
from pipeline.outreach_build import norm,text_only

DIRECTORIES={
 '24001':('Allegany County Library System','https://www.alleganycountylibrary.info/locations-and-hours/'),
 '24003':('Anne Arundel County Public Library','https://www.aacpl.net/locations'),
 '24005':('Baltimore County Public Library','https://www.bcpl.info/locations'),
 '24009':('Calvert Library','https://calvertlibrary.info/hours/'),
 '24011':('Caroline County Public Library','https://www.carolib.org/hours-and-locations/'),
 '24013':('Carroll County Public Library','https://library.carr.org/about/locations'),
 '24015':('Cecil County Public Library','https://www.cecilcountylibrary.org/about/hours-locations'),
 '24017':('Charles County Public Library','https://ccplonline.org/about/library-locations/'),
 '24019':('Dorchester County Public Library','https://www.dorchesterlibrary.org/visit-our-libraries'),
 '24021':('Frederick County Public Libraries','https://www.fcpl.org/branches-hours'),
 '24023':('Ruth Enlow Library','https://www.relib.net/locations'),
 '24025':('Harford County Public Library','https://www.hcplonline.org/locations.php'),
 '24027':('Howard County Library System','https://www.hclibrary.org/locations/'),
 '24029':('Kent County Public Library','https://kentcountylibrary.org/about-kcpl/operating-info-and-accessibility-features/'),
 '24031':('Montgomery County Public Libraries','https://www.montgomerycountymd.gov/montgomery-county-public-libraries/library-locations'),
 '24033':("Prince George's County Memorial Library System",'https://www.pgcmls.info/map-of-branches'),
 '24035':("Queen Anne's County Library",'https://www.qaclibrary.org/locations'),
 '24037':("St. Mary's County Library",'https://www.stmalib.org/about-us/locations-and-hours/'),
 '24039':('Somerset County Library','https://www.somelibrary.org/'),
 '24041':('Talbot County Free Library','https://www.tcfl.org/visit/?content=locations'),
 '24043':('Washington County Free Library','https://www.washcolibrary.org/using-the-library/locations-hours'),
 '24045':('Wicomico Public Libraries','https://www.wicomicolibrary.org/hours-locations'),
 '24047':('Worcester County Library','https://worcesterlibrary.org/branches/'),
}


def acquire_directory(pair: tuple[str,tuple[str,str]], raw: Path, retrieved: str, inventory: list[dict[str,object]]) -> dict[str,object]:
    county,(publisher,url)=pair;payload=fetch(url);document=payload.decode('utf-8',errors='replace');docs=[{'url':url,'sha256':hashlib.sha256(payload).hexdigest(),'html':document}]
    names=[str(f['properties']['ANCHORNAME']) for f in inventory if str(f['properties']['County']).lower().replace(' county','') in publisher.lower()]
    # Follow current directory branch links within its official host. Bounded to 30 linked pages per jurisdiction.
    links=[]
    for href,label in re.findall(r'<a[^>]*href=[\"\']([^\"\']+)[\"\'][^>]*>(.*?)</a>',document,re.S):
        target=urljoin(url,html.unescape(href));parsed=urlsplit(target)
        if parsed.netloc.removeprefix('www.')!=urlsplit(url).netloc.removeprefix('www.') or target.split('#')[0].rstrip('/')==url.rstrip('/'):continue
        label=text_only(label)
        if re.search(r'(branch|libraries|library-locations|hours-locations|/locations/|/branches/|/location/)',parsed.path,re.I) or (label and any(norm(label)==norm(n.replace('Library','').replace('Branch','').strip()) for n in names)):
            if not parsed.query and not parsed.fragment and not re.search(r'\.(pdf|jpg|png|css|js)$',parsed.path,re.I):links.append(target)
    for target in sorted(set(links))[:30]:
        try:
            body=fetch(target)
            docs.append({'url':target,'sha256':hashlib.sha256(body).hexdigest(),'html':body.decode('utf-8',errors='replace')})
        except OSError as error:
            docs.append({'url':target,'error':str(error)})
    payload=(canonical_json(docs)+'\n').encode()
    print(county,publisher,len(docs),'directory pages pinned',flush=True)
    return entry(raw,f'library-directory-{county}.json.gz',payload,f'library-directory-{county}',url,retrieved,publisher=publisher,documents=len(docs))


def acquire(raw: Path) -> None:
    manifest=json.loads((raw/'manifest.json').read_text())
    if any(str(e['id']).startswith('library-directory-') for e in manifest['sources']):raise ValueError('Statewide directories already pinned')
    inventory=json.loads(gzip.decompress((raw/'libraries.geojson.gz').read_bytes()))['features'];retrieved=datetime.now(timezone.utc).isoformat(timespec='seconds')
    with ThreadPoolExecutor(max_workers=5) as pool:sources=list(pool.map(lambda pair:acquire_directory(pair,raw,retrieved,inventory),DIRECTORIES.items()))
    manifest['sources'].extend(sources);manifest['sources'].sort(key=lambda e:e['id']);(raw/'manifest.json').write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n')
    print('Pinned current directories from all 23 county library systems; Pratt is pinned separately.')


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--raw',type=Path,default=ROOT/'data/outreach/raw');args=parser.parse_args();acquire(args.raw)


if __name__=='__main__':main()
