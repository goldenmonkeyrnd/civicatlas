"""Guard statistical universes, status semantics and the actual Maryland release."""
from __future__ import annotations
import json
import unittest
from pathlib import Path

from pipeline.constants import ROOT
from pipeline.outreach_build import METRICS, observation, parse_cell


class OutreachTests(unittest.TestCase):
    def test_missing_annotations_never_become_counts_and_zero_is_observed(self) -> None:
        self.assertEqual(parse_cell('0'),(0.0,'available'))
        self.assertEqual(parse_cell('N'),(None,'suppressed'))
        for literal in ('-666666666','-888888888','-555555555',''):
            self.assertEqual(parse_cell(literal),(None,'missing'))
        self.assertEqual(parse_cell('-999999999'),(None,'suppressed'))

    def test_rent_burden_excludes_not_computed_households(self) -> None:
        row={f'B25070_E{i:03}':'0' for i in range(1,12)}
        row.update({f'B25070_M{i:03}':'1' for i in range(1,12)})
        row.update(B25070_E001='100',B25070_E002='20',B25070_E007='30',B25070_E011='50')
        value=observation(row,'B25070',(7,8,9,10),tuple(range(2,11)),True)
        self.assertEqual(value['denominator'],50)
        self.assertEqual(value['value'],60)
        row['B25070_E002']='0';row['B25070_E007']='0'
        value=observation(row,'B25070',(7,8,9,10),tuple(range(2,11)),True)
        self.assertIsNone(value['value']);self.assertEqual(value['status'],'missing')

    def test_only_independent_household_counts_feed_coverage(self) -> None:
        self.assertEqual({m[0] for m in METRICS if m[-1]},{'households','noVehicle','noInternet'})
        self.assertTrue(all(m[3]=='count' for m in METRICS if m[-1]))

    def test_current_release_matches_real_geographies_and_facility_evidence(self) -> None:
        catalog=json.loads((ROOT/'data/outreach/catalog.json').read_text())
        areas=json.loads((ROOT/'data/outreach/areas.json').read_text())
        facilities=json.loads((ROOT/'data/outreach/facilities.json').read_text())
        by_geoid={a['geoid']:a for a in areas}
        self.assertEqual(len(by_geoid),5578)
        self.assertEqual(catalog['counts']['blockGroups'],4079)
        self.assertEqual(catalog['counts']['tracts'],1475)
        self.assertEqual(by_geoid['24510']['name'],'Baltimore City')
        self.assertEqual(by_geoid['24005']['name'],'Baltimore County')
        self.assertEqual(by_geoid['24510']['values']['households']['value'],255668)
        self.assertEqual(by_geoid['24510']['values']['noInternet']['value'],24937)
        self.assertEqual(by_geoid['24510']['values']['noVehicle']['value'],68150)
        current={f['name']:f for f in facilities if f['county']=='24510' and f['kind']=='library'}
        for name in ('Central Library','Pennsylvania Avenue','Southeast Anchor','Waverly'):
            self.assertTrue(current[name]['baseline'],name)
            self.assertFalse(current[name]['candidate'],name)
        for name in ('Herring Run','Orleans Street','Forest Park','Reisterstown Road','Clifton','Washington Village'):
            self.assertEqual(current[name]['status'],'closed',name)
            self.assertFalse(current[name]['candidate'],name)
        self.assertIn('606 Cherry Hill Road',current['Cherry Hill']['address'])
        self.assertTrue(current['Johnston Square']['candidate'])
        brooklyn=next(f for f in facilities if f['id']=='library-65')
        self.assertEqual(brooklyn['county'],'24003')
        self.assertFalse(brooklyn['candidate'])
        for facility in facilities:
            if facility['candidate'] or facility['baseline']:
                self.assertEqual(facility['status'],'verified')
        self.assertTrue(catalog['assets']['blockGroups'].startswith('/data/outreach/'+catalog['revision']+'/'))


if __name__=='__main__':unittest.main()
