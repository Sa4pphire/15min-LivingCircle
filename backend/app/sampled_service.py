"""为百度采样算法准备统一的 BD-09 中心点。"""

import math
from typing import Any

from .baidu.client import BaiduClient
from .baidu.errors import BaiduApiError
from .pois import CATEGORIES, PoiService
from .region_package import load_region, load_region_by_id
from .blind_zone_coverage import build_blind_zone_coverage, local_geometry_to_bd09
from .representative_routes import collect_representative_routes
from .route_sampling import collect_sampling_routes
from .schemas import CenterPoint
from .sampled_analysis import build_sampled_isochrone
from .sampled_geometry import (
    isochrone_search_radius_meters,
    point_in_isochrone,
)


# 每类最多展示 5 条真实路线，兼顾画面密度和百度步行 API 配额。
REPRESENTATIVE_ROUTES_PER_CATEGORY = 5


async def _collect_rule_pois(client, center, radius, geometry, *, region_id=None):
    """Reuse the existing five-category POI search and calibration rules."""
    region = load_region_by_id(region_id) if region_id else load_region()
    service = PoiService(client=client, region=region)
    points = [point for polygon in geometry.get('coordinates', []) for ring in polygon for point in ring]
    bounds = None
    if points:
        pad_y = 1000 / 111_320.0
        pad_x = pad_y / math.cos(math.radians(center.lat))
        bounds = [min(p[0] for p in points) - pad_x, min(p[1] for p in points) - pad_y,
                  max(p[0] for p in points) + pad_x, max(p[1] for p in points) + pad_y]
    records, info = await service.search(center, radius, CATEGORIES, bounds=bounds)
    project = None
    try:
        graph = region.graph()
        key = 'originWgs84' if 'originWgs84' in graph else 'originBd09'
        project, alignment = await service.frame({'coordType': 'wgs84ll' if key == 'originWgs84' else 'bd09ll',
                                                  'regionId': region.manifest['id'],
                                                  key: graph[key]}, cache_only=True)
        info['alignment'] = alignment
    except (BaiduApiError, OSError, ValueError, KeyError):
        info['alignment'] = 'unavailable'
    features = []
    for record in records:
        point = [record['lng'], record['lat']]
        categories = record.get('categories') or [record['category']]
        features.append({'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': point},
                         'properties': {'id': record['uid'], 'uid': record['uid'], 'name': record['name'],
                                        'address': record['address'], 'category': record['category'],
                                        'categories': categories, 'categoryLabel': CATEGORIES[record['category']]['label'],
                                        'tag': record.get('tag', ''), 'source': 'baidu', 'coordType': 'bd09ll',
                                        'localPointMeters': project(point) if project else None,
                                        'modelReachable': None, 'modelTravelTimeSeconds': None,
                                        'accessStatus': 'unverified_access', 'accessVerified': False}})
    return features, {**info, 'inventoryVerified': False, 'accessVerified': False}


# 将前端中心点统一转换为 BD-09 后运行第一套算法
async def run_sampled_analysis(
    client: BaiduClient,
    center: CenterPoint,
    *,
    threshold_seconds: float = 900.0,
    grid_step_meters: float = 100.0,
    region_id: str | None = None,
) -> dict[str, Any]:
    if center.coordType == "bd09ll":
        bd09_center = (center.lng, center.lat)
    else:
        converted = await client.convert_coordinates(
            [(center.lng, center.lat)],
            center.coordType,
        )

        if len(converted) != 1:
            raise ValueError("中心点坐标转换结果数量不正确")

        bd09_center = converted[0]

    result = await build_sampled_isochrone(
        client,
        bd09_center,
        threshold_seconds=threshold_seconds,
        grid_step_meters=grid_step_meters,
    )

    # 先用等时圈外包圆取候选，再严格按不规则多边形筛选终点。
    # 多取一个服务半径，才能判断圈边附近是否有圈外 POI 覆盖；
    # 查询范围随本次等时圈变化，避免整片区域消耗分页和请求预算。
    isochrone_radius = isochrone_search_radius_meters(result["isochroneMeters"])
    search_radius = isochrone_radius + 1000 if isochrone_radius else 0
    if isochrone_radius:
        candidates, poi_info = await _collect_rule_pois(
            client, CenterPoint(lng=bd09_center[0], lat=bd09_center[1], coordType='bd09ll'),
            search_radius, result['isochrone'], **({'region_id': region_id} if region_id else {}))
    else:
        candidates, poi_info = [], {'status': 'unavailable', 'categories': [], 'error': 'EMPTY_ISOCHRONE'}
    candidate_features = candidates
    inside_features = [
        feature for feature in candidate_features
        if point_in_isochrone(
            tuple(feature["geometry"]["coordinates"]), result["isochrone"],
        )
    ]
    for feature in inside_features:
        feature['properties']['insideDisplayPolygon'] = True
    route_pois = []
    for feature in inside_features:
        properties = feature["properties"]
        lng, lat = feature["geometry"]["coordinates"]
        route_pois.append({
            "uid": properties["uid"],
            "category": properties["category"],
            "lng": lng,
            "lat": lat,
        })

    routes, route_failures = await collect_representative_routes(
        client,
        bd09_center,
        route_pois,
        per_category=REPRESENTATIVE_ROUTES_PER_CATEGORY,
        max_duration_seconds=threshold_seconds,
    )
    result['facilities'] = {'type': 'FeatureCollection', 'coordType': 'bd09ll', 'features': inside_features}
    result['poiFacilities'] = result['facilities']
    result['poiInfo'] = poi_info
    result['poiCategories'] = [{'category': key, 'label': config['label'],
                               'queriedCount': sum(key in f['properties']['categories'] for f in candidate_features),
                               'insideDisplayCount': sum(key in f['properties']['categories'] for f in inside_features),
                               'modelReachableCount': 0, 'inventoryStatus': 'incomplete'} for key, config in CATEGORIES.items()]
    result['poiCategoryStatus'] = {item['category']: 'complete'
                                  if item['paginationComplete'] and not item.get('discardedCount', 0)
                                  and not item.get('error') else 'partial'
                                  for item in poi_info.get('categories', [])}
    result['poiWarnings'] = [] if poi_info['status'] == 'ready' else ['BAIDU_POI_DATA_PARTIAL_OR_UNAVAILABLE']
    result['poiPartial'] = poi_info['status'] != 'ready'
    result["poiSearchRadiusMeters"] = search_radius
    result["poiCandidateCount"] = len(candidate_features)
    inventory_complete = (
        poi_info['status'] == 'ready'
        and all(result['poiCategoryStatus'].get(category) == 'complete' for category in CATEGORIES)
    )
    blind_zones = build_blind_zone_coverage(
        result["isochroneMeters"],
        candidate_features,
        bd09_center,
        category_ids=CATEGORIES,
        inventory_complete=inventory_complete,
        category_completeness={category: result['poiCategoryStatus'].get(category) == 'complete'
                               for category in CATEGORIES},
    )
    if poi_info['status'] == 'unavailable':
        blind_zones['features'] = []
        blind_zones['properties'].update(status='unknown', reason='POI data unavailable')
    for feature in blind_zones['features']:
        feature['geometry'] = local_geometry_to_bd09(feature['geometry'], bd09_center)
    blind_zones['properties']['coordinateSystem'] = 'bd09ll'
    result['blindZones'] = blind_zones
    result['blindZoneStatus'] = blind_zones['properties']['status']
    result['blindZoneCategoryIds'] = list(CATEGORIES)
    result['blindZoneResolutionMeters'] = None
    result["routeSegments"] = routes
    result["routeFailures"] = route_failures
    result["routeCount"] = len({
        route["poiUid"] for route in routes if route.get("poiUid")
    })

    # RouteMatrix 只给采样点耗时；这里再为每个方向的边界点请求真实折线。
    sample_routes, sample_route_failures = await collect_sampling_routes(
        client,
        bd09_center,
        result["durationSamples"],
        threshold_seconds=threshold_seconds,
        boundary_geometry=result['isochroneMeters'],
    )
    result["samplingRouteSegments"] = sample_routes
    result["samplingRouteFailures"] = sample_route_failures
    result["samplingRouteCount"] = len({
        route["sampleIndex"] for route in sample_routes
        if route.get("sampleIndex") is not None
    })

    result["requestedCenter"] = {
        "lng": center.lng,
        "lat": center.lat,
        "coordType": center.coordType,
    }
    result["analysisCenter"] = {
        "lng": bd09_center[0],
        "lat": bd09_center[1],
        "coordType": "bd09ll",
    }
    if region_id:
        result['regionId'] = region_id

    return result
