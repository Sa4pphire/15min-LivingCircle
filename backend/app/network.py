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
    }
    if math.hypot(x_meters, y_meters) < 1 and network.get("originEdgeId"):
        payload["originEdgeId"] = network["originEdgeId"]
    metadata = {
        "originBd09": origin,
        "networkSource": "synthetic" if network.get("synthetic") else "manual",
    }
    return payload, metadata


def build_analysis_result(engine_result: dict[str, Any],
                          network_meta: dict[str, Any]) -> dict[str, Any]:
    origin = network_meta["originBd09"]
    polygons = []
    for ring in engine_result["displayPolygonMeters"]:
        if len(ring) >= 4:
            polygons.append([[_to_bd09(point, origin) for point in ring]])
    walkways = []
    for edge in engine_result["reachableEdges"]:
        walkways.append({
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [_to_bd09(point, origin) for point in edge["pathMeters"]],
            },
            "properties": {"edgeId": edge["edgeId"], "kind": edge["kind"]},
        })
    warnings = list(engine_result["diagnostics"].get("warnings", []))
    if network_meta["networkSource"] == "synthetic":
        warnings.append("SYNTHETIC_NETWORK_NOT_REAL_WORLD")
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
        "facilities": {"type": "FeatureCollection", "features": []},
        "blindZones": {"type": "FeatureCollection", "features": []},
        "metrics": {
            "reachableNodeCount": engine_result["diagnostics"]["reachableNodeCount"],
            "reachableCrossingCount": engine_result["diagnostics"]["reachableCrossingCount"],
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
