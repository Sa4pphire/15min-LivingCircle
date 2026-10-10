"""Portable, normalized POI snapshots. No credentials or online requests."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path

from .local_alignment import load_grid_frame
from .poi_categories import CATEGORIES, cached_categories

METERS_PER_DEGREE = 111320.0


def _point(value, *, geographic=False):
    return (isinstance(value, list) and len(value) == 2 and
            all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in value) and
            (not geographic or (-180 <= value[0] <= 180 and -85 < value[1] < 85)))


def graph_origin(graph):
    key = 'originWgs84' if 'originWgs84' in graph else 'originBd09'
    origin = graph[key]
    return key, [origin['lng'], origin['lat']]


def project_bd09(region):
    graph = region.graph()
    key, origin = graph_origin(graph)
    if key == 'originWgs84':
        frame = load_grid_frame({'coordType': 'wgs84ll', key: graph[key]}, region.file('alignment'))
        if frame is None:
            raise ValueError('区域缺少可离线使用的 POI 坐标校准网格')
        return frame[0]
    def project(point):
        return [(point[0]-origin[0])*METERS_PER_DEGREE*math.cos(math.radians(origin[1])),
                (point[1]-origin[1])*METERS_PER_DEGREE]
    return project


def validate_document(document, region):
    key, origin = graph_origin(region.graph())
    if (not isinstance(document, dict) or document.get('schemaVersion') != 1 or
            document.get('regionId') != region.manifest['id'] or document.get('coordType') != 'bd09ll' or
            document.get('engineAxis') != 'east-north' or document.get(key) != origin or
            not isinstance(document.get('items'), list)):
        raise ValueError('区域 POI 数据标识、坐标或格式无效')
    seen = set()
    for item in document['items']:
        if not isinstance(item, dict):
            raise ValueError('区域 POI 记录无效')
        uid, categories = item.get('uid'), item.get('categories')
        if (not isinstance(uid, str) or not uid or uid in seen or
                not isinstance(item.get('name'), str) or not item['name'] or
                not isinstance(item.get('address'), str) or
                not isinstance(categories, list) or not categories or
                any(category not in CATEGORIES for category in categories) or
                len(set(categories)) != len(categories) or item.get('category') != categories[0] or
                item.get('coordType') != 'bd09ll' or
                not _point([item.get('lng'), item.get('lat')], geographic=True) or
                not _point(item.get('localPointMeters')) or
                ('navigationPoint' in item and not _point(item['navigationPoint'], geographic=True))):
            raise ValueError('区域 POI 记录的分类、坐标或标识无效')
        seen.add(uid)
    return document


def read_region_pois(region):
    if 'pois' not in region.manifest['files']:
        return None
    return validate_document(json.loads(region.file('pois').read_text(encoding='utf-8')), region)


def _inside(point, bounds):
    return bounds['minX'] <= point[0] <= bounds['maxX'] and bounds['minY'] <= point[1] <= bounds['maxY']


def poi_document(region_id, graph, items, *, source=None):
    key, origin = graph_origin(graph)
    return {'schemaVersion': 1, 'regionId': region_id, 'coordType': 'bd09ll',
            'engineAxis': 'east-north', key: origin,
            'createdAt': datetime.now(timezone.utc).isoformat(),
            'source': source or {'provider': 'Baidu Place API', 'kind': 'local-cache-snapshot'},
            'inventoryVerified': False, 'accessVerified': False,
            'categoryCounts': {category: sum(category in item['categories'] for item in items) for category in CATEGORIES},
            'items': sorted(items, key=lambda item: item['uid'])}


def collect_cached_pois(region, cache_dir: Path, *, graph=None, bounds=None):
    """Merge the package snapshot with successful cached Place pages, by UID.

    Old snapshots remain usable for demos regardless of the request-cache TTL.
    Preserve observation times and never claim a complete facility inventory.
    """
    from .region_package import network_bounds
    graph = graph or region.graph()
    bounds = bounds or network_bounds(graph)
    project = project_bd09(region)
    previous = read_region_pois(region)
    records = {item['uid']: deepcopy(item) for item in (previous or {}).get('items', [])
               if _inside(item['localPointMeters'], bounds)}
    pages, skipped = 0, 0
    for path in sorted(Path(cache_dir).glob('*.json')):
        if len(path.stem) != 64 or any(c not in '0123456789abcdef' for c in path.stem):
            continue
        try:
            envelope = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        payload = envelope.get('payload') if isinstance(envelope, dict) else None
        if not isinstance(payload, dict) or payload.get('status') != 0 or not isinstance(payload.get('results'), list):
            continue
        pages += 1
        fetched = envelope.get('createdAt')
        try:
            observed = datetime.fromisoformat(fetched).astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError):
            observed = None
        for raw in payload['results']:
            if not isinstance(raw, dict):
                continue
            location = raw.get('location') or {}
            if not isinstance(location, dict):
                continue
            point = [location.get('lng'), location.get('lat')]
            categories = cached_categories(raw)
            if not raw.get('uid') or not raw.get('name') or not categories or not _point(point, geographic=True):
                skipped += 1
                continue
            local = project(point)
            if not _point(local) or not _inside(local, bounds):
                continue
            detail = raw.get('detail_info') if isinstance(raw.get('detail_info'), dict) else {}
            record = {'uid': str(raw['uid']), 'name': str(raw['name']), 'address': str(raw.get('address') or ''),
                      'lng': point[0], 'lat': point[1], 'coordType': 'bd09ll', 'source': 'baidu',
                      'localPointMeters': local, 'category': categories[0], 'categories': categories,
                      'tag': str(detail.get('tag') or ''), 'fetchedAt': observed,
                      'matchedKeywords': [word for category in categories for word in CATEGORIES[category]['keywords']
                                          if word in str(raw.get('name', '')) + str(detail.get('tag', ''))]}
            navigation = detail.get('navi_location')
            if isinstance(navigation, dict) and _point([navigation.get('lng'), navigation.get('lat')], geographic=True):
                record['navigationPoint'] = [navigation['lng'], navigation['lat']]
            old = records.get(record['uid'])
            if old:
                combined = [key for key in CATEGORIES if key in old['categories'] or key in categories]
                if (old.get('fetchedAt') or '') > (observed or ''):
                    record = old
                record['categories'], record['category'] = combined, combined[0]
            records[record['uid']] = record
    document = poi_document(region.manifest['id'], graph, list(records.values()))
    document['importSummary'] = {'cachedPagesRead': pages, 'skippedUnclassifiedOrInvalidRecords': skipped}
    return document


def crop_region_pois(parent, graph, region_id, bounds, cache_dir):
    document = collect_cached_pois(parent, cache_dir, graph=graph, bounds=bounds)
    return poi_document(region_id, graph, document['items'], source={
        **document['source'], 'parentRegionId': parent.manifest['id']})


def search_region_pois(region, center, radius, category_ids, *, bounds=None):
    document = read_region_pois(region)
    if document is None:
        return None
    graph = region.graph()
    key, origin = graph_origin(graph)
    if center.coordType == 'bd09ll':
        center_local = project_bd09(region)([center.lng, center.lat])
    elif key == 'originWgs84':
        center_local = [(center.lng-origin[0])*METERS_PER_DEGREE*math.cos(math.radians(origin[1])),
                        (center.lat-origin[1])*METERS_PER_DEGREE]
    else:
        raise ValueError('POI 起点坐标与区域坐标不一致')
    if not _point(center_local):
        raise ValueError('POI 起点超出已校准区域')
    order = [key for key in CATEGORIES if key in category_ids]
    records = []
    for source in document['items']:
        categories = [key for key in order if key in source['categories']]
        if not categories or math.dist(center_local, source['localPointMeters']) > radius:
            continue
        if bounds:
            point = [source['lng'], source['lat']] if center.coordType == 'bd09ll' else [
                origin[0]+source['localPointMeters'][0]/(METERS_PER_DEGREE*math.cos(math.radians(origin[1]))),
                origin[1]+source['localPointMeters'][1]/METERS_PER_DEGREE]
            if not bounds[0] <= point[0] <= bounds[2] or not bounds[1] <= point[1] <= bounds[3]:
                continue
        record = deepcopy(source)
        record['categories'], record['category'] = categories, categories[0]
        records.append(record)
    info = {'status': 'partial', 'provider': 'baidu_place_v2', 'dataSource': 'region_package',
            'cacheBackend': 'region_pois_json', 'regionId': region.manifest['id'],
            'snapshotCreatedAt': document.get('createdAt'), 'candidateCount': len(records),
            'snapshotCount': len(document['items']), 'cacheOnly': True, 'apiRequests': 0,
            'cacheHits': 1, 'cacheMisses': 0, 'stalePages': 0, 'refreshRequired': False,
            'inventoryVerified': False, 'accessVerified': False,
            'categories': [{'category': key, 'paginationComplete': False,
                            'error': '区域包包含已缓存地点，清单未经完整性核实'} for key in order]}
    return records, info
