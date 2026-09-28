"""Baidu POI -> cached candidates -> explicitly unverified C++ demo accesses.

POI centroids are display points, NOT entrances. No parcel-crossing access path
is invented. Online lists never certify a complete inventory or a real gray zone.
"""

import asyncio
import math
import sqlite3

from .baidu.client import BaiduClient
from .baidu.errors import BaiduApiError
from .network import METERS_PER_DEGREE, _contains_point, _to_map_coordinate
from .cache import SharedBaiduCache
from .schemas import CenterPoint
from .settings import settings


CATEGORIES = {
    "education": {"label": "学校", "query": "学校$幼儿园"},
    "healthcare": {"label": "医院", "query": "医院$社区卫生服务中心"},
    "shopping": {"label": "超市", "query": "超市$便利店"},
    "public_service": {"label": "公共服务", "query": "街道办事处$社区事务受理服务中心$派出所$邮局"},
}
GRID_METERS = 500


def _grid_query(center: CenterPoint, radius: int):
    # Fixed worldwide bins, not rounding relative to the changing query center.
    lat_step = GRID_METERS / METERS_PER_DEGREE
    lat = (math.floor(center.lat / lat_step) + 0.5) * lat_step
    lng_step = GRID_METERS / (METERS_PER_DEGREE * max(0.01, math.cos(math.radians(lat))))
    lng = (math.floor(center.lng / lng_step) + 0.5) * lng_step
    # Query contains the requested circle even when the center moves inside a bin.
    return (round(lng, 9), round(lat, 9)), radius + 360


class PoiService:
    def __init__(self, *, client=None, cache=None):
        shared_cache = cache or SharedBaiduCache(
            settings.analysis_cache_dir, settings.poi_cache_path,
            settings.poi_cache_ttl_hours * 3600, settings.poi_cache_stale_hours * 3600)
        self.client = client or BaiduClient()
        self.client.enable_cache(shared_cache)
        self.stats = self.client.cache_stats

    async def search(self, center: CenterPoint, radius: int, category_ids, *, refresh=False):
        query_center, query_radius = _grid_query(center, radius)
        pages = max(1, min(8, settings.poi_max_pages))
        outcomes = {}
        records = {}

        async def category_search(category_id):
            outcome = {"category": category_id, "paginationComplete": False,
                       "pages": 0, "discardedCount": 0, "error": None}
            outcomes[category_id] = outcome
            for number in range(pages):
                try:
                    page = await self.client.search_poi_page(
                        CATEGORIES[category_id]["query"], query_center, query_radius,
                        page_num=number, coord_type=center.coordType, refresh=refresh)
                except (BaiduApiError, OSError, TimeoutError, sqlite3.Error) as exc:
                    outcome["error"] = str(exc)
                    return
                outcome["pages"] += 1
                outcome["discardedCount"] += page["discardedCount"]
                for item in page["items"]:
                    existing = records.get(item["uid"])
                    if existing is None:
                        records[item["uid"]] = {**item, "category": category_id,
                                                "categories": [category_id]}
                    elif category_id not in existing["categories"]:
                        existing["categories"].append(category_id)
                if page["rawCount"] < 20 or (page["total"] is not None and
                                             (number + 1) * 20 >= page["total"]):
                    # Baidu total is capped; reaching 150 is not an exhaustive census.
                    outcome["paginationComplete"] = page["total"] is None or page["total"] < 150
                    return

        tasks = [asyncio.create_task(category_search(key)) for key in dict.fromkeys(category_ids)]
        try:
            _, pending = await asyncio.wait(tasks, timeout=settings.poi_budget_seconds)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            completed = await asyncio.gather(*tasks, return_exceptions=True)
        for key, outcome in zip(dict.fromkeys(category_ids), completed):
            if isinstance(outcome, Exception):
                outcomes[key]["error"] = "POI 响应处理失败，数据不足"
        for outcome in outcomes.values():
            if not outcome["paginationComplete"] and outcome["error"] is None:
                outcome["error"] = "检索达到分页或时间上限，清单可能不完整"
        unavailable = all(outcome["pages"] == 0 for outcome in outcomes.values())
        partial = any(outcome["error"] for outcome in outcomes.values()) or self.stats["stalePages"] > 0
        order = list(dict.fromkeys(category_ids))
        for record in records.values():
            record["categories"].sort(key=order.index)
            record["category"] = record["categories"][0]
        return sorted(records.values(), key=lambda item: item["uid"]), {
            "provider": "baidu_place_v2", "status": "unavailable" if unavailable else "partial" if partial else "ready",
            "coordinateSystem": "bd09ll", "queryCenter": list(query_center),
            "queryInputCoordType": center.coordType, "queryRadiusMeters": query_radius,
            "gridMeters": GRID_METERS, "maxPagesPerCategory": pages,
            "cacheTtlHours": settings.poi_cache_ttl_hours,
            "oldestFetchedAt": min(self.client.cache_fetched_times) if self.client.cache_fetched_times else None,
            "cacheBackend": "shared_baidu_json",
            "categories": [outcomes[key] for key in dict.fromkeys(category_ids)],
            "inventoryVerified": False, **self.stats,
        }

    async def frame(self, metadata):
        """Align BD-09 candidates to the meter graph with cached FORWARD calibration.

        No unsupported Baidu->GPS API is called. The affine plane is approximate,
        and native BD-09 coordinates are retained for the real map's POI layer.
        """
        coord_type = metadata["coordType"]
        origin = metadata["originWgs84" if coord_type == "wgs84ll" else "originBd09"]
        if coord_type == "bd09ll":
            def project(point):
                return [(point[0] - origin["lng"]) * METERS_PER_DEGREE * math.cos(math.radians(origin["lat"])),
                        (point[1] - origin["lat"]) * METERS_PER_DEGREE]
            return project, "native_bd09_local_meters"
        anchors = [_to_map_coordinate(point, origin) for point in ([0, 0], [1000, 0], [0, 1000])]
        converted = await self.client.convert_coordinates([tuple(point) for point in anchors], "wgs84ll")
        a, east, north = converted
        ex, ey = east[0] - a[0], east[1] - a[1]
        nx, ny = north[0] - a[0], north[1] - a[1]
        determinant = ex * ny - ey * nx
        if not math.isfinite(determinant) or abs(determinant) < 1e-14:
            raise BaiduApiError("百度正向坐标校准无效")
        def project(point):
            dx, dy = point[0] - a[0], point[1] - a[1]
            return [1000 * (dx * ny - dy * nx) / determinant,
                    1000 * (ex * dy - ey * dx) / determinant]
        return project, "approximate_baidu_forward_affine"


def _projection(point, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    ratio = max(0, min(1, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length2)) if length2 else 0
    projected = [a[0] + ratio * dx, a[1] + ratio * dy]
    return math.dist(point, projected), projected


class AccessIndex:
    def __init__(self, edges, tolerance):
        self.cells = {}
        self.tolerance = tolerance
        for edge in edges:
            if edge["kind"] not in ("sidewalk", "shared_way"):
                continue
            for a, b in zip(edge["pathMeters"], edge["pathMeters"][1:]):
                for x in range(math.floor((min(a[0], b[0]) - tolerance) / 20),
                               math.floor((max(a[0], b[0]) + tolerance) / 20) + 1):
                    for y in range(math.floor((min(a[1], b[1]) - tolerance) / 20),
                                   math.floor((max(a[1], b[1]) + tolerance) / 20) + 1):
                        self.cells.setdefault((x, y), []).append((edge, a, b))

    def match(self, point):
        candidates = {}
        for edge, a, b in self.cells.get((math.floor(point[0] / 20), math.floor(point[1] / 20)), []):
            distance, projected = _projection(point, a, b)
            limit = min(self.tolerance, edge.get("widthMeters", 0) / 2) if edge["kind"] == "shared_way" else self.tolerance
            if distance <= limit and (edge["id"] not in candidates or distance < candidates[edge["id"]][0]):
                candidates[edge["id"]] = (distance, edge, projected)
        ordered = sorted(candidates.values(), key=lambda item: (item[0], item[1]["id"]))
        if not ordered:
            return None, "not_on_modeled_way"
        distance, edge, projected = ordered[0]
        if edge["kind"] == "sidewalk" and any(
            other["kind"] == "sidewalk" and other.get("streetBlockId") == edge.get("streetBlockId") and
            other.get("side") != edge.get("side") and abs(d - distance) <= 1.5
            for d, other, _ in ordered[1:]
        ):
            return None, "ambiguous_side"
        return (edge, point if edge["kind"] == "shared_way" else projected, distance), "mapped_unverified"


async def enrich_engine_pois(payload: dict, metadata: dict, center: CenterPoint, *, refresh=False):
    """No graph-file mutation: enrich this request only. Offline mode still works."""
    category_map = {item["id"]: dict(item) for item in payload.get("serviceCategories", [])}
    for key in CATEGORIES:
        category_map[key] = {"id": key, "dataStatus": "incomplete", "localInventoryStatus": "incomplete"}
    payload["serviceCategories"] = list(category_map.values())
    metadata["serviceCategories"] = payload["serviceCategories"]
    service = None
    records, info = [], {"status": "unavailable", "provider": "baidu_place_v2"}
    try:
        service = PoiService()
        records, info = await service.search(center, math.ceil(2 * payload["thresholdSeconds"] *
                                                              payload["walkingSpeedMetersPerSecond"]), CATEGORIES,
                                             refresh=refresh)
        if records:
            project, alignment = await asyncio.wait_for(service.frame(metadata), timeout=5)
            info["alignment"] = alignment
            index = AccessIndex(payload["edges"], max(0, min(5, settings.poi_snap_meters)))
            facilities = list(payload.get("facilities", []))
            existing = {item["id"] for item in facilities}
            for record in records:
                record["localPointMeters"] = project([record["lng"], record["lat"]])
                record["engineId"] = "baidu:" + record["uid"]
                if "navigationPoint" not in record:
                    record["accessStatus"] = "missing_navigation_point"
                    continue
                match, status = index.match(project(record["navigationPoint"]))
                record["accessStatus"] = status
                if match is not None:
                    edge, point, distance = match
                    record["snapDistanceMeters"] = distance
                    record["accessEdgeId"] = edge["id"]
                    if record["engineId"] not in existing:
                        facilities.append({"id": record["engineId"], "category": record["category"],
                                           "name": record["name"], "source": "baidu", "accessVerified": False,
                                           "entrances": [{"id": "baidu_navigation_point", "accessEdgeId": edge["id"],
                                                          "streetAccessPointMeters": point}]})
                        existing.add(record["engineId"])
            payload["facilities"] = facilities
            metadata["facilities"] = facilities
    except (BaiduApiError, OSError, TimeoutError, ValueError, sqlite3.Error) as exc:
        info["status"] = "partial" if records else "unavailable"
        info["error"] = str(exc)
        for record in records:
            record.setdefault("accessStatus", "coordinate_alignment_unavailable")
    finally:
        if service is not None:
            await service.client.aclose()
    if service is not None:
        info.update(service.stats)
    metadata["poiRecords"] = records
    metadata["poi"] = {**info, "networkOrigin": metadata.get("originWgs84", metadata.get("originBd09")),
                       "inventoryVerified": False,
                       "accessVerified": False, "candidateCount": len(records)}


def _in_display(point, geometry):
    if point is None:
        return None
    return any(_contains_point(polygon[0], *point) and not any(
        _contains_point(hole, *point) for hole in polygon[1:])
        for polygon in geometry.get("coordinates", []) if polygon)


def add_poi_result(report: dict, engine_result: dict, metadata: dict):
    if "poi" not in metadata:
        return report
    timings = {item["id"]: item for item in engine_result.get("facilityTravelTimes", [])}
    local_mode = report.get("mode") == "local_experiment"
    features = []
    summary = []
    for record in metadata.get("poiRecords", []):
        timing = timings.get(record.get("engineId"))
        point = record.get("localPointMeters")
        inside = None if local_mode else _in_display(point, engine_result.get("displayGeometryMeters", {}))
        near = None
        if local_mode and point is not None:
            near = any(_projection(point, a, b)[0] <= 15 for edge in engine_result["reachableEdges"]
                       for a, b in zip(edge["pathMeters"], edge["pathMeters"][1:]))
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [record["lng"], record["lat"]]},
                         "properties": {"id": record["uid"], "name": record["name"], "address": record["address"],
                                        "category": record["category"], "categories": record["categories"],
                                        "categoryLabel": CATEGORIES[record["category"]]["label"],
                                        "source": "baidu", "coordType": "bd09ll", "localPointMeters": point,
                                        "insideDisplayPolygon": inside, "nearReachableWalkway": near,
                                        "accessStatus": record.get("accessStatus", "coordinate_alignment_unavailable"),
                                        "accessVerified": False,
                                        "modelReachable": timing["reachable"] if timing else None,
                                        "modelTravelTimeSeconds": timing["travelTimeSeconds"] if timing else None}})
    for key, category in CATEGORIES.items():
        members = [feature["properties"] for feature in features if key in feature["properties"]["categories"]]
        summary.append({"category": key, "label": category["label"], "queriedCount": len(members),
                        "insideDisplayCount": sum(item["insideDisplayPolygon"] is True for item in members),
                        "nearReachableWalkwayCount": sum(item["nearReachableWalkway"] is True for item in members),
                        "modelReachableCount": sum(item["modelReachable"] is True for item in members),
                        "unmappedCount": sum(item["modelReachable"] is None for item in members),
                        "inventoryStatus": "incomplete"})
    report["poiFacilities"] = {"type": "FeatureCollection", "coordType": "bd09ll", "features": features}
    report["poiCategories"] = summary
    report["metadata"]["poi"] = metadata["poi"]
    report["warnings"].append("BAIDU_POI_INVENTORY_AND_ACCESSES_UNVERIFIED")
    if metadata["poi"]["status"] != "ready":
        report["warnings"].append("BAIDU_POI_DATA_PARTIAL_OR_UNAVAILABLE")
    return report
