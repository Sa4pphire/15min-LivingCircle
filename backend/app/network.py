"""Load a manually annotated walking graph and convert local meter coordinates."""

import json
import math
from pathlib import Path
from typing import Any

from .schemas import CenterPoint
from .settings import settings

METERS_PER_DEGREE = 111_320.0


class UnsupportedAreaError(ValueError):
    pass


def _valid_xy(point: Any) -> bool:
    return (isinstance(point, list) and len(point) == 2 and
            all(not isinstance(value, bool) and isinstance(value, (int, float))
                and math.isfinite(value) for value in point))


def _contains_point(ring: list[list[float]], x: float, y: float) -> bool:
    inside = False
    for first, second in zip(ring, ring[1:]):
        ax, ay = first
        bx, by = second
        cross = (x - ax) * (by - ay) - (y - ay) * (bx - ax)
        if abs(cross) <= 1e-7 and min(ax, bx) - 1e-7 <= x <= max(ax, bx) + 1e-7 and \
                min(ay, by) - 1e-7 <= y <= max(ay, by) + 1e-7:
            return True
        if (ay > y) != (by > y) and x < (bx - ax) * (y - ay) / (by - ay) + ax:
            inside = not inside
    return inside


def _facility_entrances(facility: dict[str, Any]) -> list[dict[str, Any]]:
    if "entrances" in facility:
        return facility["entrances"]
    return [{"id": facility["id"], "accessEdgeId": facility["accessEdgeId"],
             "streetAccessPointMeters": facility["accessPointMeters"]}]


def _local_point(center: CenterPoint, origin: dict[str, float]) -> tuple[float, float]:
    latitude = float(origin["lat"])
    scale_x = METERS_PER_DEGREE * math.cos(math.radians(latitude))
    return (
        (center.lng - float(origin["lng"])) * scale_x,
        (center.lat - latitude) * METERS_PER_DEGREE,
    )


def _to_bd09(point: list[float], origin: dict[str, float]) -> list[float]:
    latitude = float(origin["lat"])
    scale_x = METERS_PER_DEGREE * math.cos(math.radians(latitude))
    return [float(origin["lng"]) + point[0] / scale_x,
            latitude + point[1] / METERS_PER_DEGREE]


def load_engine_request(center: CenterPoint, origin_edge_id: str | None = None
                        ) -> tuple[dict[str, Any], dict[str, Any]]:
    path: Path = settings.walking_network_path
    if not path.is_file():
        raise UnsupportedAreaError("演示区域尚未提供经核实的步行路网")
    try:
        network = json.loads(path.read_text(encoding="utf-8"))
        if network["schemaVersion"] != 2:
            raise ValueError("schemaVersion must be 2")
        origin = network["originBd09"]
        bounds = network["supportedCenterBoundsMeters"]
        x_meters, y_meters = _local_point(center, origin)
        for key in ("minX", "maxX", "minY", "maxY"):
            if (isinstance(bounds[key], bool) or
                    not isinstance(bounds[key], (int, float)) or
                    not math.isfinite(bounds[key])):
                raise ValueError(f"supportedCenterBoundsMeters.{key} must be numeric")
        for key in ("nodes", "edges"):
            if not isinstance(network[key], list) or not network[key]:
                raise ValueError(f"{key} must be a non-empty list")
        synthetic = network.get("synthetic") is True
        if not synthetic:
            areas = network.get("publicWalkableAreasMeters")
            if not isinstance(areas, list) or not areas:
                raise ValueError("real network needs publicWalkableAreasMeters")
            for ring in areas:
                if (not isinstance(ring, list) or len(ring) < 4 or
                        ring[0] != ring[-1] or
                        any(not _valid_xy(point) for point in ring)):
                    raise ValueError("invalid public walkable area ring")
            if not any(_contains_point(ring, x_meters, y_meters) for ring in areas):
                raise UnsupportedAreaError("中心点不在已核实的公共步行空间内")
        categories = network.get("serviceCategories", [])
        if (not isinstance(categories, list) or len(categories) > 10 or
                (not synthetic and not categories)):
            raise ValueError("real network needs serviceCategories")
        category_ids: set[str] = set()
        for category in categories:
            if (not isinstance(category, dict) or
                    not isinstance(category.get("id"), str) or
                    not category["id"] or category["id"] in category_ids or
                    category.get("dataStatus") not in ("reviewed_online", "incomplete")):
                raise ValueError("invalid service category")
            category_ids.add(category["id"])
        facilities = network.get("facilities", [])
        if not isinstance(facilities, list):
            raise ValueError("facilities must be a list")
        facility_ids: set[str] = set()
        for facility in facilities:
            if not isinstance(facility, dict):
                raise ValueError("facility must be an object")
            if (not isinstance(facility.get("id"), str) or not facility["id"] or
                    facility["id"] in facility_ids):
                raise ValueError("facility.id must be a non-empty string")
            facility_ids.add(facility["id"])
            if "entrances" in facility or not synthetic:
                if facility.get("category") not in category_ids:
                    raise ValueError("facility.category must name a service category")
                entrances = facility.get("entrances")
                if not isinstance(entrances, list) or not entrances:
                    raise ValueError("facility.entrances must be non-empty")
                if "accessEdgeId" in facility or "accessPointMeters" in facility:
                    raise ValueError("facility cannot mix legacy and entrance fields")
            else:
                entrances = _facility_entrances(facility)
            entrance_ids: set[str] = set()
            for entrance in entrances:
                if (not isinstance(entrance, dict) or
                        not isinstance(entrance.get("id"), str) or
                        not entrance["id"] or entrance["id"] in entrance_ids or
                        not isinstance(entrance.get("accessEdgeId"), str) or
                        not entrance["accessEdgeId"] or
                        not _valid_xy(entrance.get("streetAccessPointMeters"))):
                    raise ValueError("invalid facility entrance")
                entrance_ids.add(entrance["id"])
                if "accessPathMeters" in entrance:
                    path_points = entrance["accessPathMeters"]
                    if (not isinstance(path_points, list) or len(path_points) < 2 or
                            any(not _valid_xy(point) for point in path_points) or
                            path_points[0] != entrance["streetAccessPointMeters"]):
                        raise ValueError("invalid facility access path")
    except UnsupportedAreaError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise UnsupportedAreaError(f"步行路网文件无效：{exc}") from exc

    if not (bounds["minX"] <= x_meters <= bounds["maxX"] and
            bounds["minY"] <= y_meters <= bounds["maxY"]):
        raise UnsupportedAreaError("该中心点不在已标注的演示区域内")

    payload = {
        "schemaVersion": 2,
        "originMeters": {"xMeters": x_meters, "yMeters": y_meters},
        "thresholdSeconds": 900,
        "walkingSpeedMetersPerSecond": 1.3,
        "crossingWaitSeconds": 20,
        "maxOriginSnapMeters": 30 if synthetic else 5,
        "displayBufferMeters": 15,
        "displayGridStepMeters": 10,
        "nodes": network["nodes"],
        "edges": network["edges"],
        "facilities": facilities,
        "serviceCategories": categories,
    }
    if origin_edge_id is not None:
        if not origin_edge_id:
            raise UnsupportedAreaError("originEdgeId 不能为空")
        payload["originEdgeId"] = origin_edge_id
    elif math.hypot(x_meters, y_meters) < 1 and network.get("originEdgeId"):
        payload["originEdgeId"] = network["originEdgeId"]
    metadata = {
        "originBd09": origin,
        "networkSource": "synthetic" if network.get("synthetic") else "manual",
        "facilities": facilities,
        "serviceCategories": categories,
    }
    return payload, metadata


def build_analysis_result(engine_result: dict[str, Any],
                          network_meta: dict[str, Any]) -> dict[str, Any]:
    origin = network_meta["originBd09"]
    display_geometry = engine_result.get("displayGeometryMeters")
    if display_geometry is not None:
        if (not isinstance(display_geometry, dict) or
                display_geometry.get("type") != "MultiPolygon"):
            raise ValueError("C++ 引擎展示面必须是 MultiPolygon")
        polygon_meters = display_geometry["coordinates"]
        if ("displayPolygonMeters" in engine_result and
                polygon_meters != engine_result["displayPolygonMeters"]):
            raise ValueError("C++ 引擎展示面两种表示不一致")
    else:
        polygon_meters = engine_result["displayPolygonMeters"]
    polygons = [
        [[_to_bd09(point, origin) for point in ring] for ring in polygon]
        for polygon in polygon_meters
    ]
    walkways = []
    for edge in engine_result["reachableEdges"]:
        walkways.append({
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [_to_bd09(point, origin) for point in edge["pathMeters"]],
            },
            "properties": {"edgeId": edge["edgeId"], "kind": edge["kind"],
                           "widthMeters": edge.get("widthMeters")},
        })
    warnings = list(engine_result["diagnostics"].get("warnings", []))
    if network_meta["networkSource"] == "synthetic":
        warnings.append("SYNTHETIC_NETWORK_NOT_REAL_WORLD")
    requested_facilities = {facility["id"]: facility
                            for facility in network_meta.get("facilities", [])}
    facility_times = engine_result.get("facilityTravelTimes", [])
    if len(facility_times) != len(requested_facilities):
        raise ValueError("C++ 引擎设施耗时数量与路网标注不一致")
    facilities = []
    seen_facilities: set[str] = set()
    for timing in facility_times:
        facility_id = timing["id"]
        if facility_id not in requested_facilities or facility_id in seen_facilities:
            raise ValueError("C++ 引擎返回了未知或重复的设施 ID")
        seen_facilities.add(facility_id)
        facility = requested_facilities[facility_id]
        entrances = _facility_entrances(facility)
        best_entrance_id = timing.get("bestEntranceId")
        if best_entrance_id is not None and not isinstance(best_entrance_id, str):
            raise ValueError("C++ 引擎设施入口 ID 无效")
        selected_entrance = next(
            (entry for entry in entrances if entry["id"] == best_entrance_id), None)
        if best_entrance_id is not None and selected_entrance is None:
            raise ValueError("C++ 引擎返回了未知设施入口")
        if selected_entrance is None:
            selected_entrance = entrances[0]
        if timing["accessEdgeId"] != selected_entrance["accessEdgeId"]:
            raise ValueError("C++ 引擎返回的设施接入边不匹配")
        category = facility.get("category", "")
        if timing.get("category", category) != category:
            raise ValueError("C++ 引擎设施类别不匹配")
        reachable = timing["reachable"]
        seconds = timing["travelTimeSeconds"]
        if not isinstance(reachable, bool):
            raise ValueError("C++ 引擎设施可达状态无效")
        if (seconds is not None and
                (isinstance(seconds, bool) or
                 not isinstance(seconds, (int, float)) or
                 not math.isfinite(seconds) or seconds < 0)):
            raise ValueError("C++ 引擎设施步行耗时无效")
        if reachable and seconds is None:
            raise ValueError("可达设施必须返回步行耗时")
        if reachable and best_entrance_id is None and "entrances" in facility:
            raise ValueError("可达设施必须指出最佳入口")
        point = (selected_entrance.get("accessPathMeters") or
                 [selected_entrance["streetAccessPointMeters"]])[-1]
        facilities.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": _to_bd09(point, origin),
            },
            "properties": {
                "id": facility_id,
                "category": category,
                "accessEdgeId": timing["accessEdgeId"],
                "bestEntranceId": best_entrance_id,
                "reachable": reachable,
                "travelTimeSeconds": seconds,
            },
        })
    requested_categories = {category["id"]: category
                            for category in network_meta.get("serviceCategories", [])}
    gray_zones = engine_result.get("grayZones", [])
    if len(gray_zones) != len(requested_categories):
        raise ValueError("C++ 引擎灰区类别数量与路网标注不一致")
    blind_zone_features = []
    blind_walkway_features = []
    gray_metrics = []
    seen_categories: set[str] = set()
    recommendations = []
    for zone in gray_zones:
        category = zone["category"]
        if category not in requested_categories or category in seen_categories:
            raise ValueError("C++ 引擎返回了未知或重复的灰区类别")
        seen_categories.add(category)
        status = zone["status"]
        expected_status = ("candidate" if requested_categories[category]["dataStatus"] ==
                           "reviewed_online" else "data_insufficient")
        if status != expected_status:
            raise ValueError("C++ 引擎灰区数据状态不一致")
        length = zone["uncoveredLengthMeters"]
        ratio = zone["uncoveredLengthRatio"]
        if status == "data_insufficient":
            if length is not None or ratio is not None or zone["uncoveredEdges"]:
                raise ValueError("数据不足时不能输出灰区断言")
            warnings.append(f"FACILITY_DATA_INCOMPLETE:{category}")
        elif (not isinstance(length, (int, float)) or
              not isinstance(ratio, (int, float)) or
              not math.isfinite(length) or not math.isfinite(ratio) or
              length < 0 or not 0 <= ratio <= 1):
            raise ValueError("C++ 引擎灰区指标无效")
        geometry = zone["displayGeometryMeters"]
        if not isinstance(geometry, dict) or geometry.get("type") != "MultiPolygon":
            raise ValueError("C++ 引擎灰区展示面必须是 MultiPolygon")
        for polygon in geometry["coordinates"]:
            blind_zone_features.append({
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [
                    [_to_bd09(point, origin) for point in ring] for ring in polygon]},
                "properties": {"category": category, "status": status,
                               "approximate": True},
            })
        for edge in zone["uncoveredEdges"]:
            blind_walkway_features.append({
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [
                    _to_bd09(point, origin) for point in edge["pathMeters"]]},
                "properties": {"category": category, "edgeId": edge["edgeId"],
                               "kind": edge["kind"], "status": status},
            })
        gray_metrics.append({"category": category, "status": status,
                             "reachableLengthMeters": zone["reachableLengthMeters"],
                             "uncoveredLengthMeters": length,
                             "uncoveredLengthRatio": ratio})
        if status == "candidate" and length > 0:
            recommendations.append(
                f"{category}：当前等时圈内存在基于在线核查数据的疑似服务灰区，"
                "建议复核入口和设施清单。")
    return {
        "isochrone": {
            "type": "Feature",
            "geometry": {"type": "MultiPolygon", "coordinates": polygons},
            "properties": {"approximate": True, "coordType": "bd09ll"},
        },
        "reachableWalkways": {"type": "FeatureCollection", "features": walkways},
        "durationSamples": [
            {"lng": position[0], "lat": position[1], "durationSeconds": 900}
            for position in (_to_bd09(point, origin)
                             for point in engine_result["frontierMeters"])
        ],
        "facilities": {"type": "FeatureCollection", "features": facilities},
        "blindZones": {"type": "FeatureCollection", "features": blind_zone_features},
        "blindZoneWalkways": {"type": "FeatureCollection",
                              "features": blind_walkway_features},
        "metrics": {
            "reachableNodeCount": engine_result["diagnostics"]["reachableNodeCount"],
            "reachableCrossingCount": engine_result["diagnostics"]["reachableCrossingCount"],
            "facilityCount": len(facilities),
            "reachableFacilityCount": sum(
                bool(feature["properties"]["reachable"]) for feature in facilities
            ),
            "grayZonesByCategory": gray_metrics,
        },
        "recommendations": recommendations,
        "warnings": warnings,
        "metadata": {
            "schemaVersion": 1,
            "engineSchemaVersion": 2,
            "networkSource": network_meta["networkSource"],
            "isochroneApproximate": True,
            "grayZonePolygonsApproximate": True,
            "grayZoneBasis": "15-minute network travel to online-reviewed entrances",
            "originSnapMeters": engine_result["snapDistanceMeters"],
        },
    }
