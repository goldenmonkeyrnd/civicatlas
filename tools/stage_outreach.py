"""Stage immutable, gzip-compressed public artifacts without copying raw inputs."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def stage(check: bool = False) -> dict[str, object]:
    release = ROOT / 'data/outreach'
    catalog = json.loads((release / 'catalog.json').read_text())
    revision = catalog['revision']
    destination = ROOT / 'public/data/outreach' / revision
    manifest = []
    source_files = [release / name for name in ['catalog.json', 'facilities.json', 'block-groups.geojson', 'tracts.geojson', 'roads-manifest.json']]
    source_files += sorted((release / 'commutes').glob('*.json'))
    source_files += sorted((release / 'graphs').glob('*.json'))
    if len(list((release / 'graphs').glob('*.json'))) != 24:
        # Committed compressed graph artifacts are the publication inputs in CI.
        graph_files = sorted((destination / 'graphs').glob('*.json.gz'))
        if len(graph_files) != 24:
            raise ValueError('All 24 county road graphs are required before staging a release')
        for p in graph_files:
            content = gzip.decompress(p.read_bytes())
            graph = json.loads(content)
            if graph['dataRevision'] != revision:
                raise ValueError(f'{p.name}: road graph belongs to another data release')
            manifest.append({'path':str(p.relative_to(destination)),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'decodedSha256':hashlib.sha256(content).hexdigest()})
    for source in source_files:
        if not source.exists():
            raise ValueError(f'Missing publication artifact: {source}')
        content = source.read_bytes()
        packed = gzip.compress(content, compresslevel=9, mtime=0)
        if len(packed) >= 25 * 1024 * 1024:
            raise ValueError(f'{source.name}: compressed artifact exceeds Worker asset limit')
        path = destination / (str(source.relative_to(release)) + '.gz')
        if check:
            if not path.exists() or path.read_bytes() != packed:
                raise ValueError(f'Publication asset differs from reproducible output: {path}')
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(packed)
        manifest.append({'path':str(path.relative_to(destination)),'bytes':len(packed),'sha256':hashlib.sha256(packed).hexdigest(),'decodedSha256':hashlib.sha256(content).hexdigest()})
    result = {'version':1,'revision':revision,'artifacts':sorted(manifest,key=lambda m:m['path'])}
    blob = (json.dumps(result, sort_keys=True, indent=2) + '\n').encode()
    path = release / 'assets-manifest.json'
    if check:
        if path.read_bytes() != blob:
            raise ValueError('Asset manifest differs from publication artifacts')
    else:
        path.write_bytes(blob)
    return {'revision':revision,'assets':len(manifest),'compressedBytes':sum(m['bytes'] for m in manifest)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    print(json.dumps(stage(args.check)))
