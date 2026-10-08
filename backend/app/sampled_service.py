"""为百度采样算法准备统一的 BD-09 中心点。"""

import json
import math
from typing import Any

from .baidu.client import BaiduClient
from .poi import collect_pois
from .representative_routes import collect_representative_routes
from .route_sampling import collect_sampling_routes
from .schemas import CenterPoint
from .sampled_analysis import build_sampled_isochrone
from .sampled_geometry import (
    build_blind_zone_coverage,
    isochrone_search_radius_meters,
    point_in_isochrone,
)
from .settings import settings


# 每类最多展示 5 条真实路线，兼顾画面密度和百度步行 API 配额。
REPRESENTATIVE_ROUTES_PER_CATEGORY = 5


def _load_region_geometry(origin: tuple[float, float]) -> dict[str, Any] | None:
    """读取演示区边界并转成以分析中心为原点的米制 Polygon。"""
    try:
        payload = json.loads(settings.poi_map_asset_path.read_text(encoding="utf-8"))
        coordinates = payload["boundary"]["geometry"]["coordinates"]
        scale_x = 111_320.0 * math.cos(math.radians(origin[1]))
        return {
            "type": "MultiPolygon",
            "coordinates": [[[
                [(float(point[0]) - origin[0]) * scale_x,
                 (float(point[1]) - origin[1]) * 111_320.0]
                for point in ring
            ] for ring in polygon] for polygon in [coordinates]],
        }
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _geometry_radius_meters(geometry: dict[str, Any] | None) -> int:
    if not geometry:
        return 0
    points = [
        point for polygon in geometry.get("coordinates", [])
        for ring in polygon for point in ring
    ]
    return math.ceil(max((math.hypot(float(point[0]), float(point[1])) for point in points), default=0))


# 将前端中心点统一转换为 BD-09 后运行第一套算法
async def run_sampled_analysis(
    client: BaiduClient,
    center: CenterPoint,
    *,
    threshold_seconds: float = 900.0,
    grid_step_meters: float = 100.0,
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
    # 若演示区边界可用，则同时覆盖整片四路围合区域。
    region_geometry = _load_region_geometry(bd09_center)
    search_radius = max(
        isochrone_search_radius_meters(result["isochroneMeters"]),
        _geometry_radius_meters(region_geometry),
    ) + 1000
    if search_radius:
        poi_result = await collect_pois(
            client, bd09_center, radius_meters=search_radius,
        )
    else:
        poi_result = {
            "facilities": {"type": "FeatureCollection", "features": []},
            "categoryStatus": {}, "warnings": ["EMPTY_ISOCHRONE"],
            "partial": True,
        }
    candidate_features = poi_result["facilities"]["features"]
    inside_features = [
        feature for feature in candidate_features
        if point_in_isochrone(
            tuple(feature["geometry"]["coordinates"]), result["isochrone"],
        )
    ]
    # Keep the complete three-category inventory for map symbols and blind
    # coverage. Only POIs inside the isochrone are eligible route endpoints.
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
    result["facilities"] = poi_result["facilities"]
    result["poiCategoryStatus"] = poi_result["categoryStatus"]
    result["poiWarnings"] = poi_result["warnings"]
    result["poiPartial"] = poi_result["partial"]
    result["poiSearchRadiusMeters"] = search_radius
    result["poiCandidateCount"] = len(candidate_features)
    inventory_complete = (
        not poi_result["partial"]
        and all(poi_result["categoryStatus"].get(category) == "complete"
                for category in ("market", "pharmacy", "primary_school"))
    )
    result["blindZones"] = build_blind_zone_coverage(
        region_geometry or result["isochroneMeters"],
        candidate_features,
        bd09_center,
        cell_size_meters=10.0,
        inventory_complete=inventory_complete,
    )
    result["blindZoneStatus"] = result["blindZones"].get("properties", {}).get("status")
    result["blindZoneResolutionMeters"] = 10.0
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
        boundary_geometry=result["isochroneMeters"],
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

    return result
