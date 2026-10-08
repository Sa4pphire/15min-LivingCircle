"""将采样算法的局部米制几何转换为 BD-09，并计算连续服务盲区。"""

import math
from typing import Any

from shapely.geometry import Point, mapping, shape
from shapely.ops import unary_union

METERS_PER_DEGREE = 111_320.0


def local_point_to_bd09(point, origin):
    if len(point) != 2:
        raise ValueError("局部坐标必须包含 x 和 y")
    x_meters, y_meters = float(point[0]), float(point[1])
    origin_lng, origin_lat = float(origin[0]), float(origin[1])
    if not all(math.isfinite(value) for value in
               (x_meters, y_meters, origin_lng, origin_lat)):
        raise ValueError("坐标不能包含非有限数值")
    scale_x = METERS_PER_DEGREE * math.cos(math.radians(origin_lat))
    return [origin_lng + x_meters / scale_x,
            origin_lat + y_meters / METERS_PER_DEGREE]


def multipolygon_to_bd09(geometry: dict[str, Any], origin):
    if geometry.get("type") != "MultiPolygon":
        raise ValueError("等时圈几何必须是 MultiPolygon")
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list):
        raise ValueError("MultiPolygon 缺少 coordinates")
    return {"type": "MultiPolygon", "coordinates": [
        [[local_point_to_bd09(point, origin) for point in ring]
         for ring in polygon] for polygon in coordinates
    ]}


def local_geometry_to_bd09(geometry: dict[str, Any], origin):
    """Convert a Shapely-mapped local Polygon/MultiPolygon to BD-09."""
    geometry_type = geometry.get("type")
    if geometry_type not in {"Polygon", "MultiPolygon"}:
        raise ValueError("盲区几何必须是 Polygon 或 MultiPolygon")

    def convert(value):
        if (isinstance(value, (list, tuple)) and len(value) == 2 and
                all(isinstance(item, (int, float)) for item in value)):
            return local_point_to_bd09(value, origin)
        if not isinstance(value, (list, tuple)):
            raise ValueError("盲区几何坐标无效")
        return [convert(item) for item in value]

    return {"type": geometry_type, "coordinates": convert(geometry.get("coordinates"))}


def isochrone_search_radius_meters(geometry: dict[str, Any]) -> int:
    if geometry.get("type") != "MultiPolygon":
        raise ValueError("等时圈几何必须是 MultiPolygon")
    distances = [math.hypot(float(point[0]), float(point[1]))
                 for polygon in geometry.get("coordinates", [])
                 for ring in polygon for point in ring]
    if not distances:
        return 0
    if not all(math.isfinite(distance) for distance in distances):
        raise ValueError("等时圈坐标不能包含非有限数值")
    return max(1, math.ceil(max(distances) * 1.02 + 20))


def _point_in_ring(point, ring):
    x, y = point
    inside = False
    for index in range(len(ring)):
        x1, y1 = ring[index - 1]
        x2, y2 = ring[index]
        cross = (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)
        if (abs(cross) < 1e-12 and min(x1, x2) <= x <= max(x1, x2)
                and min(y1, y2) <= y <= max(y1, y2)):
            return True
        if (y1 > y) != (y2 > y):
            intersection = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < intersection:
                inside = not inside
    return inside


def point_in_isochrone(point, geometry):
    if geometry.get("type") != "MultiPolygon":
        raise ValueError("等时圈几何必须是 MultiPolygon")
    if not all(math.isfinite(value) for value in point):
        return False
    return any(
        _point_in_ring(point, polygon[0])
        and not any(_point_in_ring(point, hole) for hole in polygon[1:])
        for polygon in geometry.get("coordinates", []) if polygon
    )


def build_blind_zone_coverage(
    geometry: dict[str, Any],
    facilities: list[dict[str, Any]],
    origin: tuple[float, float],
    *,
    cell_size_meters: float = 10.0,
    service_radius_meters: float = 1000.0,
    inventory_complete: bool = True,
) -> dict[str, Any]:
    """Subtract continuous 1 km category coverage unions from the area.

    The response is a continuous MultiPolygon. The 10 m value is declared as
    boundary precision; no visible grid cells are generated.
    """
    if geometry.get("type") != "MultiPolygon":
        raise ValueError("盲区覆盖面需要 MultiPolygon 等时圈")
    if cell_size_meters <= 0 or service_radius_meters <= 0:
        return {"type": "FeatureCollection", "features": [],
                "properties": {"status": "empty",
                               "resolutionMeters": cell_size_meters}}
    # Keep rendering a candidate layer even when one or more POI pages were
    # unavailable.  The geometry is then explicitly marked provisional so a
    # missing page is never presented as proof that a service is absent.
    inventory_status = "confirmed" if inventory_complete else "provisional"
    inventory_reason = (None if inventory_complete else
                        "POI inventory is incomplete; zones use returned POIs")

    categories = ("market", "pharmacy", "primary_school")
    scale_x = METERS_PER_DEGREE * math.cos(math.radians(float(origin[1])))
    poi_points = {key: [] for key in categories}
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

    base = shape(geometry).buffer(0)
    coverage = {}
    for category in categories:
        circles = [Point(point).buffer(service_radius_meters, quad_segs=32)
                   for point in poi_points[category]]
        coverage[category] = unary_union(circles) if circles else Point().buffer(0)

    from itertools import combinations
    features = []
    for count in range(1, len(categories) + 1):
        for missing_tuple in combinations(categories, count):
            missing = set(missing_tuple)
            area = base
            for category in missing:
                area = area.difference(coverage[category])
            for category in categories:
                if category not in missing:
                    area = area.intersection(coverage[category])
            if area.geom_type == "GeometryCollection":
                area = unary_union([part for part in area.geoms
                                    if part.geom_type in {"Polygon", "MultiPolygon"}])
            if area.is_empty:
                continue
            features.append({
                "type": "Feature",
                "geometry": mapping(area),
                "properties": {
                    "missingCategories": list(missing_tuple),
                    "approximate": True,
                    "resolutionMeters": cell_size_meters,
                    "serviceRadiusMeters": service_radius_meters,
                    "geometryMethod": "continuous-service-union",
                },
            })
    properties = {"status": inventory_status,
                  "resolutionMeters": cell_size_meters,
                  "serviceRadiusMeters": service_radius_meters,
                  "geometryMethod": "continuous-service-union"}
    if inventory_reason:
        properties["reason"] = inventory_reason
    return {"type": "FeatureCollection", "features": features,
            "properties": properties}


def build_blind_zone_grid(*args, **kwargs):
    """Compatibility alias for the former grid API."""
    return build_blind_zone_coverage(*args, **kwargs)
