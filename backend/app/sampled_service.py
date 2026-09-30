"""为百度采样算法准备统一的 BD-09 中心点。"""

from typing import Any

from .baidu.client import BaiduClient
from .poi import collect_pois
from .representative_routes import collect_representative_routes
from .schemas import CenterPoint
from .sampled_analysis import build_sampled_isochrone


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

    # 搜索周边设施，再为每类代表设施获取少量真实步行路线。
    poi_result = await collect_pois(client, bd09_center)
    route_pois = []
    for feature in poi_result["facilities"]["features"]:
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
    )
    result["facilities"] = poi_result["facilities"]
    result["poiCategoryStatus"] = poi_result["categoryStatus"]
    result["poiWarnings"] = poi_result["warnings"]
    result["poiPartial"] = poi_result["partial"]
    result["routeSegments"] = routes
    result["routeFailures"] = route_failures

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
