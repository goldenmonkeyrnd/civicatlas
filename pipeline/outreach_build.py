"""Build the inspectable Maryland Outreach Lab offline from pinned bounded inputs."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import html
import io
import json
import math
import re
import sqlite3
import tempfile
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

import shapefile
from pyproj import CRS, Transformer
from shapely.geometry import mapping, shape

from pipeline.build import SourceIntegrityError, canonical_json, sql_value, transform_coordinates
from pipeline.constants import FIPS, ROOT
from pipeline.outreach_acquire import TABLES

# Formula declarations contain exact bulk-column indices, not plausible aliases.
METRICS = (
    ('population','Population','people','count','Total population','B01003',(1,),None,False),
    ('households','Households','people','count','Households','B11001',(1,),None,True),
    ('familyHouseholds','Family households','people','percent','Households','B11001',(2,),(1,),False),
    ('householdPopulation','Population in households','people','count','Population in households','B11002',(1,),None,False),
    ('incomeUnder35k','Household income below $35,000','income','percent','Households; 2024 inflation-adjusted dollars','B19001',tuple(range(2,8)),(1,),False),
    ('incomeEarnings','Households with earnings','income','percent','Households','B19051',(2,),(1,),False),
    ('unemployment','Unemployment','work','percent','Civilian labor force age 16 and older','B23025',(5,),(3,),False),
    ('poverty','Below poverty level','income','percent','Population for whom poverty status is determined','C17002',(2,3),(1,),False),
    ('rentBurden','Rent takes at least 30% of income','housing','percent','Renter-occupied housing units with a computed rent-to-income ratio','B25070',(7,8,9,10),tuple(range(2,11)),False),
    ('noVehicle','Households without a vehicle','access','count','Occupied housing units (households)','B25044',(3,10),(1,),True),
    ('noInternet','Households without internet access','access','count','Households','B28002',(13,),(1,),True),
    ('noVehicleShare','Households without a vehicle (%)','access','percent','Occupied housing units (households)','B25044',(3,10),(1,),False),
    ('noInternetShare','Households without internet access (%)','access','percent','Households','B28002',(13,),(1,),False),
)


def load_inputs(raw: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
    manifest=json.loads((raw/'manifest.json').read_text())
    if manifest.get('version')!=1:
        raise SourceIntegrityError('Unsupported outreach manifest version')
    payloads={}
    for source in manifest['sources']:
        filename=source['file']
        if Path(filename).name!=filename:
            raise SourceIntegrityError(f'Nonlocal source filename: {filename}')
        stored=(raw/filename).read_bytes()
        if hashlib.sha256(stored).hexdigest()!=source['storedSha256']:
            raise SourceIntegrityError(f'Pinned outreach source hash mismatch: {filename}')
        payload=gzip.decompress(stored) if filename.endswith('.gz') else stored
        if hashlib.sha256(payload).hexdigest()!=source['sha256']:
            raise SourceIntegrityError(f'Pinned outreach payload hash mismatch: {filename}')
        payloads[source['id']]=payload
    return manifest,payloads


def parse_cell(literal: str) -> tuple[float|None,str]:
    if literal in {'','(X)','-','null'}:
        return None,'missing'
    if literal in {'N','-999999999'}:
        return None,'suppressed'
    try:
        numeric=float(literal)
    except ValueError as error:
        raise ValueError(f'Unrecognized ACS cell {literal!r}') from error
    # Negative Census annotation codes carry no numeric magnitude.
    return (None,'missing') if numeric<0 else (numeric,'available')


def aggregate_cells(row: dict[str,str], table: str, cells: tuple[int,...], measure: str='E') -> tuple[float|None,str]:
    values=[parse_cell(row.get(f'{table}_{measure}{cell:03}','')) for cell in cells]
    if any(status!='available' for _,status in values):
        return None,'suppressed' if any(status=='suppressed' for _,status in values) else 'missing'
    amounts=[float(value) for value,_ in values if value is not None]
    return (math.sqrt(sum(value*value for value in amounts)) if measure=='M' else sum(amounts)),'available'


def observation(row: dict[str,str], table: str, numerator_cells: tuple[int,...], denominator_cells: tuple[int,...]|None, percent: bool) -> dict[str, Any]:
    numerator,status=aggregate_cells(row,table,numerator_cells)
    numerator_moe,_=aggregate_cells(row,table,numerator_cells,'M')
    denominator=None; denominator_moe=None
    if denominator_cells:
        denominator,denominator_status=aggregate_cells(row,table,denominator_cells)
        denominator_moe,_=aggregate_cells(row,table,denominator_cells,'M')
        if percent and denominator_status!='available':
            status=denominator_status
    value=numerator;moe=numerator_moe
    if percent:
        if numerator is None or denominator is None or denominator<=0:
            value=None;moe=None
            if status=='available':status='missing'
        else:
            ratio=numerator/denominator;value=ratio*100
            if numerator_moe is None or denominator_moe is None:
                moe=None
            else:
                variance=numerator_moe**2-ratio**2*denominator_moe**2
                # ACS subset proportion formula; ratio fallback if covariance term yields negative radicand.
                if variance<0:variance=numerator_moe**2+ratio**2*denominator_moe**2
                moe=math.sqrt(variance)/denominator*100
    return {'value':round(value,6) if value is not None else None,'moe':round(moe,6) if moe is not None else None,'numerator':numerator,'denominator':denominator,'status':status,'sourceId':f'acs-2024-{table}'}


def build_geography(payloads: dict[str,bytes]) -> tuple[dict[str,dict[str,Any]],dict[str,Any],dict[str,Any]]:
    areas={};collections={}
    for level,suffix in [('tract','tract'),('blockGroup','bg')]:
        with zipfile.ZipFile(io.BytesIO(payloads[f'census-geometry-{suffix}'])) as archive:
            prefix=f'tl_2024_24_{suffix}'
            transform=Transformer.from_crs(CRS.from_wkt(archive.read(prefix+'.prj').decode()),4326,always_xy=True)
            reader=shapefile.Reader(shp=io.BytesIO(archive.read(prefix+'.shp')),shx=io.BytesIO(archive.read(prefix+'.shx')),dbf=io.BytesIO(archive.read(prefix+'.dbf')),encoding='utf-8')
            features=[]
            for record in reader.iterShapeRecords():
                fields=record.record.as_dict();geoid=fields['GEOID'];county=geoid[:5]
                if county not in FIPS:raise ValueError(f'Non-Maryland boundary {geoid}')
                lon,lat=transform.transform(float(fields['INTPTLON']),float(fields['INTPTLAT']))
                converted=transform_coordinates(record.shape.__geo_interface__['coordinates'],transform)
                original={'type':record.shape.__geo_interface__['type'],'coordinates':converted}
                # Display-only simplification; authoritative points retain Census internal locations.
                geom=shape(original).simplify(0.00008,preserve_topology=True)
                if geom.is_empty or not geom.is_valid:raise ValueError(f'Invalid geometry {geoid}')
                name=f"Block group {fields['BLKGRPCE']} · Tract {fields['TRACTCE'][:4].lstrip('0') or '0'}.{fields['TRACTCE'][4:]}" if level=='blockGroup' else f"Tract {fields['NAME']}"
                area={'geoid':geoid,'level':level,'county':county,'tract':geoid[:11] if level=='blockGroup' else None,'name':name,'lon':round(lon,7),'lat':round(lat,7),'values':{},'raw':{}}
                areas[geoid]=area
                features.append({'type':'Feature','id':geoid,'properties':{'GEOID':geoid,'NAME':name,'county':county,'tract':geoid[:11],'lon':area['lon'],'lat':area['lat']},'geometry':mapping(geom)})
            collections[level]={'type':'FeatureCollection','features':sorted(features,key=lambda f:f['id'])}
    county_geometry=json.loads((ROOT/'data/processed/maryland-counties.geojson').read_text())
    county_names={f['id']:f['properties']['name'].replace(' city',' City') for f in county_geometry['features']}
    for county in FIPS:
        geom=shape(next(f['geometry'] for f in county_geometry['features'] if f['id']==county));point=geom.representative_point()
        areas[county]={'geoid':county,'level':'county','county':county,'tract':None,'name':county_names[county],'lon':round(point.x,7),'lat':round(point.y,7),'values':{},'raw':{}}
    return areas,collections['blockGroup'],collections['tract']


def build_areas(payloads: dict[str,bytes]) -> tuple[list[dict[str,Any]],dict[str,Any],dict[str,Any],list[dict[str,Any]]]:
    areas,bg,tracts=build_geography(payloads);indicators=[]
    for table in TABLES:
        rows=list(csv.DictReader(io.StringIO(payloads[f'acs-2024-{table}'].decode()),delimiter='|'))
        found=set()
        for row in rows:
            geoid=row['GEO_ID'].split('US',1)[1]
            if geoid not in areas:raise ValueError(f'ACS/boundary identifier mismatch {table}: {geoid}')
            if geoid in found:raise ValueError(f'Duplicate ACS geography {table}: {geoid}')
            found.add(geoid);areas[geoid]['raw'][f'acs-2024-{table}']=row
        if found!=set(areas):raise ValueError(f'Incomplete ACS coverage in {table}: {len(found)}')
    for identifier,label,theme,unit,universe,table,nums,dens,demand in METRICS:
        cols='+'.join(f'{table}_E{i:03}' for i in nums)
        denom='+'.join(f'{table}_E{i:03}' for i in dens) if dens else None
        definition=cols+(f' divided by ({denom}) × 100' if unit=='percent' else '')
        if identifier=='rentBurden':definition+='; excludes not-computed rent-to-income ratios'
        indicators.append({'id':identifier,'label':label,'theme':theme,'unit':unit,'universe':universe,'definition':definition,'sourceId':f'acs-2024-{table}','period':'2020–2024 ACS five-year estimates','demandEligible':demand})
        for area in areas.values():
            area['values'][identifier]=observation(area['raw'][f'acs-2024-{table}'],table,nums,dens,unit=='percent')
    return sorted(areas.values(),key=lambda a:a['geoid']),bg,tracts,indicators


def text_only(value: str) -> str:
    return re.sub(r'\s+',' ',html.unescape(re.sub('<[^>]*>',' ',value))).strip()


def norm(value: str) -> str:
    return re.sub('[^a-z0-9]','',value.lower().replace('street','st').replace('avenue','ave').replace('road','rd').replace('north','n').replace('east','e').replace('west','w').replace('south','s').replace('boulevard','blvd'))


def build_facilities(payloads: dict[str,bytes], manifest: dict[str,Any], areas: list[dict[str,Any]]) -> tuple[list[dict[str,Any]],list[dict[str,Any]]]:
    checked=manifest['acquiredAt'];rows=json.loads(payloads['maryland-libraries'])['features'];items=[];report=[]
    counties={norm(a['name'].replace(' County','')):a['geoid'] for a in areas if a['level']=='county'}
    directory=payloads['pratt-locations'].decode()
    cards=re.split(r'<div class="card" id="location\d+"',directory)[1:]
    verified={}
    for card in cards:
        name=re.search(r'<h2>(.*?)</h2>',card,re.S);address=re.search(r'<div class="address">(.*?)</div>',card,re.S);hours=re.search(r'<span class="hours">(.*?)</span>',card,re.S);url=re.search(r'<a class="cover" href="([^"]+)"',card)
        if not all((name,address,hours,url)):raise ValueError('Changed Pratt directory shape')
        verified[norm(text_only(name.group(1)).replace('Library','').replace('Branch','').strip())]={'name':text_only(name.group(1)),'address':text_only(address.group(1)),'closed':text_only(hours.group(1))=='Closed' or 'building maintenance' in text_only(hours.group(1)),'url':url.group(1)}
    verified['central']={'name':'Central Library','address':'400 Cathedral Street Baltimore, MD 21201','closed':False,'url':'https://www.prattlibrary.org/locations/central'}
    benefits={norm(name) for name in ('Central','Pennsylvania Avenue','Southeast Anchor','Waverly')}
    assistance=text_only(payloads['pratt-assistance'].decode())
    for label in ('SNAP application assistance','Central Library','Pennsylvania Avenue','Southeast Anchor Library','Waverly'):
        if label not in assistance:raise ValueError(f'Pratt assistance evidence changed: {label}')
    coordinate_overrides=json.loads(payloads.get('facility-coordinates',b'{}'))
    from shapely.geometry import Point
    county_shapes={f['id']:shape(f['geometry']) for f in json.loads((ROOT/'data/processed/maryland-counties.geojson').read_text())['features']}
    used=set()
    for row in rows:
        p=row['properties'];county=counties.get(norm(p['County']))
        if not county:
            raise ValueError(f'Unmatched library county {p["County"]}')
        name=p['ANCHORNAME'];lon,lat=row['geometry']['coordinates'];identifier=f"library-{p['OBJECTID']}"
        actual_county=next((code for code,geom in county_shapes.items() if geom.covers(Point(lon,lat))),None)
        if actual_county and actual_county!=county:
            report.append({'id':f'county-{identifier}','kind':'corrected','label':name,'detail':f'Inventory county {county} conflicts with point location; Census boundary assigns {actual_county}.','sourceId':'maryland-libraries'});county=actual_county
        item={'id':identifier,'name':name,'kind':'library','county':county,'address':p['ADDRESS'],'lon':round(lon,7),'lat':round(lat,7),'status':'unresolved','baseline':False,'candidate':False,'sourceId':'maryland-libraries','verificationUrl':p.get('SYSTEMURL',''),'checkedAt':checked,'note':'Inventory point; current public opening/location has not been independently confirmed. Excluded from automatic suggestions.'}
        if county=='24510':
            key=norm(name.replace('Library','').replace('Branch','').replace('Enoch Pratt Free','').strip())
            # Inventory calls Central the State Library Resource Center.
            if 'cathedral' in p['ADDRESS'].lower() or 'central' in key or 'statelibraryresource' in key:key='central'
            current=verified.get(key)
            if current:
                used.add(key);item.update(name=current['name'],verificationUrl=current['url'],sourceId='pratt-locations',status='closed' if current['closed'] else 'verified')
                if norm(item['address'])!=norm(current['address']):
                    report.append({'id':f'address-{identifier}','kind':'corrected','label':current['name'],'detail':f"Current directory address: {current['address']}. Inventory: {item['address']}.",'sourceId':'pratt-locations'})
                    item['address']=current['address']
                    if key in coordinate_overrides:
                        item['lon'],item['lat']=coordinate_overrides[key]['lon'],coordinate_overrides[key]['lat']
                    # Different street numbers require independently pinned coordinates.
                    elif re.findall(r'\d+',p['STREETADDR'])[:1]!=re.findall(r'\d+',current['address'])[:1]:
                        item['status']='closed' if current['closed'] else 'unresolved';item['note']='Current address corrected but revised coordinates remain unverified.'
                item['baseline']=item['status']=='verified' and key in benefits
                item['candidate']=item['status']=='verified' and not item['baseline']
                if item['status']=='closed':item['note']='Closed in the current Pratt directory on the release date; excluded from baseline and suggestions.'
                elif item['status']=='verified':item['note']='Confirmed in current Pratt directory. '+('Documented SNAP application assistance at scheduled social-worker drop-in hours; geography does not model schedules.' if item['baseline'] else 'Open library; proposed outreach candidate. Benefits assistance is not asserted.')
                if item['baseline']:item['sourceId']='pratt-assistance'
            else:
                item['status']='closed';item['note']='Legacy inventory location absent from the current Pratt directory; excluded.'
        if item['status']!='verified':report.append({'id':f'exclude-{identifier}','kind':'excluded','label':name,'detail':item['note'],'sourceId':item['sourceId']})
        items.append(item)
    for key,current in verified.items():
        if key in used:continue
        if key not in coordinate_overrides:
            report.append({'id':f'unmatched-{key}','kind':'unmatched','label':current['name'],'detail':'Current-directory location absent from legacy inventory; address found but no pinned coordinate match. Excluded from suggestions.','sourceId':'pratt-locations'});continue
        if 'technology lab' in current['name'].lower():
            report.append({'id':f'exclude-{key}','kind':'excluded','label':current['name'],'detail':'Technology lab is not a library branch candidate in this demonstration.','sourceId':'pratt-locations'});continue
        coord=coordinate_overrides[key];is_closed=current['closed']
        items.append({'id':f'library-pratt-{key}','name':current['name'],'kind':'library','county':'24510','address':current['address'],'lon':coord['lon'],'lat':coord['lat'],'status':'closed' if is_closed else 'verified','baseline':not is_closed and key in benefits,'candidate':not is_closed and key not in benefits,'sourceId':'pratt-assistance' if key in benefits else 'pratt-locations','verificationUrl':current['url'],'checkedAt':checked,'note':'Current-directory location added after coordinate verification; absent from older state inventory.'})
        report.append({'id':f'new-{key}','kind':'corrected','label':current['name'],'detail':f"New current-directory location {current['address']} added; legacy state inventory did not contain it.",'sourceId':'pratt-locations'})
    webmap=json.loads(payloads['maryland-dhs']);dhs_count=0
    for layer_index,layer in enumerate(webmap['operationalLayers']):
        for sublayer in layer.get('featureCollection',{}).get('layers',[]):
            for feature in sublayer.get('featureSet',{}).get('features',[]):
                p=feature['attributes'];name=p.get('name','Unnamed DHS location');address=text_only(p.get('desc',''));lat=p.get('lat');lon=p.get('lon')
                if lat is None or lon is None:continue
                county=next((code for code,geom in county_shapes.items() if geom.covers(Point(lon,lat))),None)
                if not county:continue
                identifier=f"dhs-{layer_index}-{p['__OBJECTID']}"; title=layer['title'];bad=any(term in (name+' '+title).lower() for term in ['old','no public service','child support only','child support enforcement','state office']) or title=='Varying Services (Please view details)'
                name=text_only(name)
                evidence_id=f'dhs-office-{county}';evidence=text_only(payloads.get(evidence_id,b'').decode())
                digits=re.search(r'\b\d{1,5}\b',address)
                # Match street number plus street tokens against the current county office page.
                street=address.split('Baltimore')[0].split('Phone:')[0].strip()
                normalized=norm(street)
                match=bool(digits and len(normalized)>8 and normalized in norm(evidence))
                if not match and digits:
                    short=norm(' '.join(street.split()[:4]));match=len(short)>8 and short in norm(evidence)
                verified_office=match and not bad
                item={'id':identifier,'name':name,'kind':'benefits','county':county,'address':address,'lon':round(lon,7),'lat':round(lat,7),'status':'verified' if verified_office else 'closed' if bad else 'unresolved','baseline':verified_office,'candidate':False,'sourceId':evidence_id if verified_office else 'maryland-dhs','verificationUrl':f'https://dhs.maryland.gov/local-offices/{county_slug(county,areas)}/','checkedAt':checked,'note':'Public service office address matched to the current DHS county-office directory; location point from DHS map.' if verified_office else 'Excluded: obsolete/non-public/child-support-only record.' if bad else 'Map address could not be reconciled with current DHS directory; excluded from baseline.'}
                items.append(item);dhs_count+=1
                if not verified_office:report.append({'id':f'exclude-{identifier}','kind':'excluded','label':name,'detail':item['note'],'sourceId':'maryland-dhs'})
    for key,coord in coordinate_overrides.items():
        if not key.startswith('dhs-new-'):continue
        if not coord.get('evidenceId') or coord['evidenceId'] not in payloads:raise ValueError('New DHS site lacks source evidence')
        items.append({'id':key,'name':coord['name'],'kind':'benefits','county':coord['county'],'address':coord['address'],'lon':coord['lon'],'lat':coord['lat'],'status':'verified','baseline':True,'candidate':False,'sourceId':coord['evidenceId'],'verificationUrl':coord['url'],'checkedAt':checked,'note':'Current DHS customer-service office added from directory; coordinates pinned through official address geocoding.'})
        report.append({'id':f'add-{key}','kind':'corrected','label':coord['name'],'detail':'Current public-assistance location missing from older DHS map added using current directory and pinned geocoder response.','sourceId':coord['evidenceId']})
    items.sort(key=lambda item:item['id'])
    report.append({'id':'facility-coverage','kind':'coverage','label':'Facility verification coverage','detail':f"{len(rows)} legacy library points and {dhs_count} DHS map points inspected. {sum(i['candidate'] for i in items)} current verified outreach candidates; {sum(i['baseline'] for i in items)} verified benefits-assistance baseline sites. Current library verification focuses on Baltimore City; other inventory points are visible but excluded from automatic suggestions.",'sourceId':'maryland-libraries'})
    return items,sorted(report,key=lambda item:item['id'])


def county_slug(county: str, areas: list[dict[str,Any]]) -> str:
    name=next(a['name'] for a in areas if a['geoid']==county)
    return name.lower().replace(' county','-county').replace("'",'').replace(' ','-')


def build_history(payloads: dict[str,bytes], revision: str) -> list[dict[str,Any]]:
    raw=json.loads(payloads['qcew-history']);histories=[]
    for county in FIPS:
        observations=[]
        for row in sorted((r for r in raw if r['area_fips']==county),key=lambda r:int(r['year'])):
            suppressed=(row['disclosure_code'] or '').strip()=='N'
            observations.append({'year':int(row['year']),'employment':None if suppressed else int(row['annual_avg_emplvl']),'annualPay':None if suppressed else int(row['avg_annual_pay']),'establishments':None if suppressed else int(row['annual_avg_estabs']),'status':'suppressed' if suppressed else 'available','sourceId':'qcew-history'})
        if len(observations)!=15:raise ValueError(f'Incomplete QCEW history {county}')
        histories.append({'revision':revision,'county':county,'observations':observations})
    return histories


def build_commutes(payloads: dict[str,bytes], revision: str) -> list[dict[str,Any]]:
    coords={row['geoid']:[round(row['lon'],6),round(row['lat'],6)] for row in json.loads(payloads['lodes-xwalk'])};flows=json.loads(payloads['lodes-2021']);results=[]
    for county in FIPS:
        relevant=[row for row in flows if row['home'].startswith(county)]
        # Top 120 actual home/work block-group relationships, chosen once per release.
        available=[row for row in relevant if row['home'] in coords and row['work'] in coords]
        shown=sorted(available,key=lambda r:(-r['jobs'],r['home'],r['work']))[:120]
        total=sum(row['jobs'] for row in relevant)
        results.append({'revision':revision,'county':county,'year':2021,'totalJobs':total,'flows':[{'home':r['home'],'work':r['work'],'jobs':r['jobs'],'from':coords[r['home']],'to':coords[r['work']]} for r in shown],'omittedJobs':total-sum(r['jobs'] for r in shown),'limitation':'LODES 2021 all-job home/work relationships with both ends in Maryland (main file only). Top 120 block-group flows shown; omittedJobs includes remaining flows and unmatched coordinates. Coordinates are unweighted mean Census-block internal points; lines are relationships, not observed travel routes.'})
    return results


def source_metadata(manifest: dict[str,Any]) -> list[dict[str,Any]]:
    sources=[]
    for entry in manifest['sources']:
        identifier=entry['id'];publisher='U.S. Census Bureau';period='2020–2024'
        if identifier.startswith('acs-2024-'):title=f"ACS 2024 five-year table {identifier.removeprefix('acs-2024-')}";transformation='Exact Maryland county, tract and block-group rows filtered from official pipe-delimited table. Raw estimates and margins of error retained. Additive MOEs use square root of summed squares; percentage MOEs use ACS subset proportion formula, ratio fallback for negative radicand; estimates with unavailable/negative annotation codes remain null.'
        elif identifier.startswith('census-geometry'):title='Census 2024 Maryland '+('block-group' if identifier.endswith('bg') else 'tract')+' boundaries';period='2024';transformation='NAD83 shapefile coordinates converted to WGS84. Display polygons simplified at 0.00008 degrees preserving topology; representative locations retain Census INTPTLON/INTPTLAT internal points.'
        elif identifier=='qcew-history':title='Maryland county private-sector employment and nominal pay history';publisher='U.S. Bureau of Labor Statistics';period='2010–2024';transformation='Annual county/private-sector/total-industry rows only (own_code=5, industry_code=10, agglvl_code=71, size_code=0). Employment is workplace jobs; average annual pay is nominal USD, not median or inflation-adjusted. National input SHA-256 values retained in manifest.'
        elif identifier.startswith('lodes'):title='Maryland LODES 2021 home/work relationships' if identifier=='lodes-2021' else 'LODES Census block coordinates';period='2021' if identifier=='lodes-2021' else '2020 Census blocks; 2025 crosswalk revision';transformation='Main Maryland all-jobs OD file aggregated by 12-digit home/work block group. No out-of-state jobs from auxiliary files. Crosswalk points averaged unweighted by block group for relationship visualization only; original input hashes retained.'
        elif identifier=='maryland-libraries':title='Maryland iMAP library inventory';publisher='Maryland iMAP';period='Legacy inventory; checked October 14, 2026';transformation='198 official inventory point records retained with stable source OBJECTID. Current directory verification required before automatic suggestions; unresolved locations excluded.'
        elif identifier.startswith('dhs-office'):title=f'Maryland DHS current county-office directory {identifier[-5:]}';publisher='Maryland Department of Human Services';period='Checked October 14, 2026';transformation='Current official directory supplies public-service address verification. Child-support-only, no-public-service and obsolete map points excluded.'
        elif identifier=='maryland-dhs':title='Maryland DHS office map';publisher='Maryland Department of Human Services';period='Legacy map; checked October 14, 2026';transformation='Official embedded feature-collection points reconciled with current county-office pages. Obsolete, no-public-service and child-support-only markers excluded.'
        elif identifier=='pratt-locations':title='Pratt Library current locations and hours';publisher='Enoch Pratt Free Library';period='Checked October 14, 2026';transformation='Current branch names, addresses and ongoing closures reconcile the older state library inventory. Ordinary outside-hours closed-now status does not imply a facility closure.'
        elif identifier=='pratt-assistance':title='Pratt Social Worker in the Library';publisher='Enoch Pratt Free Library';period='Checked October 14, 2026';transformation='SNAP application assistance documented at Central, Pennsylvania Avenue, Southeast Anchor and Waverly. Scheduled drop-in hours are not modeled as continuous access.'
        elif identifier=='facility-coordinates':title='Official Census geocoder coordinate reconciliation';period='Current benchmark Public_AR_Current';transformation='Pinned geocoder result corrects relocated or missing current-directory facility coordinates; returned matched addresses retained in raw source. No arbitrary coordinates substituted.'
        else:title='ACS 2024 variable table shells';transformation='Bounded variable declarations for published tables; exact universes and column definitions retained.'
        sources.append({'id':identifier,'title':title,'publisher':publisher,'url':entry['url'],'period':period,'retrievedAt':entry['retrievedAt'],'sha256':entry['sha256'],'transformation':transformation,'license':'U.S. government public data' if publisher.startswith('U.S.') else 'Public institutional directory; attributed to publisher'})
    return sources


def validate(areas: list[dict[str,Any]], facilities: list[dict[str,Any]], bg: dict[str,Any], tracts: dict[str,Any]) -> None:
    counts=Counter(a['level'] for a in areas)
    if counts!={'county':24,'tract':1475,'blockGroup':4079}:raise ValueError(f'Unexpected area coverage: {counts}')
    for level,collection in [('blockGroup',bg),('tract',tracts)]:
        if {f['id'] for f in collection['features']}!={a['geoid'] for a in areas if a['level']==level}:raise ValueError(f'Boundary mismatch {level}')
    for area in areas:
        for identifier,item in area['values'].items():
            if item['value'] is not None and (item['value']<0 or not math.isfinite(item['value'])):raise ValueError(f'Invalid {identifier} at {area["geoid"]}')
            if item['status']=='available' and item['value'] is None:raise ValueError('Available status without value')
            if identifier in ('familyHouseholds','incomeUnder35k','incomeEarnings','poverty','rentBurden','unemployment','noVehicleShare','noInternetShare') and item['value'] is not None and item['value']>100:raise ValueError(f'Invalid proportion {identifier}')
    if len({f['id'] for f in facilities})!=len(facilities):raise ValueError('Duplicate facility ID')
    for item in facilities:
        if (item['candidate'] or item['baseline']) and item['status']!='verified':raise ValueError('Unverified facility entered planning')
        if item['candidate'] and item['baseline']:raise ValueError('Facility cannot be baseline and an additional candidate')
    if sum(f['candidate'] for f in facilities if f['county']=='24510')<3:raise ValueError('Baltimore needs three verified candidate locations')


def write_seed(out: Path, catalog: dict[str,Any], areas: list[dict[str,Any]], facilities: list[dict[str,Any]], history: list[dict[str,Any]]) -> None:
    schema='''CREATE TABLE IF NOT EXISTS outreach_release(id TEXT PRIMARY KEY,catalog_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS outreach_areas(geoid TEXT PRIMARY KEY,level TEXT NOT NULL,county TEXT NOT NULL,tract TEXT,name TEXT NOT NULL,lon REAL NOT NULL,lat REAL NOT NULL,values_json TEXT NOT NULL,raw_json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS outreach_areas_county ON outreach_areas(county,level);
CREATE INDEX IF NOT EXISTS outreach_areas_tract ON outreach_areas(tract,level);
CREATE TABLE IF NOT EXISTS outreach_facilities(id TEXT PRIMARY KEY,county TEXT NOT NULL,payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS outreach_history(county TEXT PRIMARY KEY,payload_json TEXT NOT NULL);
'''
    lines=[schema,'DELETE FROM outreach_release;','DELETE FROM outreach_areas;','DELETE FROM outreach_facilities;','DELETE FROM outreach_history;',f"INSERT INTO outreach_release VALUES ('current',{sql_value(canonical_json(catalog))});"]
    for area in areas:
        fields=[area[k] for k in ('geoid','level','county','tract','name','lon','lat')]+[canonical_json(area['values']),canonical_json(area['raw'])]
        lines.append('INSERT INTO outreach_areas VALUES ('+','.join(sql_value(v) for v in fields)+');')
    for facility in facilities:lines.append('INSERT INTO outreach_facilities VALUES ('+','.join(sql_value(v) for v in (facility['id'],facility['county'],canonical_json(facility)))+');')
    for item in history:lines.append('INSERT INTO outreach_history VALUES ('+','.join(sql_value(v) for v in (item['county'],canonical_json(item)))+');')
    sql='\n'.join(lines)+'\n';(out/'seed.sql').write_text(sql)
    con=sqlite3.connect(':memory:');con.executescript(sql);con.executescript(sql)
    if con.execute('SELECT COUNT(*) FROM outreach_areas').fetchone()[0]!=5578:raise ValueError('SQL seed round trip lost geographies')
    con.close()


def build(raw: Path, out: Path) -> dict[str,Any]:
    manifest,payloads=load_inputs(raw)
    recipe={'version':1,'metrics':METRICS,'geometryToleranceDegrees':0.00008,'facilityPolicyVersion':1,'commuteTopFlows':120}
    road_source_path=raw/'roads-source.json'
    road_source=json.loads(road_source_path.read_text()) if road_source_path.exists() else None
    if road_source is not None and (road_source.get('id')!='osm-roads' or not re.fullmatch('[0-9a-f]{64}',road_source.get('sha256',''))):
        raise SourceIntegrityError('Invalid pinned OSM road source metadata')
    revision='md-outreach-2024-'+hashlib.sha256(canonical_json({'inputs':manifest,'recipe':recipe,'roads':road_source}).encode()).hexdigest()[:16]
    areas,bg,tracts,indicators=build_areas(payloads);facilities,reconciliation=build_facilities(payloads,manifest,areas)
    reconciliation.extend([{'id':'acs-boundary-coverage','kind':'coverage','label':'ACS boundary reconciliation','detail':'24 county observations, 1,475 tracts and 4,079 block groups joined to matching 2024 geography IDs across all 10 ACS tables; zero unmatched identifiers.','sourceId':'census-geometry-bg'},{'id':'representative-points','kind':'coverage','label':'Representative household locations','detail':'Each block group is represented by its Census internal point. Road snaps and unmatched/disconnected results are reported separately by the graph release. Household distribution within each polygon is not observed.','sourceId':'census-geometry-bg'}])
    catalog={'version':1,'revision':revision,'title':'CivicAtlas · Maryland Outreach Lab','period':'ACS 2020–2024 · QCEW 2010–2024 · LODES 2021','counties':[{'geoid':county,'name':next(a['name'] for a in areas if a['geoid']==county),'blockGroups':sum(a['level']=='blockGroup' and a['county']==county for a in areas),'tracts':sum(a['level']=='tract' and a['county']==county for a in areas)} for county in FIPS],'indicators':indicators,'sources':source_metadata(manifest),'reconciliation':sorted(reconciliation,key=lambda item:item['id']),'counts':{'blockGroups':4079,'tracts':1475,'facilities':len(facilities)},'assets':{'blockGroups':f'/data/outreach/{revision}/block-groups.geojson','tracts':f'/data/outreach/{revision}/tracts.geojson','graphPattern':f'/data/outreach/{revision}/graphs/{{county}}.json','commutePattern':f'/data/outreach/{revision}/commutes/{{county}}.json'},'limitations':['ACS five-year estimates describe 2020–2024; estimates are not 2026 household counts. Margins of error reflect sampling uncertainty; modeled coverage has additional location uncertainty.','Household conditions overlap. One household measure is used at a time; percent persons below poverty is not a count of eligible households.','Coverage represents block-group internal points within modeled motor-vehicle road distance. It is not attendance, eligibility, transit access or driving directions.','Benefits sites have scheduled hours and finite capacity; geographic reach does not establish service availability.','Current library verification focuses on Baltimore City. Statewide legacy library points remain unresolved and excluded from automated recommendations.','LODES main file covers home/work relationships with both ends in Maryland in 2021. It omits cross-border auxiliary-file relationships; flow lines are not observed routes.','QCEW employment measures private covered workplace jobs; annual pay is average nominal USD, not median household income.']}
    if road_source is not None:catalog['sources'].append(road_source)
    history=build_history(payloads,revision)
    commutes=build_commutes(payloads,revision)
    return publish(out,catalog,areas,facilities,bg,tracts,history,commutes,revision)


def publish(out: Path, catalog: dict[str,Any], areas: list[dict[str,Any]], facilities: list[dict[str,Any]], bg: dict[str,Any], tracts: dict[str,Any], history: list[dict[str,Any]], commutes: list[dict[str,Any]], revision: str) -> dict[str,Any]:
    """Mandatory validation boundary immediately before every release write."""
    validate(areas,facilities,bg,tracts)
    out.mkdir(parents=True,exist_ok=True)
    for filename,value in [('catalog.json',catalog),('areas.json',areas),('facilities.json',facilities),('block-groups.geojson',bg),('tracts.geojson',tracts)]: (out/filename).write_text(canonical_json(value)+'\n')
    (out/'history.json').write_text(canonical_json(history)+'\n')
    (out/'commutes').mkdir(exist_ok=True)
    for item in commutes:(out/'commutes'/f"{item['county']}.json").write_text(canonical_json(item)+'\n')
    write_seed(out,catalog,areas,facilities,history)
    artifacts={str(path.relative_to(out)):{'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size} for path in sorted(out.rglob('*')) if path.is_file() and 'raw' not in path.relative_to(out).parts and path.name!='build-report.json' and 'graphs' not in path.relative_to(out).parts}
    report={'revision':revision,'areaCounts':dict(sorted(Counter(a['level'] for a in areas).items())),'statuses':dict(sorted(Counter(v['status'] for a in areas for v in a['values'].values()).items())),'facilityStatuses':dict(sorted(Counter(f['status'] for f in facilities).items())),'baselineSites':sum(f['baseline'] for f in facilities),'candidateSites':sum(f['candidate'] for f in facilities),'artifacts':artifacts};(out/'build-report.json').write_text(canonical_json(report)+'\n')
    return report


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true');parser.add_argument('--output-dir',type=Path,default=ROOT/'data/outreach');args=parser.parse_args()
    if args.check:
        with tempfile.TemporaryDirectory(prefix='civicatlas-outreach-check-') as tmp:
            report=build(ROOT/'data/outreach/raw',Path(tmp))
            for name in [*report['artifacts'],'build-report.json']:
                if (Path(tmp)/name).read_bytes()!=(args.output_dir/name).read_bytes():raise ValueError(f'Stale/non-reproducible outreach output {name}')
        print('PASS: source hashes, 5,578 geography rows, facility policy, SQL round trip and byte-identical outreach artifacts.')
    else:print(canonical_json(build(ROOT/'data/outreach/raw',args.output_dir)))


if __name__=='__main__':main()
