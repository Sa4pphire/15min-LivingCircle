"""Clip editor snapshots without inventing connections, then publish a region ZIP."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
import tempfile
import zipfile

from .manual_graph_edits import make_manual_edits, validate_editor_graph
from .region_package import RegionPackage, load_region, network_bounds, refresh_manifest, sha256
from .walking_types import normalize_graph


def _inside(point, bounds):
    return bounds['minX'] <= point[0] <= bounds['maxX'] and bounds['minY'] <= point[1] <= bounds['maxY']


def _segment(a, b, bounds):
    dx, dy = b[0] - a[0], b[1] - a[1]
    low, high = 0.0, 1.0
    for p, q in ((-dx, a[0] - bounds['minX']), (dx, bounds['maxX'] - a[0]),
                 (-dy, a[1] - bounds['minY']), (dy, bounds['maxY'] - a[1])):
        if abs(p) < 1e-15:
            if q < 0:
                return None
        elif p < 0:
            low = max(low, q / p)
        else:
            high = min(high, q / p)
        if low > high:
            return None
    def point(t):
        return [max(bounds['minX'], min(bounds['maxX'], a[0] + t * dx)),
                max(bounds['minY'], min(bounds['maxY'], a[1] + t * dy))]
    return low, high, point(low), point(high)


def clip_path(path, bounds, *, minimum_length=0.05):
    runs, offset = [], 0.0
    for a, b in zip(path, path[1:]):
        length = math.dist(a, b)
        part = _segment(a, b, bounds)
        if part and length * (part[1] - part[0]) > 1e-9:
            low, high, start, end = part
            begin, finish = offset + low * length, offset + high * length
            if runs and abs(runs[-1]['end'] - begin) < 1e-8:
                runs[-1]['path'].append(end)
                runs[-1]['end'] = finish
            else:
                runs.append({'path': [start, end], 'start': begin, 'end': finish})
        offset += length
    return [run for run in runs if run['end'] - run['start'] >= minimum_length], offset


def _identity(prefix, edge_id, distance, bounds):
    data = json.dumps([edge_id, round(distance, 8), bounds], sort_keys=True, separators=(',', ':'))
    return prefix + hashlib.sha256(data.encode()).hexdigest()[:24]


def _point_on_path(point, path):
    for a, b in zip(path, path[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        denominator = dx * dx + dy * dy
        t = max(0, min(1, ((point[0]-a[0])*dx + (point[1]-a[1])*dy) / denominator)) if denominator else 0
        if math.dist(point, [a[0]+t*dx, a[1]+t*dy]) <= 1e-6:
            return True
    return False


def crop_graph(graph, bounds):
    graph = normalize_graph(graph)
    validate_editor_graph(graph)
    if set(bounds) != {'minX', 'maxX', 'minY', 'maxY'} or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in bounds.values()):
        raise ValueError('框选范围坐标无效')
    if bounds['maxX'] - bounds['minX'] < 10 or bounds['maxY'] - bounds['minY'] < 10:
        raise ValueError('框选区域的宽和高至少需要 10 米')
    coverage = network_bounds(graph)
    if not (_inside([bounds['minX'], bounds['minY']], coverage) and
            _inside([bounds['maxX'], bounds['maxY']], coverage)):
        raise ValueError('框选区域必须位于当前路网数据范围内')
    original_nodes = {n['id']: n for n in graph['nodes']}
    nodes, edges, edge_map = {}, [], {}
    boundary_nodes, partial_connections, clipped_count = [], 0, 0
    for edge in graph['edges']:
        path = edge['pathMeters']
        if edge['kind'] != 'walkway' and not all(_inside(p, bounds) for p in path):
            if clip_path(path, bounds)[0]:
                partial_connections += 1
            continue
        runs, length = clip_path(path, bounds)
        for run in runs:
            complete = abs(run['start']) < 1e-8 and abs(run['end'] - length) < 1e-8
            item = deepcopy(edge)
            if not complete:
                item['id'] = _identity('crop-edge:', edge['id'], run['start'], bounds)
                item['sourceEdgeIds'] = list(dict.fromkeys([*edge.get('sourceEdgeIds', []), edge['id']]))
                item['packageClip'] = {'sourceEdgeId': edge['id'], 'startMeters': run['start'], 'endMeters': run['end']}
                clipped_count += 1
            item['pathMeters'] = deepcopy(path) if complete else run['path']
            for role, point, position, original, retained in (
                ('from', item['pathMeters'][0], run['start'], edge['from'], abs(run['start']) < 1e-8),
                ('to', item['pathMeters'][-1], run['end'], edge['to'], abs(run['end'] - length) < 1e-8)):
                if retained:
                    node_id = original
                    nodes[node_id] = deepcopy(original_nodes[node_id])
                    # Retained shared nodes must meet C++'s exact endpoint tolerance.
                    point[:] = [nodes[node_id]['xMeters'], nodes[node_id]['yMeters']]
                else:
                    node_id = _identity('crop-node:', edge['id'], position, bounds)
                    nodes[node_id] = {'id': node_id, 'xMeters': point[0], 'yMeters': point[1],
                        'sourceEdgeId': edge['id'], 'packageBoundary': True,
                        'verificationStatus': 'data_extent_clip'}
                    boundary_nodes.append(node_id)
                item[role] = node_id
            edges.append(item)
            edge_map.setdefault(edge['id'], []).append(item)
    facilities = []
    for facility in graph.get('facilities', []):
        entrances = []
        for entrance in facility.get('entrances', [facility]):
            point = entrance.get('accessPointMeters')
            for edge in edge_map.get(entrance.get('accessEdgeId'), []):
                if point and _inside(point, bounds) and _point_on_path(point, edge['pathMeters']):
                    entrances.append({**deepcopy(entrance), 'accessEdgeId': edge['id']})
                    break
        if entrances:
            item = deepcopy(facility)
            if 'entrances' in facility:
                item['entrances'] = entrances
            else:
                item['accessEdgeId'] = entrances[0]['accessEdgeId']
            facilities.append(item)
    excluded = {'nodes', 'edges', 'facilities', 'sourceGraph', 'localExperiment', 'originEdgeId', 'authoringWorkspace', 'draftBoundsMeters',
                'originMeters', 'demoCenterWgs84', 'demoCenterBd09', 'supportedCenterPolygonMeters'}
    cropped = {key: deepcopy(value) for key, value in graph.items() if key not in excluded}
    cropped.update(nodes=list(nodes.values()), edges=edges, facilities=facilities,
                   selectionSource='network-path-extent')
    if not edges:
        raise ValueError('框选区域内没有可打包的路段，请扩大或移动选框')
    cropped['supportedCenterBoundsMeters'] = network_bounds(cropped)
    actual = cropped['supportedCenterBoundsMeters']
    if actual['maxX'] - actual['minX'] < 1 or actual['maxY'] - actual['minY'] < 1:
        raise ValueError('选中的路网范围过窄，无法生成可用地图区域')
    cropped['sourceGraph'] = {'name': 'editor-region-snapshot', 'crop': {
        'requestedBoundsMeters': dict(bounds), 'boundaryNodeIds': sorted(set(boundary_nodes)),
        'clippedWalkwayCount': clipped_count, 'excludedPartialConnections': partial_connections}}
    audit = validate_editor_graph(cropped)
    return cropped, {'nodeCount': len(nodes), 'edgeCount': len(edges), 'facilityCount': len(facilities),
                     'boundaryNodeCount': len(set(boundary_nodes)), 'clippedWalkwayCount': clipped_count,
                     'excludedPartialConnections': partial_connections, 'boundsMeters': actual,
                     'componentCount': audit['componentCount']}


def _context(context, bounds):
    display = {'minX': bounds['minX'], 'maxX': bounds['maxX'], 'minY': -bounds['maxY'], 'maxY': -bounds['minY']}
    features = []
    for feature in context['features']:
        if re.search(r'[a-df-kno-yA-DF-KNO-Y]', feature['d']):
            # Keep unfamiliar curved paths rather than guessing their visible bounds.
            features.append(deepcopy(feature)); continue
        numbers = [float(n) for n in re.findall(r'[-+]?(?:\d*\.?\d+)(?:[eE][+-]?\d+)?', feature['d'])]
        if len(numbers) < 4:
            continue
        xs, ys = numbers[::2], numbers[1::2]
        if max(xs) >= display['minX'] and min(xs) <= display['maxX'] and max(ys) >= display['minY'] and min(ys) <= display['maxY']:
            features.append(deepcopy(feature))
    key = 'originWgs84' if 'originWgs84' in context else 'originBd09'
    return {**deepcopy(context), 'schemaVersion': context.get('schemaVersion', 1), key: context[key], 'features': features}


def _alignment(asset, bounds):
    result = deepcopy(asset)
    grid = result['alignment']
    if grid.get('kind') == 'bd09_local_meters':
        return result
    step, minimum, columns, rows = grid['stepMeters'], grid['minLocalMeters'], grid['columns'], grid['rows']
    west, east = (bounds['minX'] - minimum[0]) / step, (bounds['maxX'] - minimum[0]) / step
    north, south = (-bounds['maxY'] - minimum[1]) / step, (-bounds['minY'] - minimum[1]) / step
    if west < -1e-8 or north < -1e-8 or east > columns - 1 + 1e-8 or south > rows - 1 + 1e-8:
        raise ValueError('选区超出已有百度坐标校准网格，请先补齐校准数据')
    left, top = min(columns-2, max(0, math.floor(west))), min(rows-2, max(0, math.floor(north)))
    right, bottom = min(columns-1, max(left+1, math.ceil(east))), min(rows-1, max(top+1, math.ceil(south)))
    anchors = grid['pointsBd09']
    grid.update(columns=right-left+1, rows=bottom-top+1,
                minLocalMeters=[minimum[0]+left*step, minimum[1]+top*step],
                pointsBd09=[anchors[y*columns+x] for y in range(top, bottom+1) for x in range(left, right+1)])
    return result


def region_zip(region: RegionPackage) -> bytes:
    selected = {'manifest.json', *region.manifest['files'].values()}
    if (region.root / 'README.md').is_file():
        selected.add('README.md')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for relative in sorted(selected):
            archive.writestr(f"{region.manifest['id']}/{relative}", (region.root / relative).read_bytes())
    return buffer.getvalue()


def create_region(parent: RegionPackage, graph: dict, summary: dict, identity: str,
                  name: str, version: str, root: Path) -> dict:
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}', identity) or not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('区域标识或版本格式无效')
    name = name.strip()
    if not name or len(name) > 80 or '\n' in name or '\r' in name:
        raise ValueError('请输入有效的区域名称')
    regions = root / 'data/regions'
    regions.mkdir(parents=True, exist_ok=True)
    destination = regions / identity
    if destination.exists():
        raise FileExistsError('这个区域标识已经存在，请更换标识；已有区域包不会被覆盖')
    graph = deepcopy(graph)
    graph['regionId'] = identity
    lineage = {'regionId': parent.manifest['id'], 'networkRevision': sha256(parent.file('network')),
               'createdAt': datetime.now(timezone.utc).isoformat(),
               'includesEditorSnapshot': True}
    graph['sourceGraph']['parentRegion'] = lineage
    annotations = {'schemaVersion': 1, 'coordinateSystem': 'engine-local-meters',
                   'authoringMode': 'engine-snapshot', 'crossings': [], 'junctions': [],
                   'manualGraphEdits': make_manual_edits(graph, graph)}
    manifest = {key: deepcopy(value) for key, value in parent.manifest.items() if key not in ('files', 'sha256')}
    manifest.update(id=identity, name=name, version=version, status='ready', authoringMode='engine-snapshot', parentRegion=lineage,
        files={'network': 'network.json', 'context': 'context.json', 'alignment': 'alignment.bd09.json',
               'sourceGraph': 'source/engine-base.json', 'annotations': 'source/annotations.json'},
        facilityData={'annotatedCount': len(graph['facilities']), 'candidatePois': 'optional-local-cache-not-bundled'})
    context = _context(json.loads(parent.file('context').read_text('utf-8')), summary['boundsMeters'])
    alignment = _alignment(json.loads(parent.file('alignment').read_text('utf-8')), summary['boundsMeters'])
    with tempfile.TemporaryDirectory(prefix='.package-', dir=regions) as folder:
        working = Path(folder) / identity
        (working / 'source').mkdir(parents=True)
        values = {'manifest.json': manifest, 'network.json': graph, 'context.json': context,
                  'alignment.bd09.json': alignment, 'source/engine-base.json': graph,
                  'source/annotations.json': annotations}
        for relative, value in values.items():
            (working / relative).write_text(json.dumps(value, ensure_ascii=False, allow_nan=False,
                separators=(',', ':')), encoding='utf-8')
        (working / 'README.md').write_text(f'# {name}\n\n版本 {version}。由 {parent.manifest["name"]} 的编辑器画布裁剪。\n\n'
            '局部米制原点与父区域一致。过街与转弯仅保留完整连接；裁剪端点不建立隐式连接。\n'
            '范围从 network.json 派生，不需要 boundary.geojson。边缘可达结果可能被数据截断。\n'
            '本包包含可回放的引擎源图和人工修改记录，可在路网编辑器继续处理。\n'
            'POI 缓存和 API 密钥未包含。地图显示不代表已核实通行权限。\n', encoding='utf-8')
        refresh_manifest(load_region(working, verify=False))
        load_region(working).public_metadata()
        working.rename(destination)
    region = load_region(destination)
    archive = root / 'data/region-packages' / f'{identity}-{version}.zip'
    archive.parent.mkdir(parents=True, exist_ok=True)
    temporary = archive.with_suffix('.zip.tmp')
    temporary.write_bytes(region_zip(region))
    temporary.replace(archive)
    return {**summary, 'id': identity, 'name': name, 'version': version, 'archiveName': archive.name,
            'archiveBytes': archive.stat().st_size,
            'downloadUrl': f'/api/v1/network-editor/regions/{identity}/download'}
