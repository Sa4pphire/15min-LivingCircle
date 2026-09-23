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


def load_engine_request(center: CenterPoint) -> tuple[dict[str, Any], dict[str, Any]]:
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
            if not isinstance(bounds[key], (int, float)):
                raise ValueError(f"supportedCenterBoundsMeters.{key} must be numeric")
        for key in ("nodes", "edges"):
            if not isinstance(network[key], list) or not network[key]:
                raise ValueError(f"{key} must be a non-empty list")
        facilities = network.get("facilities", [])
        if not isinstance(facilities, list):
            raise ValueError("facilities must be a list")
        for facility in facilities:
            if not isinstance(facility, dict):
                raise ValueError("facility must be an object")
            if not isinstance(facility.get("id"), str) or not facility["id"]:
                raise ValueError("facility.id must be a non-empty string")
            if (not isinstance(facility.get("accessEdgeId"), str) or
                    not facility["accessEdgeId"]):
                raise ValueError("facility.accessEdgeId must be a non-empty string")
            access_point = facility.get("accessPointMeters")
            if (not isinstance(access_point, list) or len(access_point) != 2 or
                    any(isinstance(value, bool) or
                        not isinstance(value, (int, float)) or
                        not math.isfinite(value) for value in access_point)):
                raise ValueError("facility.accessPointMeters must be a finite [x, y]")
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
        "displayBufferMeters": 15,
        "displayGridStepMeters": 10,
        "nodes": network["nodes"],
        "edges": network["edges"],
        "facilities": facilities,
    }
    if math.hypot(x_meters, y_meters) < 1 and network.get("originEdgeId"):
        payload["originEdgeId"] = network["originEdgeId"]
    metadata = {
        "originBd09": origin,
        "networkSource": "synthetic" if network.get("synthetic") else "manual",
        "facilities": facilities,
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
        if timing["accessEdgeId"] != facility["accessEdgeId"]:
            raise ValueError("C++ 引擎返回的设施接入边不匹配")
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
        facilities.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": _to_bd09(facility["accessPointMeters"], origin),
            },
            "properties": {
                "id": facility_id,
                "accessEdgeId": facility["accessEdgeId"],
                "reachable": reachable,
                "travelTimeSeconds": seconds,
            },
        })
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
        "blindZones": {"type": "FeatureCollection", "features": []},
        "metrics": {
            "reachableNodeCount": engine_result["diagnostics"]["reachableNodeCount"],
            "reachableCrossingCount": engine_result["diagnostics"]["reachableCrossingCount"],
            "facilityCount": len(facilities),
            "reachableFacilityCount": sum(
                bool(feature["properties"]["reachable"]) for feature in facilities
            ),
        },
        "recommendations": [],
        "warnings": warnings,
        "metadata": {
            "schemaVersion": 1,
            "engineSchemaVersion": 2,
            "networkSource": network_meta["networkSource"],
            "isochroneApproximate": True,
            "originSnapMeters": engine_result["snapDistanceMeters"],
        },
    }
