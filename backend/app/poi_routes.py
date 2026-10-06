"""On-demand routes use the completed analysis graph and its bound entrances.

No Baidu calls or mutations of the saved road-network file. Synthetic preview
may estimate a straight parcel connector when no bound entrance is available.
"""
from collections import OrderedDict
import math

from .engine import run_engine
from .network import _to_map_coordinate, _valid_xy
from .pois import _projection


# Only broaden a synthetic destination's LOCAL access, not the walking graph.
# A small distance band includes near-parallel paths without inventing a long
# parcel shortcut simply because some distant street is easier to reach.
ACCESS_DISTANCE_BAND_METERS = 10.0
MAX_ESTIMATED_ENTRANCES = 16
_GEOMETRY_EPSILON = 1e-8


def _segments_intersect(a, b, c, d):
    def cross(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    if (max(a[0], b[0]) < min(c[0], d[0]) - _GEOMETRY_EPSILON or
            max(c[0], d[0]) < min(a[0], b[0]) - _GEOMETRY_EPSILON or
            max(a[1], b[1]) < min(c[1], d[1]) - _GEOMETRY_EPSILON or
            max(c[1], d[1]) < min(a[1], b[1]) - _GEOMETRY_EPSILON):
        return False
    first, second = cross(a, b, c), cross(a, b, d)
    third, fourth = cross(c, d, a), cross(c, d, b)
    return ((first <= _GEOMETRY_EPSILON and second >= -_GEOMETRY_EPSILON or
             second <= _GEOMETRY_EPSILON and first >= -_GEOMETRY_EPSILON) and
            (third <= _GEOMETRY_EPSILON and fourth >= -_GEOMETRY_EPSILON or
             fourth <= _GEOMETRY_EPSILON and third >= -_GEOMETRY_EPSILON))


def _crosses_ordinary_sidewalk(start, finish, edge_id, sidewalks):
    if math.dist(start, finish) <= _GEOMETRY_EPSILON:
        return False
    for sidewalk in sidewalks:
        if sidewalk["id"] == edge_id:
            continue
        for a, b in zip(sidewalk["pathMeters"], sidewalk["pathMeters"][1:]):
            if not _segments_intersect(start, finish, a, b):
                continue
            # A connector may leave an existing junction at its attachment,
            # but may not cross another sidewalk farther along the connector.
            if (_projection(start, a, b)[0] <= _GEOMETRY_EPSILON and
                    _projection(finish, a, b)[0] > _GEOMETRY_EPSILON):
                continue
            return True
    return False


def _access_anchor(edge, point):
    for endpoint, coordinate in (("from", edge["pathMeters"][0]),
                                 ("to", edge["pathMeters"][-1])):
        if edge.get(endpoint) and math.dist(point, coordinate) <= _GEOMETRY_EPSILON:
            return ("node", edge[endpoint])
    return ("edge", edge["id"])


def estimate_poi_access(poi_id: str, point, edges: list[dict]) -> tuple[dict | None, float]:
    """Local candidate entrances; C++ minimizes graph time PLUS access time.

    These parcel connectors remain synthetic/unverified and are destination-only:
    they never connect two graph edges. An ordinary-road destination stays on its
    nearest sidewalk's block/side. Shared-way alternatives cannot cross modeled
    ordinary sidewalks. Unknown walls and water remain a limitation, not proof of
    real access. The returned distance is the nearest candidate's; route() reports
    the distance of the entrance actually selected by C++.
    """
    if not _valid_xy(point):
        return None, 0
    candidates = []
    sidewalks = [edge for edge in edges if edge["kind"] == "sidewalk"]
    for edge in edges:
        if edge["kind"] not in ("sidewalk", "shared_way"):
            continue
        distance, projected = min(
            (_projection(point, first, second)
             for first, second in zip(edge["pathMeters"], edge["pathMeters"][1:])),
            key=lambda candidate: candidate[0])
        candidates.append((distance, edge, projected))
    if not candidates:
        return None, 0
    candidates.sort(key=lambda candidate: (candidate[0], candidate[1]["id"]))
    nearest_distance, nearest_edge, _ = candidates[0]
    entrances, anchors = [], set()
    for distance, edge, projected in candidates:
        if distance > nearest_distance + ACCESS_DISTANCE_BAND_METERS:
            break
        if edge["kind"] != nearest_edge["kind"]:
            continue
        if edge["kind"] == "sidewalk" and edge["id"] != nearest_edge["id"]:
            if (not nearest_edge.get("streetBlockId") or
                    nearest_edge.get("side") not in ("left", "right") or
                    edge.get("streetBlockId") != nearest_edge["streetBlockId"] or
                    edge.get("side") != nearest_edge["side"]):
                continue
        anchor = _access_anchor(edge, projected)
        if anchor in anchors or _crosses_ordinary_sidewalk(projected, point, edge["id"], sidewalks):
            continue
        entrance = {"id": "estimated_straight_access" + (f":{len(entrances)}" if entrances else ""),
                    "accessEdgeId": edge["id"], "streetAccessPointMeters": projected}
        if distance > _GEOMETRY_EPSILON:
            entrance["accessPathMeters"] = [projected, list(point)]
        entrances.append(entrance)
        anchors.add(anchor)
        if len(entrances) >= MAX_ESTIMATED_ENTRANCES:
            break
    if not entrances:
        return None, 0
    return {"id": f"estimated-poi:{poi_id}", "entrances": entrances}, nearest_distance


class RouteUnavailable(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class PoiRouteStore:
    def __init__(self, capacity: int = 16):
        self.capacity = capacity
        self.contexts: OrderedDict[str, tuple[dict, dict, dict]] = OrderedDict()

    def remember(self, analysis_id: str, payload: dict, metadata: dict, report: dict):
        if metadata.get("networkSource") != "synthetic":
            return
        self.contexts[analysis_id] = (payload, metadata, report)
        self.contexts.move_to_end(analysis_id)
        while len(self.contexts) > self.capacity:
            self.contexts.popitem(last=False)

    async def route(self, analysis_id: str, poi_id: str) -> dict:
        context = self.contexts.get(analysis_id)
        if context is None:
            raise RouteUnavailable("ROUTE_CONTEXT_EXPIRED", "该分析的路由上下文不存在或已过期，请重新计算等时圈。")
        payload, metadata, report = context
        feature = next((feature for feature in report.get("poiFacilities", {}).get("features", [])
                        if feature.get("properties", {}).get("id") == poi_id and
                        feature["properties"].get("insideDisplayPolygon") is True), None)
        if feature is None:
            raise RouteUnavailable("POI_NOT_IN_ANALYSIS", "该设施不在当前报告的等时圈候选清单中。")
        record = next((item for item in metadata.get("poiRecords", []) if item["uid"] == poi_id), None)
        facility = next((item for item in payload.get("facilities", [])
                         if record and item["id"] == record.get("engineId")), None)
        base = {"schemaVersion": 1, "analysisId": analysis_id, "poiId": poi_id,
                "algorithm": "dijkstra", "coordType": metadata["coordType"],
                "networkSource": "synthetic", "accessVerified": False}
        estimated_access = facility is None
        access_distance = 0
        if estimated_access:
            point = record.get("localPointMeters") if record else feature["properties"].get("localPointMeters")
            facility, access_distance = estimate_poi_access(poi_id, point, payload["edges"])
        base["destinationAccessMode"] = "estimated_straight_line" if estimated_access else "bound_entrance"
        base["destinationAccessDistanceMeters"] = access_distance
        if estimated_access:
            base["destinationAccessCandidateCount"] = len(facility["entrances"]) if facility else 0
            base["destinationAccessSelection"] = "minimum_total_time_local_candidates"
        if facility is None:
            return {**base, "status": "unmapped", "geometry": None, "segments": [],
                    "message": "该设施缺少有效的局部坐标或可步行路段，暂时无法生成估算接入。"}
        # Only this target is split into the graph. Routing skips all display
        # rasterization and category coverage, while retaining the same origin.
        request = {**payload, "facilities": [facility],
                   "routeFacilityId": facility["id"], "routeOnly": True}
        result = await run_engine(request)
        route = result.get("facilityRoute")
        if not isinstance(route, dict) or route.get("facilityId") != facility["id"]:
            raise ValueError("C++ 引擎缺少匹配的 POI 路径结果，请更新引擎后重试。")
        if route.get("connected") is not True:
            return {**base, "status": "unreachable", "geometry": None, "segments": [],
                    "message": "起点与该设施的接入路段在当前路网中不连通。"}
        if estimated_access:
            selected = next((entrance for entrance in facility["entrances"]
                             if entrance["id"] == route.get("entranceId")), None)
            if selected is None:
                raise ValueError("C++ 引擎缺少匹配的估算接入入口。")
            access_path = selected.get("accessPathMeters", [])
            base["destinationAccessDistanceMeters"] = sum(
                math.dist(a, b) for a, b in zip(access_path, access_path[1:]))
        origin = metadata["originWgs84" if metadata["coordType"] == "wgs84ll" else "originBd09"]

        def convert_path(path):
            if not isinstance(path, list) or len(path) < 2 or any(
                not isinstance(point, list) or len(point) != 2 or any(
                    isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                    for value in point) for point in path
            ):
                raise ValueError("C++ 路径坐标无效")
            return [_to_map_coordinate(point, origin) for point in path]

        coordinates = convert_path(route.get("pathMeters"))
        seconds = route.get("travelTimeSeconds")
        if not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 0:
            raise ValueError("C++ 路径耗时无效")
        segments = []
        for index, segment in enumerate(route.get("segments", [])):
            segments.append({"type": "Feature", "geometry": {
                "type": "LineString", "coordinates": convert_path(segment.get("pathMeters"))},
                "properties": {"id": index, "edgeId": segment.get("edgeId"),
                               "kind": segment.get("kind"),
                               "travelTimeSeconds": segment.get("travelTimeSeconds")}})
        return {**base, "status": "ready", "geometry": {"type": "LineString", "coordinates": coordinates},
                "segments": segments, "travelTimeSeconds": seconds,
                "lengthMeters": route["lengthMeters"], "crossingWaitSeconds": route["crossingWaitSeconds"],
                "withinThreshold": route["withinThreshold"], "entranceId": route.get("entranceId"),
                "destinationCoordinates": coordinates[-1],
                "message": ("最后一段为穿越地块的直线估算，已计入耗时；未核查建筑、围墙或通行权限。"
                            if estimated_access else
                            "路线通往绑定入口；POI 标记点不一定就是入口，路网与入口仍未经现场核实。"),
                "warnings": [*result.get("diagnostics", {}).get("warnings", []),
                             *(["UNVERIFIED_STRAIGHT_LINE_POI_ACCESS"] if estimated_access else [])]}
