"""从 POI 候选中选择少量可展示的百度步行路线。"""

import math
from typing import Any, Protocol

from .baidu.errors import BaiduApiError


class WalkingRouteClient(Protocol):
    async def walking_route(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        *,
        destination_uid: str | None = None,
    ) -> dict[str, Any]:
        ...


def select_representative_pois(
    facilities: list[dict[str, Any]],
    origin: tuple[float, float],
    *,
    per_category: int = 1,
) -> list[dict[str, Any]]:
    """每类选择离中心最近的有限 POI，控制路线详情调用数量。"""
    if per_category < 1:
        return []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for facility in facilities:
        category = str(facility.get("category") or "unknown")
        try:
            point = (float(facility["lng"]), float(facility["lat"]))
        except (KeyError, TypeError, ValueError):
            continue
        if not all(math.isfinite(value) for value in (*point, *origin)):
            continue
        record = {**facility, "lng": point[0], "lat": point[1]}
        record["_distance"] = math.hypot(
            (point[0] - origin[0]) * math.cos(math.radians(origin[1])),
            point[1] - origin[1],
        )
        grouped.setdefault(category, []).append(record)

    selected = []
    for category in sorted(grouped):
        candidates = sorted(grouped[category], key=lambda item: (item["_distance"], str(item.get("uid", ""))))
        selected.extend(candidates[:per_category])
    for item in selected:
        item.pop("_distance", None)
    return selected


async def collect_representative_routes(
    client: WalkingRouteClient,
    origin: tuple[float, float],
    facilities: list[dict[str, Any]],
    *,
    per_category: int = 1,
    max_duration_seconds: float | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """返回可绘制路线，并过滤超过生活圈时限的目的地。"""
    routes: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for facility in select_representative_pois(facilities, origin, per_category=per_category):
        try:
            route = await client.walking_route(
                origin,
                (facility["lng"], facility["lat"]),
                destination_uid=facility.get("uid"),
            )
        except (BaiduApiError, OSError, TimeoutError) as exc:
            failures.append({"uid": facility.get("uid"), "error": str(exc)})
            continue
        duration = float(route["durationSeconds"])
        if max_duration_seconds is not None and duration > max_duration_seconds:
            failures.append({
                "uid": facility.get("uid"),
                "error": "route exceeds threshold",
                "durationSeconds": duration,
            })
            continue
        for index, points in enumerate(route["segments"]):
            routes.append({
                "id": f"poi:{facility.get('uid', index)}:{index}",
                "category": facility.get("category", "unknown"),
                "poiUid": facility.get("uid"),
                "points": points,
                "distanceMeters": route["distanceMeters"],
                "durationSeconds": route["durationSeconds"],
            })
    return routes, failures
