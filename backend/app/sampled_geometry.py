"""将百度采样算法的局部米制几何转换为 BD-09 经纬度。"""

import math
from typing import Any


METERS_PER_DEGREE = 111_320.0


# 将相对中心点的局部米制坐标转换为 BD-09 经纬度
def local_point_to_bd09(
    point: list[float] | tuple[float, float],
    origin: tuple[float, float],
) -> list[float]:
    if len(point) != 2:
        raise ValueError("局部坐标必须包含 x 和 y")

    x_meters = float(point[0])
    y_meters = float(point[1])
    origin_lng = float(origin[0])
    origin_lat = float(origin[1])

    if not all(
        math.isfinite(value)
        for value in (x_meters, y_meters, origin_lng, origin_lat)
    ):
        raise ValueError("坐标不能包含非有限数值")

    scale_x = METERS_PER_DEGREE * math.cos(
        math.radians(origin_lat)
    )

    return [
        origin_lng + x_meters / scale_x,
        origin_lat + y_meters / METERS_PER_DEGREE,
    ]


# 将局部米制 MultiPolygon 的每个点转换为 BD-09
def multipolygon_to_bd09(
    geometry: dict[str, Any],
    origin: tuple[float, float],
) -> dict[str, Any]:
    if geometry.get("type") != "MultiPolygon":
        raise ValueError("等时圈几何必须是 MultiPolygon")

    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list):
        raise ValueError("MultiPolygon 缺少 coordinates")

    return {
        "type": "MultiPolygon",
        "coordinates": [
            [
                [
                    local_point_to_bd09(point, origin)
                    for point in ring
                ]
                for ring in polygon
            ]
            for polygon in coordinates
        ],
    }


# 用能包住等时圈的圆形范围向百度取候选，最终仍按多边形筛选。
def isochrone_search_radius_meters(geometry: dict[str, Any]) -> int:
    if geometry.get("type") != "MultiPolygon":
        raise ValueError("等时圈几何必须是 MultiPolygon")
    distances = [
        math.hypot(float(point[0]), float(point[1]))
        for polygon in geometry.get("coordinates", [])
        for ring in polygon
        for point in ring
    ]
    if not distances:
        return 0
    if not all(math.isfinite(distance) for distance in distances):
        raise ValueError("等时圈坐标不能包含非有限数值")
    # 留少量余量，避免坐标转换和圆形检索的边界精度漏掉 POI。
    return max(1, math.ceil(max(distances) * 1.02 + 20))


def _point_in_ring(point: tuple[float, float], ring: list[list[float]]) -> bool:
    x, y = point
    inside = False
    for index in range(len(ring)):
        x1, y1 = ring[index - 1]
        x2, y2 = ring[index]
        cross = (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)
        if abs(cross) < 1e-12 and min(x1, x2) <= x <= max(x1, x2) and min(y1, y2) <= y <= max(y1, y2):
            return True
        if (y1 > y) != (y2 > y):
            intersection = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < intersection:
                inside = not inside
    return inside


# 判断 BD-09 POI 是否落在任一等时圈面内，同时排除内洞。
def point_in_isochrone(
    point: tuple[float, float],
    geometry: dict[str, Any],
) -> bool:
    if geometry.get("type") != "MultiPolygon":
        raise ValueError("等时圈几何必须是 MultiPolygon")
    if not all(math.isfinite(value) for value in point):
        return False
    return any(
        _point_in_ring(point, polygon[0])
        and not any(_point_in_ring(point, hole) for hole in polygon[1:])
        for polygon in geometry.get("coordinates", [])
        if polygon
    )


# 用 POI 服务半径把等时圈离散成可解释的候选盲区网格。
def build_blind_zone_grid(
    geometry: dict[str, Any],
    facilities: list[dict[str, Any]],
    origin: tuple[float, float],
    *,
    cell_size_meters: float = 10.0,
    service_radius_meters: float = 1000.0,
    inventory_complete: bool = True,
) -> dict[str, Any]:
    if geometry.get("type") != "MultiPolygon":
        raise ValueError("盲区网格需要 MultiPolygon 等时圈")
    points = [
        point
        for polygon in geometry.get("coordinates", [])
        for ring in polygon
        for point in ring
    ]
    if not points or cell_size_meters <= 0 or service_radius_meters <= 0:
        return {"type": "FeatureCollection", "features": [],
                "properties": {"status": "empty", "resolutionMeters": cell_size_meters}}
    if not inventory_complete:
        # An incomplete provider response cannot prove a service blind area.
        return {"type": "FeatureCollection", "features": [],
                "properties": {
                    "status": "unknown",
                    "resolutionMeters": cell_size_meters,
                    "reason": "POI inventory is incomplete",
                }}
    min_x = math.floor(min(float(point[0]) for point in points) / cell_size_meters)
    max_x = math.ceil(max(float(point[0]) for point in points) / cell_size_meters)
    min_y = math.floor(min(float(point[1]) for point in points) / cell_size_meters)
    max_y = math.ceil(max(float(point[1]) for point in points) / cell_size_meters)
    categories = ("market", "pharmacy", "primary_school")
    scale_x = METERS_PER_DEGREE * math.cos(math.radians(float(origin[1])))
    poi_points: dict[str, list[tuple[float, float]]] = {key: [] for key in categories}
    for feature in facilities:
        properties = feature.get("properties", {})
        category = properties.get("category")
        if category not in poi_points:
            continue
        try:
            lng, lat = feature["geometry"]["coordinates"]
            point = ((float(lng) - origin[0]) * scale_x,
                     (float(lat) - origin[1]) * METERS_PER_DEGREE)
        except (KeyError, TypeError, ValueError):
            continue
        if all(math.isfinite(value) for value in point):
            poi_points[category].append(point)

    # First classify 10 m cells.  Adjacent cells with the same missing
    # categories are coalesced into horizontal strips, so the response keeps
    # 10 m boundary resolution without rendering a distracting coarse grid.
    row_runs: list[tuple[int, int, int, tuple[str, ...]]] = []
    for iy in range(min_y, max_y + 1):
        runs: list[tuple[int, int, tuple[str, ...]]] = []
        run_start: int | None = None
        run_missing: tuple[str, ...] | None = None
        for ix in range(min_x, max_x + 1):
            center = ((ix + 0.5) * cell_size_meters,
                      (iy + 0.5) * cell_size_meters)
            if not point_in_isochrone(center, geometry):
                current = None
            else:
                missing = tuple(
                    category for category in categories
                    if not any(math.dist(center, poi) <= service_radius_meters
                               for poi in poi_points[category])
                )
                current = missing or None
            if current != run_missing:
                if run_start is not None and run_missing:
                    runs.append((run_start, ix - 1, run_missing))
                run_start = ix if current else None
                run_missing = current
        if run_start is not None and run_missing:
            runs.append((run_start, max_x, run_missing))
        for start, end, missing in runs:
            row_runs.append((start, end, iy, missing))

    features = []
    for start, end, iy, missing in row_runs:
        corners = [
            (start * cell_size_meters, iy * cell_size_meters),
            ((end + 1) * cell_size_meters, iy * cell_size_meters),
            ((end + 1) * cell_size_meters, (iy + 1) * cell_size_meters),
            (start * cell_size_meters, (iy + 1) * cell_size_meters),
            (start * cell_size_meters, iy * cell_size_meters),
        ]
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[local_point_to_bd09(point, origin)
                                  for point in corners]],
            },
            "properties": {
                "missingCategories": list(missing),
                "approximate": True,
                "resolutionMeters": cell_size_meters,
                "serviceRadiusMeters": service_radius_meters,
            },
        })
    return {
        "type": "FeatureCollection",
        "features": features,
        "properties": {
            "status": "confirmed",
            "resolutionMeters": cell_size_meters,
            "serviceRadiusMeters": service_radius_meters,
        },
    }
