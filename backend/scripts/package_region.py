"""Create/check/archive a portable region from the existing, edited road model.

Includes normalized region POI snapshots from existing local caches.
No API calls, boundary.geojson, credentials, tests or generated SVG files.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app.region_package import load_region, network_bounds, refresh_manifest
from app.network_audit import audit_network
from app.settings import settings
from bundle_region_pois import bundle_region


def package_current(region_id: str, name: str, version: str) -> Path:
    directory = ROOT / 'data/regions' / region_id
    if directory.exists():
        raise ValueError('区域包已存在；请用 --archive 检查并打包，避免覆盖已编辑区域')
    source_network = ROOT / 'data/networks/synthetic-preview.json'
    graph = json.loads(source_network.read_text(encoding='utf-8'))
    audit_network(graph)
    bounds = network_bounds(graph)
    graph['supportedCenterBoundsMeters'] = bounds
    graph.pop('supportedCenterPolygonMeters', None)
    graph['selectionSource'] = 'network-path-extent'
    graph['regionId'] = region_id
    # Preserve every existing modeled node, edge, facility and source annotation.
    directory.mkdir(parents=True)
    (directory / 'source').mkdir()
    (directory / 'network.json').write_text(json.dumps(graph, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False), encoding='utf-8')
    copies = {
        'context': ('frontend/src/data/demoContext.extended.wgs84.json', 'context.json'),
        'sourceGraph': ('frontend/src/data/demoRoadGraph.local.json', 'source/road-graph.json'),
        'annotations': ('data/networks/synthetic-preview.annotations.json', 'source/annotations.json'),
    }
    files = {'network': 'network.json', 'alignment': 'alignment.bd09.json'}
    for key, (source, target) in copies.items():
        shutil.copyfile(ROOT / source, directory / target)
        files[key] = target
    asset = json.loads((ROOT / 'frontend/src/data/demoMap.bd09.json').read_text(encoding='utf-8'))
    # Retain official grid anchors/provenance; the old four-road polygon is unused.
    alignment = {key: asset[key] for key in ('schemaVersion', 'coordType', 'alignment', 'conversion')}
    alignment['input'] = {'originWgs84': asset['input']['originWgs84']}
    (directory / 'alignment.bd09.json').write_text(json.dumps(alignment, ensure_ascii=False,
        separators=(',', ':')), encoding='utf-8')
    manifest = {'packageSchemaVersion': 1, 'id': region_id, 'name': name, 'version': version,
                'synthetic': graph.get('synthetic', False), 'files': files,
                'selectionSource': 'network-path-extent',
                'coordinateSystem': {'engine': 'local-meters-east-north',
                                     'display': 'local-meters-east-south', 'geographic': 'wgs84ll'},
                'facilityData': {'annotatedCount': len(graph.get('facilities', [])),
                                'candidatePois': 'optional-local-cache-not-bundled'},
                'source': {'provider': 'OpenStreetMap contributors', 'license': 'ODbL 1.0',
                           'url': 'https://www.openstreetmap.org/copyright'},
                'coverageNotice': '路网外接范围仅表示现有数据范围；边缘可达结果可能被数据截断。'}
    (directory / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    refresh_manifest(load_region(directory, verify=False))
    return directory


def check(directory: Path) -> dict:
    region = load_region(directory)
    graph = region.graph()
    audit = audit_network(graph)
    from app.region_package import sha256
    for key, expected in region.manifest.get('sha256', {}).items():
        if sha256(region.file(key)) != expected:
            raise ValueError(f'区域文件校验和不一致: {key}')
    context = json.loads(region.file('context').read_text(encoding='utf-8'))
    metadata = region.public_metadata()
    origin_key = 'originWgs84' if metadata['geographicCoordType'] == 'wgs84ll' else 'originBd09'
    if context[origin_key] != metadata[origin_key]:
        raise ValueError('底图与路网原点不一致')
    alignment = json.loads(region.file('alignment').read_text(encoding='utf-8'))
    if alignment['input'][origin_key] != metadata[origin_key]:
        raise ValueError('校准网格与路网原点不一致')
    grid = alignment['alignment']
    if grid.get('kind') == 'bd09_local_meters':
        return {'id': metadata['id'], 'version': metadata['version'],
                'nodes': len(graph['nodes']), 'edges': len(graph['edges']),
                'boundsMeters': metadata['boundsMeters'], 'status': metadata['status'],
                'boundaryFileRequired': False, 'audit': audit}
    low = grid['minLocalMeters']
    high = [low[0] + (grid['columns'] - 1) * grid['stepMeters'],
            low[1] + (grid['rows'] - 1) * grid['stepMeters']]
    bounds = metadata['boundsMeters']
    if not (low[0] <= bounds['minX'] < bounds['maxX'] <= high[0] and
            low[1] <= -bounds['maxY'] < -bounds['minY'] <= high[1]):
        raise ValueError('当前路网范围超出已校准网格')
    return {'id': metadata['id'], 'version': metadata['version'],
            'nodes': len(graph['nodes']), 'edges': len(graph['edges']),
            'boundsMeters': bounds, 'boundaryFileRequired': False, 'audit': audit}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--region-id', default='shanghai-new-jiangwan')
    parser.add_argument('--name', default='新江湾城路网演示区')
    parser.add_argument('--version', default='0.1.0')
    parser.add_argument('--create', action='store_true')
    parser.add_argument('--archive', action='store_true')
    args = parser.parse_args()
    if not args.region_id or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for c in args.region_id):
        raise ValueError('region-id 只能使用小写字母、数字、连字符或下划线')
    directory = (package_current(args.region_id, args.name, args.version) if args.create else
                 ROOT / 'data/regions' / args.region_id)
    if args.create or args.archive:
        bundle_region(load_region(directory), settings.analysis_cache_dir)
    report = check(directory)
    region = load_region(directory)
    report['poiCount'] = region.public_metadata()['poiCount']
    if args.archive:
        target = ROOT / 'data/region-packages' / f"{report['id']}-{report['version']}.zip"
        target.parent.mkdir(parents=True, exist_ok=True)
        region = load_region(directory)
        selected = {'manifest.json', *region.manifest['files'].values()}
        if (directory / 'README.md').is_file():
            selected.add('README.md')
        with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for relative in sorted(selected):
                path = directory / relative
                archive.write(path, path.relative_to(directory.parent).as_posix())
        report['archive'] = str(target.relative_to(ROOT))
        report['archiveBytes'] = target.stat().st_size
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
