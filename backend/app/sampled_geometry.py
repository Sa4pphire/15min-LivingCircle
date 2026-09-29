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