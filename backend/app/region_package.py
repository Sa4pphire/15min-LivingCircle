"""Region packages share one walking graph, map frame and authoring source.

The graph's paths define the supported extent. No selection-boundary file is
required. An extent is a data limit, not evidence of complete real-world access.
"""
from dataclasses import dataclass
import hashlib
import json
import math
import re
from pathlib import Path

from .settings import REPO_ROOT, settings


def network_bounds(graph: dict) -> dict[str, float]:
    points = [point for edge in graph['edges'] for point in edge.get('pathMeters', [])]
    if not points:
        if graph.get('authoringWorkspace') is True:
            bounds = graph.get('draftBoundsMeters', {})
            if (set(bounds) == {'minX', 'maxX', 'minY', 'maxY'} and
                    all(not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v) for v in bounds.values()) and
                    bounds['minX'] < bounds['maxX'] and bounds['minY'] < bounds['maxY']):
                return dict(bounds)
        raise ValueError('区域路网没有可显示的路径')
    if any(not isinstance(p, list) or len(p) != 2 or
           any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
               for v in p) for p in points):
        raise ValueError('区域路网路径坐标无效')
    bounds = {'minX': min(p[0] for p in points), 'maxX': max(p[0] for p in points),
              'minY': min(p[1] for p in points), 'maxY': max(p[1] for p in points)}
    if graph.get('authoringWorkspace'):
        for low, high in (('minX','maxX'),('minY','maxY')):
            if bounds[high]-bounds[low] < 1:
                midpoint = (bounds[low]+bounds[high])/2
                bounds[low], bounds[high] = midpoint-0.5, midpoint+0.5
    return bounds


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class RegionPackage:
    root: Path
    manifest: dict

    def file(self, key: str) -> Path:
        relative = self.manifest['files'].get(key)
        if not isinstance(relative, str) or not relative:
            raise ValueError(f'区域包缺少文件: {key}')
        path = (self.root / relative).resolve()
        if Path(relative).is_absolute() or not path.is_relative_to(self.root.resolve()):
            raise ValueError('区域文件必须位于区域包目录内')
        if path.suffix.lower() != '.json':
            raise ValueError('区域包数据文件必须为 JSON')
        if not path.is_file():
            raise ValueError(f'区域文件不存在: {key}')
        return path

    def graph(self) -> dict:
        graph = json.loads(self.file('network').read_text(encoding='utf-8'))
        draft = self.manifest.get('status') == 'draft' and graph.get('authoringWorkspace') is True
        if (graph.get('schemaVersion') != 2 or not isinstance(graph.get('nodes'), list) or
                not isinstance(graph.get('edges'), list) or
                (not draft and (not graph['nodes'] or not graph['edges']))):
            raise ValueError('区域包需要 v2 步行路网')
        return graph

    def public_metadata(self, *, asset_base: str = '/api/v1/region/assets') -> dict:
        graph = self.graph()
        bounds = network_bounds(graph)
        origin_key = 'originWgs84' if 'originWgs84' in graph else 'originBd09'
        origin = graph.get(origin_key)
        if not isinstance(origin, dict) or not all(
                not isinstance(origin.get(k), bool) and
                isinstance(origin.get(k), (int, float)) and math.isfinite(origin[k])
                for k in ('lng', 'lat')):
            raise ValueError('当前区域包需要明确的地理坐标原点')
        if not (-180 <= origin['lng'] <= 180 and -85 < origin['lat'] < 85):
            raise ValueError('区域原点超出支持的地理坐标范围')
        revision = sha256(self.file('network'))
        assets = {key: f'{asset_base}/{key}?v={sha256(self.file(key))}'
                  for key in ('context', 'alignment', 'pois') if key in self.manifest['files']}
        from .region_pois import read_region_pois
        pois = read_region_pois(self)
        return {'id': self.manifest['id'], 'name': self.manifest['name'],
                'source': self.manifest.get('source', {}),
                'version': self.manifest['version'], 'revision': revision,
                'synthetic': graph.get('synthetic', False),
                origin_key: [origin['lng'], origin['lat']],
                'geographicCoordType': 'wgs84ll' if origin_key == 'originWgs84' else 'bd09ll',
                'status': self.manifest.get('status', 'ready'),
                'authoringWorkspace': graph.get('authoringWorkspace', False),
                'workspaceBoundsMeters': graph.get('draftBoundsMeters'),
                'engineAxis': 'east-north', 'displayAxis': 'east-south',
                'boundsMeters': bounds, 'selectionSource': 'network-path-extent',
                'nodeCount': len(graph['nodes']), 'edgeCount': len(graph['edges']),
                'poiCount': len(pois['items']) if pois is not None else 0,
                'poiDataSource': 'region_package' if pois is not None else 'runtime_cache',
                'assets': assets,
                'coverageNotice': ('新区域草稿，请根据底图绘制步行通道。' if not graph['edges'] else
                    '范围由现有路网派生；边缘结果可能受数据截断影响，通行关系仍待核实。')}


def load_region(root: Path | None = None, *, verify: bool = True) -> RegionPackage:
    directory = root or (REPO_ROOT / 'data/regions' / settings.region_id)
    directory = directory.resolve()
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    if (manifest.get('packageSchemaVersion') != 1 or
            not isinstance(manifest.get('files'), dict) or
            not all(isinstance(manifest.get(k), str) and manifest[k]
                    for k in ('id', 'name', 'version'))):
        raise ValueError('区域包清单无效')
    region = RegionPackage(directory, manifest)
    if (not re.fullmatch(r'[a-z0-9_-]+', manifest['id']) or
            not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', manifest['version'])):
        raise ValueError('区域 ID 或版本格式无效')
    for key in manifest['files']:
        region.file(key)
    if verify:
        hashes = manifest.get('sha256')
        if not isinstance(hashes, dict) or hashes.keys() != manifest['files'].keys():
            raise ValueError('区域清单必须提供全部数据文件的校验和')
        for key, expected in hashes.items():
            if sha256(region.file(key)) != expected:
                raise ValueError(f'区域文件校验和不一致: {key}')
    if root is None and settings.synthetic_network_path.resolve() != region.file('network'):
        raise ValueError('SYNTHETIC_NETWORK_PATH 与 REGION_ID 不一致，请移除旧路径覆盖')
    if 'pois' in manifest['files']:
        from .region_pois import read_region_pois
        read_region_pois(region)
    return region


def load_region_by_id(identity: str) -> RegionPackage:
    if not isinstance(identity, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}', identity):
        raise ValueError('区域标识无效')
    directory = REPO_ROOT / 'data/regions' / identity
    if not directory.resolve().is_relative_to((REPO_ROOT / 'data/regions').resolve()):
        raise ValueError('区域目录超出数据范围')
    region = load_region(directory)
    if region.manifest['id'] != identity:
        raise ValueError('区域目录名与清单标识不一致')
    return region


def list_display_regions() -> dict:
    items = []
    directory = REPO_ROOT / 'data/regions'
    for path in sorted(directory.iterdir()) if directory.is_dir() else []:
        if not path.is_dir() or path.name.startswith('.'):
            continue
        try:
            metadata = load_region_by_id(path.name).public_metadata()
            item = {key: metadata[key] for key in ('id', 'name', 'version', 'nodeCount',
                    'edgeCount', 'boundsMeters', 'synthetic', 'status')}
            item['displayReady'] = metadata['nodeCount'] > 0 and metadata['edgeCount'] > 0
            if not item['displayReady']:
                item['unavailableReason'] = '路网尚未完成，请先在编辑器中绘制并保存。'
            items.append(item)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            items.append({'id': path.name, 'name': path.name, 'displayReady': False,
                          'error': str(exc), 'unavailableReason': '区域包数据异常，请在编辑器中检查。'})
    return {'items': items, 'defaultRegionId': settings.region_id}


def refresh_manifest(region: RegionPackage) -> None:
    manifest = dict(region.manifest)
    graph = json.loads(region.file('network').read_text(encoding='utf-8'))
    if graph.get('authoringWorkspace'):
        manifest['status'] = 'ready' if graph['edges'] else 'draft'
    manifest['sha256'] = {key: sha256(region.file(key)) for key in manifest['files']}
    temporary = region.root / 'manifest.json.tmp'
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(region.root / 'manifest.json')


def validate_region_center(center, region: RegionPackage | None = None) -> None:
    region = region or load_region()
    graph = region.graph()
    bounds = network_bounds(graph)
    origin_key = 'originWgs84' if 'originWgs84' in graph else 'originBd09'
    origin = graph[origin_key]
    if center.coordType == ('wgs84ll' if origin_key == 'originWgs84' else 'bd09ll'):
        point = [(center.lng - origin['lng']) * 111320 * math.cos(math.radians(origin['lat'])),
                 (center.lat - origin['lat']) * 111320]
    elif center.coordType == 'bd09ll' and origin_key == 'originWgs84':
        from .local_alignment import load_grid_frame
        frame = load_grid_frame({'coordType': 'wgs84ll', 'originWgs84': origin}, region.file('alignment'))
        point = frame[0]([center.lng, center.lat]) if frame else None
    else:
        point = None
    if point is None or not (bounds['minX'] <= point[0] <= bounds['maxX'] and
                             bounds['minY'] <= point[1] <= bounds['maxY']):
        raise ValueError('起点超出现有路网数据范围')
