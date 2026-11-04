"""One startup-loaded graph/context pair; requests borrow graph data read-only.

The artifact is exactly the file hashed and loaded by C++. Request defaults,
calibration and optional POI records live in a graph-ID-bound companion file.
Neither preparation nor POI lookup reads the current authoring network.
"""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

from .engine_protocol import compact_graph_fields, is_graph_snapshot_id
from .local_alignment import grid_frame_from_asset
from .network import (METERS_PER_DEGREE, UnsupportedAreaError, _contains_point,
                      _facility_entrances, _local_point, _valid_xy,
                      build_engine_payload)
from .region_pois import search_poi_snapshot, validate_poi_snapshot
from .schemas import CenterPoint

MAX_DOCUMENT_BYTES = 20_000_000
_REQUEST_FIELDS = {
    "originMeters", "originEdgeId", "thresholdSeconds", "walkingSpeedMetersPerSecond",
    "crossingWaitSeconds", "maxOriginSnapMeters", "allowOffNetworkOrigin",
    "displayBufferMeters", "displayAreaRadiusMeters", "displayMinHoleAreaSquareMeters",
    "displayGridStepMeters", "facilities", "serviceCategories", "localExperiment",
    "routeFacilityId", "routeOnly", "facilitiesOnly",
}


def _finite(value: Any) -> bool:
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value))


def _sha256_hex(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _read_document(path: Path, *, expected_graph_id: str | None = None) -> tuple[bytes, dict]:
    with path.open("rb") as stream:
        raw = stream.read(MAX_DOCUMENT_BYTES + 1)
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise ValueError("固定图或请求上下文文件超过 20,000,000 字节")
    if expected_graph_id is not None and "sha256:" + hashlib.sha256(raw).hexdigest() != expected_graph_id:
        raise ValueError("Python 固定图原始字节摘要与配置 ID 不一致")
    document = json.loads(raw.decode("utf-8"))
    if not isinstance(document, dict):
        raise ValueError("固定图与请求上下文必须为 JSON 对象")
    return raw, document


def _validate_ring(ring: Any) -> None:
    if (not isinstance(ring, list) or len(ring) < 4 or ring[0] != ring[-1]
            or any(not _valid_xy(point) for point in ring)):
        raise ValueError("固定图范围必须为有限坐标的闭合环")


def _artifact_network(artifact: dict) -> dict:
    """Validate Python's preparation contract; C++ owns full topology validation."""
    for field, expected in (("schemaVersion", 2), ("snapshotFormatVersion", 1),
                            ("normalizationVersion", 1)):
        if type(artifact.get(field)) is not int or artifact[field] != expected:
            raise ValueError(f"固定图 {field} 版本不支持")
    if _REQUEST_FIELDS.intersection(artifact):
        raise ValueError("固定图产物不得包含请求字段")
    if (not isinstance(artifact.get("regionId"), str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", artifact["regionId"])):
        raise ValueError("固定图 regionId 无效")
    if type(artifact.get("synthetic")) is not bool:
        raise ValueError("固定图 synthetic 必须为布尔值")
    frame = artifact.get("coordinateFrame")
    if not isinstance(frame, dict) or frame.get("geographicType") not in ("bd09ll", "wgs84ll"):
        raise ValueError("固定图地理坐标类型无效")
    origin = frame.get("origin")
    if (not isinstance(origin, dict) or not all(_finite(origin.get(k)) for k in ("lng", "lat"))
            or not -180 <= origin["lng"] <= 180 or not -85 < origin["lat"] < 85):
        raise ValueError("固定图原点超出 Python 局部坐标支持范围")
    if "sourcePackageVersionId" in artifact and not _sha256_hex(artifact["sourcePackageVersionId"]):
        raise ValueError("固定图来源版本必须为小写 SHA-256")
    bounds = artifact.get("supportedCenterBoundsMeters")
    if (not isinstance(bounds, dict)
            or not all(_finite(bounds.get(k)) for k in ("minX", "maxX", "minY", "maxY"))
            or bounds["minX"] > bounds["maxX"] or bounds["minY"] > bounds["maxY"]):
        raise ValueError("固定图范围无效")
    areas = artifact.get("publicWalkableAreasMeters", [])
    if not isinstance(areas, list) or (not artifact["synthetic"] and not areas):
        raise ValueError("真实固定图需要公共步行面")
    for ring in areas:
        _validate_ring(ring)
    if "supportedCenterPolygonMeters" in artifact:
        _validate_ring(artifact["supportedCenterPolygonMeters"])
    nodes, edges = artifact.get("nodes"), artifact.get("edges")
    if (not isinstance(nodes, list) or not 1 <= len(nodes) <= 30_000
            or not isinstance(edges, list) or not 1 <= len(edges) <= 50_000):
        raise ValueError("固定图节点/边数量无效")
    node_ids: set[str] = set()
    for node in nodes:
        if (not isinstance(node, dict) or not isinstance(node.get("id"), str) or not node["id"]
                or node["id"] in node_ids or not _finite(node.get("xMeters"))
                or not _finite(node.get("yMeters"))):
            raise ValueError("固定图节点无效")
        node_ids.add(node["id"])
    edge_ids: set[str] = set()
    for edge in edges:
        if (not isinstance(edge, dict) or not isinstance(edge.get("id"), str) or not edge["id"]
                or edge["id"] in edge_ids or not isinstance(edge.get("from"), str)
                or not isinstance(edge.get("to"), str) or edge["from"] not in node_ids
                or edge["to"] not in node_ids or edge.get("kind") not in ("walkway", "turn", "crossing")
                or (edge["kind"] == "walkway" and edge.get("accessMode") not in ("shared", "separated"))
                or (edge["kind"] != "walkway" and "accessMode" in edge)
                or not isinstance(edge.get("pathMeters"), list) or len(edge["pathMeters"]) < 2
                or any(not _valid_xy(point) for point in edge["pathMeters"])):
            raise ValueError("固定图道路必须为规范化的有效路径")
        edge_ids.add(edge["id"])
    origin_key = "originWgs84" if frame["geographicType"] == "wgs84ll" else "originBd09"
    return {"schemaVersion": 2, "synthetic": artifact["synthetic"], origin_key: origin,
            "nodes": nodes, "edges": edges, "supportedCenterBoundsMeters": bounds,
            "publicWalkableAreasMeters": areas,
            **({"supportedCenterPolygonMeters": artifact["supportedCenterPolygonMeters"]}
               if "supportedCenterPolygonMeters" in artifact else {})}


def _validate_request_defaults(document: dict, network: dict) -> None:
    categories, facilities = document.get("serviceCategories"), document.get("facilities")
    if (not isinstance(categories, list) or len(categories) > 10
            or (not network["synthetic"] and not categories) or not isinstance(facilities, list)):
        raise ValueError("固定请求模板的设施/类别无效")
    category_ids = set()
    for category in categories:
        if (not isinstance(category, dict) or not isinstance(category.get("id"), str)
                or not category["id"] or category["id"] in category_ids
                or category.get("dataStatus") not in ("reviewed_online", "incomplete")):
            raise ValueError("固定请求模板类别无效")
        category_ids.add(category["id"])
    edge_ids = {edge["id"] for edge in network["edges"]}
    facility_ids = set()
    for facility in facilities:
        if (not isinstance(facility, dict) or not isinstance(facility.get("id"), str)
                or not facility["id"] or facility["id"] in facility_ids):
            raise ValueError("固定请求模板设施 ID 无效")
        facility_ids.add(facility["id"])
        if "entrances" in facility or not network["synthetic"]:
            if (facility.get("category") not in category_ids
                    or not isinstance(facility.get("entrances"), list) or not facility["entrances"]
                    or "accessEdgeId" in facility or "accessPointMeters" in facility):
                raise ValueError("固定请求模板设施入口无效")
        entrances = _facility_entrances(facility)
        entrance_ids = set()
        for entrance in entrances:
            if (not isinstance(entrance, dict) or not isinstance(entrance.get("id"), str)
                    or not entrance["id"] or entrance["id"] in entrance_ids
                    or not isinstance(entrance.get("accessEdgeId"), str)
                    or entrance["accessEdgeId"] not in edge_ids
                    or not _valid_xy(entrance.get("streetAccessPointMeters"))):
                raise ValueError("固定请求模板入口引用无效")
            entrance_ids.add(entrance["id"])
            if "accessPathMeters" in entrance:
                path = entrance["accessPathMeters"]
                if (not isinstance(path, list) or len(path) < 2
                        or any(not _valid_xy(point) for point in path)
                        or path[0] != entrance["streetAccessPointMeters"]):
                    raise ValueError("固定请求模板接入路径无效")
    if "originEdgeId" in document and (not isinstance(document["originEdgeId"], str)
                                      or document["originEdgeId"] not in edge_ids):
        raise ValueError("固定请求模板起点道路引用无效")


@dataclass(frozen=True)
class FixedGraphContext:
    graph_id: str
    region_id: str
    synthetic: bool
    _network: dict
    _defaults: dict
    _metadata: dict
    _poi_frame: tuple | None
    _pois: dict | None

    def prepare_request(self, center: CenterPoint, origin_edge_id: str | None = None
                        ) -> tuple[dict[str, Any], dict[str, Any]]:
        if center.coordType != self._metadata["coordType"]:
            raise UnsupportedAreaError(f"固定图需要 {self._metadata['coordType']} 坐标，收到 {center.coordType}")
        origin_key = "originWgs84" if center.coordType == "wgs84ll" else "originBd09"
        x, y = _local_point(center, self._network[origin_key])
        if not self.synthetic:
            if not any(_contains_point(ring, x, y) for ring in self._network["publicWalkableAreasMeters"]):
                raise UnsupportedAreaError("中心点不在已核实的公共步行空间内")
        elif "supportedCenterPolygonMeters" in self._network:
            if not _contains_point(self._network["supportedCenterPolygonMeters"], x, y):
                raise UnsupportedAreaError("中心点不在合成演示选区内")
        bounds = self._network["supportedCenterBoundsMeters"]
        if not (bounds["minX"] <= x <= bounds["maxX"] and bounds["minY"] <= y <= bounds["maxY"]):
            raise UnsupportedAreaError("该中心点不在固定版本路网范围内")
        # The large graph arrays are read-only borrowed data. Everything that
        # enrichment changes belongs to this request, never to the template.
        request_network = {**self._network, **deepcopy(self._defaults)}
        payload = build_engine_payload(request_network, x, y, origin_edge_id)
        metadata = deepcopy(self._metadata)
        metadata.update({"facilities": payload["facilities"],
                         "serviceCategories": payload["serviceCategories"],
                         "displayGridStepMeters": payload["displayGridStepMeters"],
                         "displayGeometryParameters": {key: payload[key] for key in (
                             "thresholdSeconds", "walkingSpeedMetersPerSecond", "displayBufferMeters",
                             "displayAreaRadiusMeters", "displayMinHoleAreaSquareMeters", "displayGridStepMeters")},
                         "_fixedGraphContext": self})
        return payload, metadata

    def poi_frame(self) -> tuple | None:
        return self._poi_frame

    def search_pois(self, center, radius, category_ids, bounds=None):
        if self._pois is None:
            return None
        return search_poi_snapshot(self._pois, self._metadata, center, radius, category_ids,
                                   bounds=bounds,
                                   project_bd09=self._poi_frame[0] if self._poi_frame else None)


def _make_context(artifact: dict, companion: dict, graph_id: str,
                  artifact_name: str, source_bytes: int) -> FixedGraphContext:
    network = _artifact_network(artifact)
    if (type(companion.get("contextFormatVersion")) is not int
            or companion["contextFormatVersion"] != 1 or companion.get("graphId") != graph_id
            or not _sha256_hex(companion.get("networkRevision"))):
        raise ValueError("固定请求上下文版本、图身份或来源摘要不匹配")
    _validate_request_defaults(companion, network)
    coord_type = artifact["coordinateFrame"]["geographicType"]
    origin_key = "originWgs84" if coord_type == "wgs84ll" else "originBd09"
    metadata = {"coordType": coord_type, origin_key: network[origin_key],
                "regionId": artifact["regionId"], "graphId": graph_id,
                "engineInputMode": "fixed_graph", "networkRevision": companion["networkRevision"],
                "networkSource": "synthetic" if artifact["synthetic"] else "manual",
                "networkFile": artifact_name, "networkNodeCount": len(network["nodes"]),
                "networkEdgeCount": len(network["edges"]), "graphSourceBytes": source_bytes}
    if companion.get("coverageStatus") == "bounded-model":
        metadata.update({"coverageStatus": "bounded-model",
                         "coverageBoundsMeters": network["supportedCenterBoundsMeters"]})
    if "sourcePackageVersionId" in artifact:
        metadata["packageVersionId"] = artifact["sourcePackageVersionId"]
    frame = None
    if "alignment" in companion:
        alignment = companion["alignment"]
        origin = network[origin_key]
        if (not isinstance(alignment, dict)
                or alignment.get("input", {}).get(origin_key) != [origin["lng"], origin["lat"]]):
            raise ValueError("固定校准数据与图原点不一致")
        frame = grid_frame_from_asset(metadata, alignment)
        if coord_type == "wgs84ll" and frame is None:
            raise ValueError("固定 WGS84 校准数据缺少可用网格")
    if coord_type == "bd09ll":
        origin = network[origin_key]
        def project(point):
            return [(point[0] - origin["lng"]) * METERS_PER_DEGREE * math.cos(math.radians(origin["lat"])),
                    (point[1] - origin["lat"]) * METERS_PER_DEGREE]
        frame = project, "fixed_native_bd09_local_meters"
    pois = companion.get("pois")
    if pois is not None:
        validate_poi_snapshot(pois, metadata)
    defaults = {key: companion[key] for key in ("facilities", "serviceCategories", "originEdgeId")
                if key in companion}
    return FixedGraphContext(graph_id, artifact["regionId"], artifact["synthetic"],
                             network, defaults, metadata, frame, pois)


def load_fixed_graph_context(artifact_path: Path, expected_graph_id: str,
                             context_path: Path) -> FixedGraphContext:
    if not is_graph_snapshot_id(expected_graph_id):
        raise ValueError("固定图 ID 必须为 sha256: 加 64 位小写十六进制")
    raw, artifact = _read_document(artifact_path, expected_graph_id=expected_graph_id)
    _, companion = _read_document(context_path)
    return _make_context(artifact, companion, expected_graph_id, artifact_path.name, len(raw))


def export_snapshot_documents(network: dict, *, region_id: str, network_revision: str,
                              source_package_version_id: str | None = None,
                              alignment: dict | None = None, pois: dict | None = None
                              ) -> tuple[bytes, bytes, str]:
    """Prepare both files before writing; no live request state enters the artifact."""
    if "localExperiment" in network:
        raise ValueError("局部实验路网不能导出为固定图")
    # authoringWorkspace remains set after roads are drawn. It is provenance,
    # not a readiness check; the artifact's nonempty graph checks reject blanks.
    if network.get("schemaVersion") != 2 or type(network.get("synthetic", False)) is not bool:
        raise ValueError("导出需要 v2 路网和布尔 synthetic 标识")
    synthetic = network.get("synthetic", False)
    coord_type = "wgs84ll" if synthetic and "originWgs84" in network else "bd09ll"
    origin_key = "originWgs84" if coord_type == "wgs84ll" else "originBd09"
    bounds = network["supportedCenterBoundsMeters"]
    bounded = network.get("selectionSource") == "network-path-extent"
    if bounded:
        from .region_package import network_bounds
        bounds = network_bounds(network)
    artifact = {"schemaVersion": 2, "snapshotFormatVersion": 1, "normalizationVersion": 1,
                "regionId": region_id, "synthetic": synthetic,
                "coordinateFrame": {"geographicType": coord_type, "origin": network[origin_key]},
                "supportedCenterBoundsMeters": bounds, **compact_graph_fields(network)}
    if "publicWalkableAreasMeters" in network:
        artifact["publicWalkableAreasMeters"] = network["publicWalkableAreasMeters"]
    # The old preparation path ignores a synthetic polygon for path-extent selection.
    if "supportedCenterPolygonMeters" in network and not (synthetic and bounded):
        artifact["supportedCenterPolygonMeters"] = network["supportedCenterPolygonMeters"]
    if source_package_version_id is not None:
        artifact["sourcePackageVersionId"] = source_package_version_id
    raw = json.dumps(artifact, ensure_ascii=False, allow_nan=False,
                     separators=(",", ":"), sort_keys=True).encode("utf-8")
    graph_id = "sha256:" + hashlib.sha256(raw).hexdigest()
    companion = {"contextFormatVersion": 1, "graphId": graph_id,
                 "networkRevision": network_revision,
                 "facilities": network.get("facilities", []),
                 "serviceCategories": network.get("serviceCategories", [])}
    if network.get("originEdgeId"):
        companion["originEdgeId"] = network["originEdgeId"]
    if bounded:
        companion["coverageStatus"] = "bounded-model"
    if alignment is not None:
        companion["alignment"] = alignment
    if pois is not None:
        companion["pois"] = pois
    context_raw = json.dumps(companion, ensure_ascii=False, allow_nan=False,
                             separators=(",", ":"), sort_keys=True).encode("utf-8")
    if max(len(raw), len(context_raw)) > MAX_DOCUMENT_BYTES:
        raise ValueError("导出的固定图或请求上下文超过 20,000,000 字节")
    # Validate the actual serialized documents used by future Python startups.
    # Full C++ graph/topology validation still takes place during C++ startup.
    _make_context(json.loads(raw), json.loads(context_raw), graph_id, "snapshot.json", len(raw))
    return raw, context_raw, graph_id
