"""On-demand Baidu walking routes for a displayed real-area POI."""

import math

from .baidu.errors import BaiduApiError
from .poi_routes import RouteUnavailable
from .pois import CATEGORIES


def _point(value):
    return (isinstance(value, (list, tuple)) and len(value) == 2 and
            all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in value)
            and -180 <= value[0] <= 180 and -90 < value[1] < 90)


def _result(analysis_id, poi_id, origin, route, threshold, cache_source, stats):
    distance, duration = route.get('distanceMeters'), route.get('durationSeconds')
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0
               for v in (distance, duration)):
        raise BaiduApiError('百度步行路线距离或耗时无效')
    parts = route.get('segments')
    if (not isinstance(parts, list) or not parts or
            any(not isinstance(part, list) or len(part) < 2 or not all(_point(p) for p in part) for part in parts)):
        raise BaiduApiError('百度步行路线折线无效')
    return {'schemaVersion': 1, 'analysisId': analysis_id, 'poiId': poi_id, 'status': 'ready',
            'algorithm': 'baidu_walking', 'provider': 'baidu_direction_v2', 'coordType': 'bd09ll',
            'distanceMeters': distance, 'durationSeconds': duration,
            'lengthMeters': distance, 'travelTimeSeconds': duration, 'withinThreshold': duration <= threshold,
            'originCoordinates': list(origin), 'destinationCoordinates': list(parts[-1][-1]),
            'geometry': {'type': 'MultiLineString', 'coordinates': parts},
            'segments': [{'id': f'baidu:{poi_id}:{index}', 'kind': 'baidu_walk', 'points': part,
                          # Animation order only; the API adapter exposes no per-step times.
                          'startProgress': index / len(parts)} for index, part in enumerate(parts)],
            'cacheSource': cache_source, 'apiRequests': stats.get('apiRequests', 0),
            'cacheHits': stats.get('cacheHits', 0), 'accessVerified': False}


async def route_sampled_poi(client, analysis_id, poi_id, report):
    collection = report.get('poiFacilities', {})
    if collection.get('coordType') != 'bd09ll':
        raise RouteUnavailable('ROUTE_CONTEXT_EXPIRED', '当前分析缺少有效设施清单，请重新计算真实区域分析。')
    feature = next((item for item in collection.get('features', [])
                    if item.get('properties', {}).get('id') == poi_id
                    and item.get('geometry', {}).get('type') == 'Point'
                    and item['properties'].get('insideDisplayPolygon') is True
                    and item['properties'].get('category') in CATEGORIES), None)
    if feature is None:
        raise RouteUnavailable('POI_NOT_IN_ANALYSIS', '该设施不在当前等时圈候选清单中。')
    data = feature['properties']
    destination = feature.get('geometry', {}).get('coordinates')
    center = report.get('analysisCenter', {})
    origin = [center.get('lng'), center.get('lat')]
    if center.get('coordType') != 'bd09ll' or not _point(origin) or not _point(destination):
        raise RouteUnavailable('ROUTE_CONTEXT_EXPIRED', '当前分析的起终点坐标不可用，请重新计算真实区域分析。')
    threshold = report.get('thresholdSeconds', 900)
    if not isinstance(threshold, (int, float)) or not math.isfinite(threshold) or threshold <= 0:
        raise RouteUnavailable('ROUTE_CONTEXT_EXPIRED', '当前分析的步行阈值无效，请重新计算。')
    uid = data.get('uid', data['id'])
    previous = [segment for segment in report.get('routeSegments', []) if segment.get('poiUid') == uid]
    if previous:
        first = previous[0]
        if all(segment.get('distanceMeters') == first.get('distanceMeters') and
               segment.get('durationSeconds') == first.get('durationSeconds') for segment in previous):
            try:
                return _result(analysis_id, poi_id, origin,
                               {'distanceMeters': first.get('distanceMeters'), 'durationSeconds': first.get('durationSeconds'),
                                'segments': [segment.get('points') for segment in previous]}, threshold, 'analysis', {})
            except BaiduApiError:
                pass  # An incomplete old display route must be fetched again.
    before = dict(getattr(client, 'cache_stats', {}))
    route = await client.walking_route(tuple(origin), tuple(destination), destination_uid=uid)
    stats = {key: value - before.get(key, 0) for key, value in getattr(client, 'cache_stats', {}).items()}
    source = 'shared_cache' if stats.get('cacheHits', 0) or stats.get('stalePages', 0) else 'api'
    return _result(analysis_id, poi_id, origin, route, threshold, source, stats)
