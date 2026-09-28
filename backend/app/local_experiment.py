"""Independent, short-range experiment on an explicitly bounded local graph.

These segments are not the 15-minute result and must not be merged into the
Baidu-backed report. Missing topology or facility inventory is uncertainty,
not evidence that a neighborhood lacks a service.
"""

from pathlib import Path
from typing import Any

from .network import (UnsupportedAreaError, _to_map_coordinate, _valid_xy,
                      load_engine_request)
from .schemas import CenterPoint
from .settings import settings


LOCAL_THRESHOLD_SECONDS = 180
_REVIEW_STATES = {"verified", "incomplete"}
_SEGMENT_GROUPS = (
    ("coveredEdges", "covered", "已覆盖"),
    ("candidateUncoveredEdges", "candidate_uncovered", "候选未覆盖"),
    ("unknownEdges", "unknown", "未知"),
)


def load_local_experiment_request(
    center: CenterPoint,
    origin_edge_id: str | None = None,
    network_path: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build a 180-second engine request; never use the normal 900-second loader as-is."""
    path = network_path if network_path is not None else settings.local_experiment_network_path
    payload, metadata = load_engine_request(
        center, origin_edge_id, network_path=path, allow_local_experiment=True)
    try:
        if "localExperiment" in metadata:
            experiment = metadata["localExperiment"]
        elif metadata["networkSource"] == "synthetic":
            # The existing model has no checked cut exits. Do not manufacture
            # them, and never turn missing facility data into a gray-zone claim.
            experiment = {"boundaryNodeIds": [], "topologyStatus": "incomplete"}
        else:
            raise ValueError("real local graph needs localExperiment")
        if not isinstance(experiment, dict):
            raise ValueError("localExperiment must be an object")
        boundary_ids = experiment.get("boundaryNodeIds")
        if not isinstance(boundary_ids, list) or any(
            not isinstance(node_id, str) or not node_id for node_id in boundary_ids
        ) or len(boundary_ids) != len(set(boundary_ids)):
            raise ValueError("localExperiment.boundaryNodeIds must contain unique node IDs")
        valid_node_ids = {node["id"] for node in payload["nodes"]}
        if not set(boundary_ids) <= valid_node_ids:
            raise ValueError("localExperiment.boundaryNodeIds contains an unknown node")
        topology_status = experiment.get("topologyStatus", "incomplete")
        if topology_status not in _REVIEW_STATES:
            raise ValueError("localExperiment.topologyStatus must be verified or incomplete")
        categories = payload["serviceCategories"]
        for category in categories:
            inventory_status = category.get("localInventoryStatus", "incomplete")
            if inventory_status not in _REVIEW_STATES:
                raise ValueError("service category localInventoryStatus is invalid")
            category["localInventoryStatus"] = inventory_status
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise UnsupportedAreaError(f"局部实验路网文件无效：{exc}") from exc

    payload["thresholdSeconds"] = LOCAL_THRESHOLD_SECONDS
    # Local analysis must start on a known public edge even for synthetic tests.
    payload["allowOffNetworkOrigin"] = False
    payload["maxOriginSnapMeters"] = 5
    payload["localExperiment"] = {
        "boundaryNodeIds": boundary_ids,
        "topologyStatus": topology_status,
    }
    metadata["localExperiment"] = payload["localExperiment"]
    metadata["serviceCategories"] = categories
    return payload, metadata


def _edge_feature(edge: dict[str, Any], origin: dict[str, float],
                  properties: dict[str, Any]) -> dict[str, Any]:
    path = edge["pathMeters"]
    if (not isinstance(path, list) or len(path) < 2 or
            any(not _valid_xy(point) for point in path)):
        raise ValueError("C++ 引擎返回的局部街段路径无效")
    return {
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": [_to_map_coordinate(point, origin) for point in path],
        },
        "properties": {"edgeId": edge["edgeId"], "kind": edge["kind"], **properties},
    }


def build_local_experiment_result(
    engine_result: dict[str, Any], network_meta: dict[str, Any]
) -> dict[str, Any]:
    """Convert exact street segments only; no isochrone/gray polygon or coverage ratio."""
    coord_type = network_meta.get("coordType", "bd09ll")
    origin = network_meta["originWgs84" if coord_type == "wgs84ll" else "originBd09"]
    categories = network_meta["serviceCategories"]
    expected_ids = {category["id"] for category in categories}
    zones = engine_result["localGrayZones"]
    if not isinstance(zones, list) or len(zones) != len(expected_ids):
        raise ValueError("C++ 引擎局部实验类别数量与标注不一致")

    reachable = [
        _edge_feature(edge, origin, {}) for edge in engine_result["reachableEdges"]
    ]
    features: list[dict[str, Any]] = []
    seen: set[str] = set()
    for zone in zones:
        category_id = zone["category"]
        if category_id not in expected_ids or category_id in seen:
            raise ValueError("C++ 引擎返回了未知或重复的局部实验类别")
        seen.add(category_id)
        for key, classification, label in _SEGMENT_GROUPS:
            segments = zone[key]
            if not isinstance(segments, list):
                raise ValueError(f"C++ 引擎局部街段 {key} 无效")
            for edge in segments:
                features.append(_edge_feature(edge, origin, {
                    "category": category_id,
                    "classification": classification,
                    "classificationLabel": label,
                }))

    warnings = list(engine_result["diagnostics"].get("warnings", []))
    if network_meta["networkSource"] == "synthetic":
        warnings.append("SYNTHETIC_NETWORK_NOT_REAL_WORLD")
    if network_meta["localExperiment"]["topologyStatus"] != "verified":
        warnings.append("LOCAL_TOPOLOGY_NOT_VERIFIED")
    if any(category["localInventoryStatus"] != "verified" for category in categories):
        warnings.append("LOCAL_FACILITY_INVENTORY_NOT_VERIFIED")
    if not categories:
        warnings.append("LOCAL_FACILITY_DATA_NOT_PROVIDED")
    if (network_meta["localExperiment"]["topologyStatus"] != "verified" and
            not network_meta["localExperiment"]["boundaryNodeIds"]):
        warnings.append("LOCAL_BOUNDARY_NOT_MARKED")
    return {
        "mode": "local_experiment",
        "label": "3 分钟局部路网实验",
        "thresholdSeconds": LOCAL_THRESHOLD_SECONDS,
        "notForMainReport": True,
        "reachableWalkways": {"type": "FeatureCollection", "features": reachable},
        "categorySegments": {"type": "FeatureCollection", "features": features},
        "warnings": warnings,
        "metadata": {
            "engineSchemaVersion": 2,
            "networkSource": network_meta["networkSource"],
            "coordType": coord_type,
            "networkFile": network_meta.get("networkFile"),
            "networkNodeCount": network_meta.get("networkNodeCount"),
            "networkEdgeCount": network_meta.get("networkEdgeCount"),
            "grayZoneStatus": "classified_segments" if categories else "data_insufficient",
            "topologyStatus": network_meta["localExperiment"]["topologyStatus"],
            "facilityInventoryStatusByCategory": {
                category["id"]: category["localInventoryStatus"] for category in categories
            },
            "boundaryNodeIds": network_meta["localExperiment"]["boundaryNodeIds"],
            "scope": "已标注的局部步行路网；不代表完整 15 分钟生活圈",
        },
    }
