"""Explicit acquisition of bounded Maryland inputs; routine builds stay offline."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from pipeline.constants import FIPS, ROOT
from pipeline.build import canonical_json

BULK = 'https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/data/5YRData/'
LIBRARY_URL = 'https://mdgeodata.md.gov/imap/rest/services/Education/MD_Libraries/FeatureServer/0'
DHS_URL = 'https://www.arcgis.com/sharing/rest/content/items/677d0b906a6e48f0b611ca71964e9faf/data?f=json'
TABLES = ('B01003', 'B11001', 'B11002', 'B19001', 'B19051', 'B23025', 'C17002', 'B25070', 'B25044', 'B28002')
LOCAL = {'B01003': 'national_reference_2024/acsdt5y2024-b01003.dat', 'B11001': 'national_reference_2024/acsdt5y2024-b11001.dat', 'B23025': 'national_reference_2024/acsdt5y2024-b23025.dat', **{t:f'national_reference_2024/households_2026-10-02/acsdt5y2024-{t.lower()}.dat' for t in ('B11002','B19001','B19051')}}


def fetch(url: str) -> bytes:
    """Read one official bounded document with retries and meaningful errors."""
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={'User-Agent':'CivicAtlas/1.0 public-data demonstration'}), timeout=120) as response:
                payload = response.read(250 * 1024 * 1024)
                if response.status != 200 or len(payload) >= 250*1024*1024:
                    raise ValueError(f'Unexpected status/size at {url}')
                return payload
        except OSError:
            if attempt == 2:
                raise
            time.sleep(2**attempt)
    raise RuntimeError(f'Could not fetch {url}')


def entry(raw: Path, filename: str, payload: bytes, source_id: str, url: str, retrieved: str, **extra: Any) -> dict[str, Any]:
    stored = gzip.compress(payload, mtime=0) if filename.endswith('.gz') else payload
    (raw / filename).write_bytes(stored)
    return {'id':source_id,'file':filename,'url':url,'retrievedAt':retrieved,'sha256':hashlib.sha256(payload).hexdigest(),'storedSha256':hashlib.sha256(stored).hexdigest(), **extra}


def extract_acs(table: str, raw: Path, library: Path, retrieved: str) -> dict[str, Any]:
    local = library / LOCAL[table] if table in LOCAL else None
    payload = local.read_bytes() if local else fetch(BULK + f'acsdt5y2024-{table.lower()}.dat')
    lines = payload.splitlines(keepends=True)
    prefixes = (b'0500000US24', b'1400000US24', b'1500000US24')
    subset = b''.join([lines[0], *(line for line in lines[1:] if line.startswith(prefixes))])
    if len(subset.splitlines()) != 5579:
        raise ValueError(f'{table} expected 24 counties + 1475 tracts + 4079 block groups, got {len(subset.splitlines())-1}')
    record = entry(raw, f'acs-{table}.dat.gz', subset, f'acs-2024-{table}', BULK+f'acsdt5y2024-{table.lower()}.dat', retrieved, originalSha256=hashlib.sha256(payload).hexdigest(), origin='read-only local library' if local else 'official bulk download', rows=5578)
    if local:
        record['originalRetrievedAt'] = None
        record['retrievalNote'] = 'retrievedAt is the Maryland extraction date; original local download timestamp was not universally recorded.'
    return record


def acquire_history(raw: Path, library: Path, retrieved: str) -> dict[str, Any]:
    import duckdb
    con = duckdb.connect(':memory:')
    con.execute("SET threads=2")
    records = []
    originals = []
    for year in range(2010,2025):
        path = library / f'qcew/{year}.annual.singlefile.csv'
        digest = hashlib.file_digest(path.open('rb'),'sha256').hexdigest()
        rows = con.execute("SELECT * FROM read_csv(?,all_varchar=true,nullstr='__CIVICATLAS_NEVER_NULL__') WHERE area_fips IN (SELECT unnest(?)) AND own_code='5' AND industry_code='10' AND agglvl_code='71' AND size_code='0'",[str(path),list(FIPS)]).fetchall()
        names = [c[0] for c in con.description]
        if len(rows)!=24:
            raise ValueError(f'QCEW {year} expected 24 totals, got {len(rows)}')
        records.extend(dict(zip(names,row,strict=True)) for row in rows)
        originals.append({'year':year,'sha256':digest,'url':f'https://data.bls.gov/cew/data/files/{year}/csv/{year}_annual_singlefile.zip'})
        print(f'QCEW {year}: 24 county totals',flush=True)
    con.close()
    payload=(canonical_json(records)+'\n').encode()
    return entry(raw,'qcew-history.json.gz',payload,'qcew-history','https://www.bls.gov/cew/downloadable-data-files.htm',retrieved,originals=originals,rows=len(records))


def acquire_commuting(raw: Path, library: Path, retrieved: str) -> list[dict[str, Any]]:
    import duckdb
    con=duckdb.connect(':memory:');con.execute('SET threads=2')
    od=library/'lodes/od/md_od_main_JT00_2021.csv.gz'
    xwalk=library/'lodes/us_xwalk.csv.gz'
    con.execute("CREATE TABLE flows AS SELECT substr(h_geocode,1,12) AS home,substr(w_geocode,1,12) AS work,sum(CAST(S000 AS BIGINT)) AS jobs FROM read_csv(?,all_varchar=true) GROUP BY ALL",[str(od)])
    cursor=con.execute('SELECT home,work,jobs FROM flows ORDER BY home,work')
    flows=[dict(zip(('home','work','jobs'),row,strict=True)) for row in cursor.fetchall()]
    con.execute("CREATE TABLE coords AS SELECT bgrp AS geoid,avg(CAST(blklondd AS DOUBLE)) AS lon,avg(CAST(blklatdd AS DOUBLE)) AS lat FROM read_csv(?,all_varchar=true) WHERE st='24' GROUP BY bgrp",[str(xwalk)])
    coords=[dict(zip(('geoid','lon','lat'),row,strict=True)) for row in con.execute('SELECT * FROM coords ORDER BY geoid').fetchall()]
    con.close()
    return [entry(raw,'lodes-flows.json.gz',(canonical_json(flows)+'\n').encode(),'lodes-2021','https://lehd.ces.census.gov/data/lodes/LODES8/md/od/md_od_main_JT00_2021.csv.gz',retrieved,originalSha256=hashlib.file_digest(od.open('rb'),'sha256').hexdigest(),rows=len(flows)),entry(raw,'lodes-coordinates.json.gz',(canonical_json(coords)+'\n').encode(),'lodes-xwalk','https://lehd.ces.census.gov/data/lodes/LODES8/md/md_xwalk.csv.gz',retrieved,originalSha256=hashlib.file_digest(xwalk.open('rb'),'sha256').hexdigest(),originalUrl='https://lehd.ces.census.gov/data/lodes/LODES8/us_xwalk.csv.gz',rows=len(coords))]


def acquire(raw: Path, library: Path) -> None:
    if (raw/'manifest.json').exists():
        raise ValueError('Pinned acquisition already exists; deliberately remove or choose a separate raw directory for a new release.')
    raw.mkdir(parents=True,exist_ok=True)
    retrieved=datetime.now(timezone.utc).isoformat(timespec='seconds')
    with ThreadPoolExecutor(max_workers=3) as pool:
        sources=list(pool.map(lambda t:extract_acs(t,raw,library,retrieved), TABLES))
    for level in ('bg','tract'):
        url=f'https://www2.census.gov/geo/tiger/TIGER2024/{level.upper()}/tl_2024_24_{level}.zip'
        sources.append(entry(raw,f'tl_2024_24_{level}.zip',fetch(url),f'census-geometry-{level}',url,retrieved))
    shell=library/'national_reference_2024/ACS20245YR_Table_Shells.txt'
    rows=list(csv.DictReader(io.StringIO(shell.read_text()),delimiter='|'))
    # Preserve the local shell itself: bounded declaration of variables used for each chosen table.
    text=shell.read_text(); header=text.splitlines()[0]
    selected='\n'.join([header,*[line for line in text.splitlines()[1:] if line.split('|',1)[0] in TABLES]])+'\n'
    sources.append(entry(raw,'acs-table-shells.txt.gz',selected.encode(),'acs-table-shells','https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/documentation/ACS20245YR_Table_Shells.txt',retrieved,originalSha256=hashlib.sha256(shell.read_bytes()).hexdigest()))
    query=LIBRARY_URL+'/query?where=1%3D1&outFields=*&outSR=4326&f=geojson'
    libraries=fetch(query);obj=json.loads(libraries)
    if obj.get('error') or obj.get('exceededTransferLimit') or len(obj.get('features',[]))!=198:
        raise ValueError(f'Unexpected library coverage: {len(obj.get("features",[]))}')
    sources.append(entry(raw,'libraries.geojson.gz',libraries,'maryland-libraries',query,retrieved,rows=198))
    sources.append(entry(raw,'dhs-webmap.json.gz',fetch(DHS_URL),'maryland-dhs',DHS_URL,retrieved))
    for filename,url in [('pratt-locations.html.gz','https://www.prattlibrary.org/locations'),('pratt-assistance.html.gz','https://www.prattlibrary.org/services/social-impact-programs/social-worker-in-the-library')]:
        sources.append(entry(raw,filename,fetch(url),filename.split('.')[0],url,retrieved))
    sources.append(acquire_history(raw,library,retrieved))
    sources.extend(acquire_commuting(raw,library,retrieved))
    (raw/'manifest.json').write_text(json.dumps({'version':1,'acquiredAt':retrieved,'sources':sorted(sources,key=lambda e:e['id'])},sort_keys=True,indent=2)+'\n')


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library',type=Path,default=Path('/media/user/data/babylon-data'))
    parser.add_argument('--raw',type=Path,default=ROOT/'data/outreach/raw')
    args=parser.parse_args(); acquire(args.raw,args.library)
    print('Pinned Maryland extracts, boundaries, facility evidence, history and commuting inputs.')


if __name__=='__main__':
    main()
