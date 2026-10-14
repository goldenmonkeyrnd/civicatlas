"""Check an isolated remote release before deploying its consuming application."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def verify() -> None:
    sql = (
        'SELECT id,catalog_json FROM outreach_release ORDER BY id;'
        'SELECT level,COUNT(*) AS count FROM outreach_areas GROUP BY level;'
        'SELECT id,payload_json FROM outreach_facilities ORDER BY id;'
        "SELECT values_json,raw_json FROM outreach_areas WHERE geoid='245102708031';"
    )
    result = subprocess.run(
        ['bunx', 'wrangler', 'd1', 'execute', 'civicatlas-outreach-20261014', '--remote', '--json', '--command', sql],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    replies = json.loads(result.stdout)
    if len(replies) != 4 or not all(r['success'] for r in replies):
        raise ValueError('Remote release queries did not all succeed')
    catalog = json.loads(''.join(r['catalog_json'] for r in replies[0]['results']))
    expected = json.loads((ROOT / 'data/outreach/catalog.json').read_text())
    if catalog != expected:
        raise ValueError('Remote catalog differs from the validated release')
    counts = {r['level']: r['count'] for r in replies[1]['results']}
    if counts != {'blockGroup': 4079, 'tract': 1475, 'county': 24}:
        raise ValueError(f'Remote geography coverage differs: {counts}')
    facilities = [json.loads(r['payload_json']) for r in replies[2]['results']]
    if facilities != sorted(json.loads((ROOT / 'data/outreach/facilities.json').read_text()), key=lambda f: f['id']):
        raise ValueError('Remote reconciled facility records differ')
    source_area = next(a for a in json.loads((ROOT / 'data/outreach/areas.json').read_text()) if a['geoid'] == '245102708031')
    raw = replies[3]['results']
    if len(raw) != 1 or json.loads(raw[0]['values_json']) != source_area['values'] or json.loads(raw[0]['raw_json']) != source_area['raw']:
        raise ValueError('Remote representative ACS record differs from its validated inputs')
    print(f"PASS: {catalog['revision']}; 5,578 areas; {len(facilities)} exact facility records; representative ACS estimates, MOEs and raw fields.")


if __name__ == '__main__':
    verify()
