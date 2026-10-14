"""Build reproducible, county-buffered directed graphs from pinned OSM extracts.

No geometric crossing is turned into a junction: only shared OSM node IDs join.
The input files remain outside Git. The bounded graphs and provenance are published.
"""
from __future__ import annotations

import argparse
from collections import Counter, deque
import hashlib
import json
import math
from pathlib import Path
import pickle
import time

import osmium
from pyproj import Transformer
import shapefile
from shapely.geometry import Point, LineString, shape
from shapely.ops import unary_union
from shapely import transform
from shapely.prepared import prep
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[1]
BUFFER_METERS = 17000  # exceeds the UI's maximum ten road miles
SNAP_METERS = 500
PROJECTION = Transformer.from_crs(4326, 26918, always_xy=True)
HIGHWAYS = {'motorway', 'trunk', 'primary', 'secondary', 'tertiary', 'unclassified', 'residential', 'living_street', 'service', 'motorway_link', 'trunk_link', 'primary_link', 'secondary_link', 'tertiary_link'}


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False) + '\n').encode()


def county_shapes() -> dict[str, object]:
    reader = shapefile.Reader('/media/user/data/babylon-data/tiger/county/tl_2024_us_county.shp')
    fields = [f[0] for f in reader.fields[1:]]
    result = {}
    for record in reader.iterShapeRecords():
        row = dict(zip(fields, record.record))
        if row['STATEFP'] == '24':
            result[row['GEOID']] = transform(shape(record.shape.__geo_interface__), PROJECTION.transform, interleaved=False).buffer(BUFFER_METERS)
    if len(result) != 24:
        raise ValueError('Expected all 24 Maryland jurisdictions in the county boundary source')
    return result


class Roads(osmium.SimpleHandler):
    def __init__(self) -> None:
        super().__init__()
        self.ways: dict[int, tuple[list[int], list[tuple[float, float]], str, int]] = {}
        self.blocked: set[int] = set()

    def node(self, node: osmium.osm.Node) -> None:
        # Explicitly denied public motor access wins over an ordinary road tag.
        permission = node.tags.get('motor_vehicle', node.tags.get('vehicle', node.tags.get('access', 'yes')))
        if permission in {'no', 'private'}:
            self.blocked.add(node.id)

    def way(self, way: osmium.osm.Way) -> None:
        if way.id in self.ways or way.tags.get('highway') not in HIGHWAYS or way.tags.get('area') == 'yes':
            return
        permission = way.tags.get('motorcar', way.tags.get('motor_vehicle', way.tags.get('vehicle', way.tags.get('access', 'yes'))))
        if permission in {'no', 'private'} or way.tags.get('construction') or way.tags.get('proposed'):
            return
        ids = [n.ref for n in way.nodes]
        if len(ids) < 2:
            return
        try:
            coords = [(n.lon, n.lat) for n in way.nodes]
        except osmium.InvalidLocationError as exc:
            raise ValueError(f'OSM way {way.id} has an unresolved node location') from exc
        # Broad study envelope includes the ten-mile Maryland border buffer.
        if not any(-79.8 <= lon <= -74.8 and 37.7 <= lat <= 40.0 for lon, lat in coords):
            return
        direction = way.tags.get('oneway:motor_vehicle', way.tags.get('oneway', 'yes' if way.tags.get('junction') == 'roundabout' else 'no'))
        mode = -1 if direction == '-1' else 1 if direction in {'yes', 'true', '1'} else 0
        self.ways[way.id] = (ids, coords, way.tags.get('name', way.tags.get('ref', 'Unnamed road')), mode)


def collect(directory: Path) -> tuple[Roads, list[dict[str, object]]]:
    cache = directory / 'roads-cache.pickle'
    inputs = []
    files = sorted(directory.glob('*-latest.osm.pbf'), key=lambda p: (p.name != 'maryland-latest.osm.pbf', p.name))
    if len(files) != 6:
        raise ValueError('Supply Maryland and all five adjoining-state/DC OSM extracts')
    for file in files:
        digest = hashlib.file_digest(file.open('rb'), 'sha256').hexdigest()
        with osmium.io.Reader(str(file)) as reader:
            header = reader.header()
            timestamp = header.get('osmosis_replication_timestamp')
        inputs.append({'file': file.name, 'url': f'https://download.geofabrik.de/north-america/us/{file.name}', 'sha256': digest, 'bytes': file.stat().st_size, 'dataTimestamp': timestamp})
    key = hashlib.sha256(canonical(inputs)).hexdigest()
    if cache.exists():
        stored = pickle.loads(cache.read_bytes())
        if stored['key'] == key:
            roads = Roads()
            roads.ways, roads.blocked = stored['ways'], stored['blocked']
            return roads, inputs
    roads = Roads()
    for file in files:
        start = time.monotonic()
        roads.apply_file(str(file), locations=True, idx='flex_mem')
        print(f'{file.name}: {len(roads.ways)} retained roads ({time.monotonic()-start:.1f}s)', flush=True)
    cache.write_bytes(pickle.dumps({'key': key, 'ways': roads.ways, 'blocked': roads.blocked}, protocol=5))
    return roads, inputs


def contract_segments(segments: list[tuple], protected: set[int]) -> list[tuple]:
    """Contract degree-two continuations, preserving directions and exact length."""
    adjacent: dict[int, list[int]] = {}
    for i, edge in enumerate(segments):
        adjacent.setdefault(edge[0], []).append(i)
        adjacent.setdefault(edge[1], []).append(i)
    junctions = {node for node, edges in adjacent.items() if len(edges) != 2} | protected
    visited: set[int] = set()
    result = []
    for start in sorted(junctions & adjacent.keys()):
        for first in adjacent[start]:
            if first in visited:
                continue
            node, edge_id = start, first
            coords, labels, length = [], [], 0.0
            traversed = []
            forward, backward = True, True
            while True:
                visited.add(edge_id)
                traversed.append(edge_id)
                u, v, miles, points, name, direction, _ = segments[edge_id]
                reversed_edge = node == v
                next_node = u if reversed_edge else v
                part = list(reversed(points)) if reversed_edge else points
                coords.extend(part if not coords else part[1:])
                length += miles
                if name not in labels:
                    labels.append(name)
                orientation = -direction if reversed_edge else direction
                forward &= orientation >= 0
                backward &= orientation <= 0
                node = next_node
                if node in junctions:
                    break
                next_edges = [e for e in adjacent[node] if e != edge_id]
                if not next_edges or next_edges[0] in visited:
                    break
                edge_id = next_edges[0]
            if start != node and (forward or backward):
                direction = 0 if forward and backward else 1 if forward else -1
                line = LineString([PROJECTION.transform(*xy) for xy in coords])
                result.append((start, node, round(length, 8), coords, ' / '.join(labels[:3]), direction, line))
            else:
                # Opposed one-way links and a cycle returning to a protected
                # attachment must retain their original connectivity/state.
                result.extend(segments[i] for i in traversed)
    # Pure isolated cycles have no junction; retain their road context unchanged.
    result.extend(edge for i, edge in enumerate(segments) if i not in visited)
    print(f'Degree-two contraction: {len(segments)} physical segments → {len(result)} corridors', flush=True)
    return result


def prune_empty_branches(segments: list[tuple], protected: set[int]) -> list[tuple]:
    """Remove leaf branches containing no neighborhood or facility attachment."""
    adjacent: dict[int, set[int]] = {}
    for i, edge in enumerate(segments):
        adjacent.setdefault(edge[0], set()).add(i)
        adjacent.setdefault(edge[1], set()).add(i)
    queue = deque(node for node, edges in adjacent.items() if len(edges) == 1 and node not in protected)
    removed: set[int] = set()
    while queue:
        node = queue.popleft()
        if node in protected or len(adjacent[node]) != 1:
            continue
        edge_id = next(iter(adjacent[node]))
        removed.add(edge_id)
        u, v = segments[edge_id][:2]
        for endpoint in (u, v):
            adjacent[endpoint].discard(edge_id)
            if len(adjacent[endpoint]) == 1 and endpoint not in protected:
                queue.append(endpoint)
    result = [edge for i, edge in enumerate(segments) if i not in removed]
    print(f'No-demand leaf pruning: {len(segments)} → {len(result)} physical segments', flush=True)
    return result


def county_node_ids(local_segments: list[tuple], protected: set[int], coordinates: dict[int, tuple[float, float]], region) -> list[int]:
    """Keep matched isolated attachments as well as corridor endpoints."""
    isolated = {n for n in protected if region.covers(Point(*PROJECTION.transform(*coordinates[n])))}
    return sorted({s[0] for s in local_segments} | {s[1] for s in local_segments} | isolated)


def build(directory: Path, release: Path) -> None:
    catalog = json.loads((release / 'catalog.json').read_text())
    areas = json.loads((release / 'areas.json').read_text())
    facilities = json.loads((release / 'facilities.json').read_text())
    # Support the response envelope as well as the publication list.
    areas = areas.get('areas') if isinstance(areas, dict) else areas
    facilities = facilities.get('facilities') if isinstance(facilities, dict) else facilities
    roads, inputs = collect(directory)
    counties = county_shapes()
    inverse = Transformer.from_crs(26918, 4326, always_xy=True)
    envelope = prep(transform(unary_union(list(counties.values())), inverse.transform, interleaved=False))
    roads.ways = {key: way for key, way in roads.ways.items() if envelope.intersects(LineString(way[1]))}
    print(f'{len(roads.ways)} motor roads intersect the complete Maryland study buffers', flush=True)
    coordinates: dict[int, tuple[float, float]] = {}
    usage: Counter[int] = Counter()
    for ids, coords, _, _ in roads.ways.values():
        coordinates.update(zip(ids, coords))
        usage.update(set(ids))
    node_ids = sorted(set(coordinates) - roads.blocked)
    projected = [Point(*PROJECTION.transform(*coordinates[n])) for n in node_ids]
    tree = STRtree(projected)
    attachments: dict[str, int | None] = {}
    for item in areas + facilities:
        if item.get('level') and item['level'] != 'blockGroup':
            continue
        point = Point(*PROJECTION.transform(item['lon'], item['lat']))
        index = int(tree.nearest(point))
        attachments[item.get('geoid', item.get('id'))] = node_ids[index] if point.distance(projected[index]) <= SNAP_METERS else None
    retained = {n for n, count in usage.items() if count > 1} | {n for n in attachments.values() if n is not None}
    segments = []
    for way_id, (ids, coords, name, direction) in sorted(roads.ways.items()):
        start = 0
        for end in range(1, len(ids)):
            if end != len(ids)-1 and ids[end] not in retained and ids[end] not in roads.blocked:
                continue
            part = coords[start:end+1]
            if ids[start] not in roads.blocked and ids[end] not in roads.blocked:
                line = LineString([PROJECTION.transform(*xy) for xy in part])
                length = line.length / 1609.344
                if length > .000001 and ids[start] != ids[end]:
                    segments.append((ids[start], ids[end], round(length, 8), part, name, direction, line))
            start = end
    original_endpoints = {s[0] for s in segments} | {s[1] for s in segments}
    protected = {n for n in attachments.values() if n is not None and n in original_endpoints}
    segments = contract_segments(prune_empty_branches(segments, protected), protected)
    segment_tree = STRtree([s[6] for s in segments])
    output = release / 'graphs'
    output.mkdir(exist_ok=True)
    summaries = []
    for county, region in sorted(counties.items()):
        local = [segments[int(i)] for i in sorted(segment_tree.query(region, predicate='intersects'))]
        # A component with a single protected point can prune down to that
        # isolated point. Retain it so disconnected never becomes unmatched.
        ids = county_node_ids(local, protected, coordinates, region)
        index = {n: i for i, n in enumerate(ids)}
        edges, shapes, names = [], [], []
        for u, v, length, coords, name, direction, _ in local:
            rounded = [[round(x, 7), round(y, 7)] for x, y in coords]
            if direction >= 0:
                edges.append([index[u], index[v], length]); shapes.append(rounded); names.append(name)
            if direction <= 0:
                edges.append([index[v], index[u], length]); shapes.append(list(reversed(rounded))); names.append(name)
        graph = {
            'version': 1, 'revision': '', 'dataRevision': catalog['revision'], 'county': county,
            'nodes': [[round(x, 7), round(y, 7)] for n in ids for x, y in [coordinates[n]]],
            'edges': edges, 'shapes': shapes, 'names': names,
            'areaNodes': {a['geoid']: index.get(attachments.get(a['geoid'])) for a in areas if a['level'] == 'blockGroup' and a['county'] == county},
            'facilityNodes': {f['id']: index.get(attachments.get(f['id'])) for f in facilities},
            'sourceId': 'osm-roads',
            'limitations': ['Motor-road distance approximation; turn restrictions, traffic, schedules and travel time are not modeled.', 'Neighborhoods use Census internal representative points snapped to an existing road node within 500 meters.', 'Roads have a 17 km county buffer; all ten-road-mile coverage searches remain inside this buffer.', 'Grade-separated crossings connect only where OSM has a shared node.', 'Degree-two continuations and leaf branches without neighborhood or facility attachments are reduced for browser delivery. Hypothetical locations snap to a retained node within 500 meters.'],
        }
        graph['revision'] = 'roads-' + hashlib.sha256(canonical(graph)).hexdigest()[:16]
        blob = canonical(graph)
        (output / f'{county}.json').write_bytes(blob)
        summaries.append({'county': county, 'revision': graph['revision'], 'nodes': len(ids), 'edges': len(edges), 'bytes': len(blob), 'matchedAreas': sum(n is not None for n in graph['areaNodes'].values()), 'areas': len(graph['areaNodes'])})
        print(json.dumps(summaries[-1]), flush=True)
    manifest = {'version': 1, 'sources': inputs, 'attribution': '© OpenStreetMap contributors, ODbL 1.0. Derived graph data available with this release.', 'bufferMeters': BUFFER_METERS, 'snapMeters': SNAP_METERS, 'dataRevision': catalog['revision'], 'graphs': summaries}
    (release / 'roads-manifest.json').write_bytes(canonical(manifest))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, default=ROOT / 'artifacts/roads')
    parser.add_argument('--release', type=Path, default=ROOT / 'data/outreach')
    args = parser.parse_args()
    build(args.inputs, args.release)
