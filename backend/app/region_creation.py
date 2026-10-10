"""Create an empty, replayable editor workspace at a native Baidu location."""
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import tempfile
from uuid import uuid4

from .manual_graph_edits import make_manual_edits
from .region_package import load_region, refresh_manifest
from .region_pois import poi_document


def create_blank_region(location: str, name: str, center: list[float], root: Path) -> dict:
    location, name = location.strip(), name.strip()
    if (not location or not name or '\n' in name or '\r' in name or
            len(center) != 2 or any(isinstance(v, bool) or not isinstance(v, (int,float)) or not math.isfinite(v) for v in center) or
            not (-180 <= center[0] <= 180 and -85 < center[1] < 85)):
        raise ValueError('地点、名称或定位坐标无效')
    identity = 'region-' + uuid4().hex[:12]
    bounds = {'minX': -20000, 'maxX': 20000, 'minY': -20000, 'maxY': 20000}
    graph = {'schemaVersion': 2, 'synthetic': True, 'authoringWorkspace': True,
        'regionId': identity, 'originBd09': {'lng': center[0], 'lat': center[1]},
        'selectionSource': 'network-path-extent', 'draftBoundsMeters': bounds,
        'supportedCenterBoundsMeters': bounds, 'nodes': [], 'edges': [], 'facilities': [],
        'serviceCategories': [], 'sourceGraph': {'name': 'user-created-region',
            'verificationStatus': 'user_edited_unverified', 'locationQuery': location}}
    annotations = {'schemaVersion': 1, 'coordinateSystem': 'engine-local-meters',
        'authoringMode': 'engine-snapshot', 'crossings': [], 'junctions': [],
        'manualGraphEdits': make_manual_edits(graph, graph)}
    alignment = {'schemaVersion': 1, 'coordType': 'bd09ll', 'input': {'originBd09': center},
        'alignment': {'kind': 'bd09_local_meters', 'localAxis': 'east-south', 'originBd09': center},
        'conversion': {'provider': 'Baidu JSAPI geocoder', 'model': 'native_bd09_local_meters',
            'accuracy': 'local_tangent_plane_approximation',
            'documentationUrl': 'https://lbsyun.baidu.com/cms/jsapi/reference/jsapi_webgl_1_0.html'}}
    manifest = {'packageSchemaVersion': 1, 'id': identity, 'name': name, 'version': '0.1.0',
        'status': 'draft', 'synthetic': True, 'authoringMode': 'engine-snapshot',
        'createdAt': datetime.now(timezone.utc).isoformat(),
        'location': {'query': location, 'centerBd09': center, 'provider': 'Baidu JSAPI geocoder'},
        'selectionSource': 'network-path-extent',
        'coordinateSystem': {'engine': 'local-meters-east-north', 'display': 'local-meters-east-south', 'geographic': 'bd09ll'},
        'files': {'network': 'network.json', 'context': 'context.json', 'alignment': 'alignment.bd09.json',
                  'sourceGraph': 'source/engine-base.json', 'annotations': 'source/annotations.json', 'pois': 'pois.json'},
        'facilityData': {'annotatedCount': 0, 'candidatePois': 'bundled-snapshot', 'candidateCount': 0},
        'source': {'provider': '用户绘制', 'verificationStatus': 'user_edited_unverified'}}
    directory = root / 'data/regions'
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.new-region-', dir=directory) as folder:
        working = Path(folder) / identity
        (working/'source').mkdir(parents=True)
        values = {'manifest.json': manifest, 'network.json': graph,
            'context.json': {'schemaVersion': 1, 'originBd09': center, 'features': []},
            'alignment.bd09.json': alignment, 'source/engine-base.json': graph,
            'source/annotations.json': annotations, 'pois.json': poi_document(identity, graph, [])}
        for relative, value in values.items():
            (working/relative).write_text(json.dumps(value, ensure_ascii=False, allow_nan=False,
                separators=(',',':')), encoding='utf-8')
        (working/'README.md').write_text(f'# {name}\n\n地点：{location}。当前为空白路网草稿。\n'
            '在路网编辑器里新增节点并连接步行通道，保存后可以继续编辑和框选导出。\n'
            '使用原生 BD-09 原点和近似局部米制平面，不包含 WGS-84 校准网格。\n'
            '草稿工作范围只供编辑定位；有路段后数据范围从路网派生。\n'
            'pois.json 初始为空，可从本机缓存导入。未核实实际通行权限；API 密钥未打包。\n', encoding='utf-8')
        refresh_manifest(load_region(working, verify=False))
        load_region(working).public_metadata()
        working.rename(directory/identity)
    return {'id': identity, 'name': name, 'version': '0.1.0', 'status': 'draft'}
