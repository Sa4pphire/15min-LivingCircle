"""百度地图 Web API 客户端。"""

import asyncio
import math
from pathlib import Path
from typing import Any

import httpx

from ..settings import settings
from ..cache import SharedBaiduCache
from .errors import (
    BaiduApiError,
    BaiduAuthError,
    BaiduQuotaError,
    BaiduTransientError,
)


class BaiduClient:
    def __init__(
        self,
        *,
        ak: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        cache_dir: Path | None = None,
        cache_enabled: bool | None = None,
        max_qps: float | None = None,
    ) -> None:
        self._ak = settings.baidu_server_ak if ak is None else ak
        self._cache_dir = cache_dir
        self._cache_enabled = transport is None if cache_enabled is None else cache_enabled
        self._cache = None
        self._semaphore = asyncio.Semaphore(max(1, min(4, settings.baidu_max_concurrency)))
        self._halt_error = None
        self._request_limit = None
        # Mock transports need no real-world pacing unless the test opts in.
        self._max_qps = (settings.baidu_max_qps if transport is None else 0) if max_qps is None else max_qps
        self._request_clock = 0.0
        self._request_lock = asyncio.Lock()
        self.cache_stats = {"cacheHits": 0, "cacheMisses": 0, "stalePages": 0, "apiRequests": 0}
        self.cache_fetched_times = []
        self._http = httpx.AsyncClient(
            base_url=settings.baidu_api_base_url,
            timeout=settings.baidu_timeout_seconds,
            transport=transport,
        )

    # 异步关闭 HTTP 客户端，释放网络连接；"async def"用来定义“异步函数”，可以在函数内部使用“await”关键字等待异步操作完成。
    async def aclose(self) -> None:
        await self._http.aclose()

    @property
    def has_ak(self) -> bool:
        return bool(self._ak)

    def enable_cache(self, cache: SharedBaiduCache) -> None:
        """Use the same response cache as regular Python POI callers."""
        self._cache = cache
        self._cache_enabled = True

    def limit_requests(self, maximum: int | None) -> None:
        """Limit additional HTTP requests; cache hits never consume this budget."""
        self._request_limit = (None if maximum is None else
                               self.cache_stats["apiRequests"] + max(0, maximum))

    async def _wait_for_request_slot(self):
        if self._max_qps <= 0:
            return
        if self._cache is not None:
            await self._cache.wait_for_request_slot(settings.baidu_api_base_url, self._max_qps)
        else:
            async with self._request_lock:
                loop = asyncio.get_running_loop()
                await asyncio.sleep(max(0, self._request_clock - loop.time()))
                self._request_clock = loop.time() + 1 / self._max_qps

    @staticmethod
    def _legacy_request(path: str, params: dict):
        """Read the previous demo cache without keeping a second active store."""
        if (path == "/place/v2/search" and not params.get("region") and
                params.get("radius_limit", "true") == "true"):
            lat, lng = [float(value) for value in params["location"].split(",")]
            coord_type = {1: "wgs84ll", 2: "gcj02ll", 3: "bd09ll"}[params["coord_type"]]
            legacy = {"endpoint": "place/v2/search", "query": params["query"],
                      "center": [lng, lat], "radius": params["radius"],
                      "inputCoordType": coord_type, "outputCoordType": "bd09ll",
                      "page": params["page_num"], "pageSize": params["page_size"],
                      "scope": 2, "radiusLimit": True}

            def transform(page):
                results = []
                for item in page["items"]:
                    detail = {"tag": item.get("tag", "")}
                    if "navigationPoint" in item:
                        detail["navi_location"] = dict(zip(("lng", "lat"), item["navigationPoint"]))
                    results.append({"uid": item["uid"], "name": item["name"],
                                    "address": item.get("address", ""),
                                    "location": {"lng": item["lng"], "lat": item["lat"]},
                                    "detail_info": detail})
                output = {"status": 0, "results": results,
                          "_cachePageRawCount": page["rawCount"]}
                if page.get("total") is not None:
                    output["total"] = page["total"]
                return output
            return legacy, transform
        if path == "/geoconv/v2/" and params.get("model") == 2:
            anchors = [[float(value) for value in point.split(",")]
                       for point in params["coords"].split(";")]
            return {"endpoint": "geoconv/v2", "model": 2, "anchors": anchors}, lambda points: {
                "status": 0, "result": [{"x": point[0], "y": point[1]} for point in points]}
        return None

    # 统一发送百度 API 请求，解析 JSON，并分类处理错误
    async def _get_json(
        self, path: str, params: dict[str, Any], *, refresh: bool = False,
        cache_only: bool = False,
    ) -> dict[str, Any] | None:
        if "ak" in params or "sn" in params:
            raise BaiduApiError("密钥只能通过客户端配置，不能写入缓存参数")
        if cache_only and refresh:
            raise BaiduApiError("缓存只读查询不能强制刷新")

        async def fetch():
            async with self._semaphore:
                # An AK is only needed for a real request, never for a warm read.
                if not self._ak:
                    raise BaiduAuthError("未配置百度服务端 AK")
                if self._halt_error is not None:
                    raise self._halt_error
                if (self._request_limit is not None and
                        self.cache_stats["apiRequests"] >= self._request_limit):
                    raise BaiduApiError("已达到本次 POI 请求预算，未继续调用 API")
                await self._wait_for_request_slot()
                # Another worker may have hit a quota/budget while we waited.
                if self._halt_error is not None:
                    raise self._halt_error
                if (self._request_limit is not None and
                        self.cache_stats["apiRequests"] >= self._request_limit):
                    raise BaiduApiError("已达到本次 POI 请求预算，未继续调用 API")
                self.cache_stats["apiRequests"] += 1
                try:
                    return await self._request_json(path, params)
                except (BaiduAuthError, BaiduQuotaError) as exc:
                    self._halt_error = exc
                    raise

        if not self._cache_enabled:
            return None if cache_only else await fetch()
        if self._cache is None:
            self._cache = SharedBaiduCache(
                self._cache_dir or settings.analysis_cache_dir,
                None if self._cache_dir else settings.poi_cache_path)
        ttl = settings.poi_cache_ttl_hours * 3600 if path == "/place/v2/search" else settings.cache_ttl_hours * 3600
        if path == "/geoconv/v2/":
            ttl = settings.poi_cache_stale_hours * 3600
        parameters = {"provider": settings.baidu_api_base_url, "path": path, "params": params}
        legacy = self._legacy_request(path, params)
        if legacy and not refresh:
            self._cache.migrate_legacy(parameters,
                                      {"provider": settings.baidu_api_base_url, "version": 1, **legacy[0]},
                                      legacy[1], ttl)
        if cache_only:
            cached = self._cache.peek(parameters, ttl_seconds=ttl)
            if cached is None:
                return None
            value, fetched = cached
            source = "hit"
        else:
            value, source, fetched = await self._cache.get_or_fetch(parameters, fetch,
                                                                   refresh=refresh, ttl_seconds=ttl)
        self.cache_stats[{"hit": "cacheHits", "miss": "cacheMisses", "stale": "stalePages"}[source]] += 1
        self.cache_fetched_times.append(fetched)
        return value

    async def _request_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:

        try:
            response = await self._http.get(
                path,
                params={**params, "ak": self._ak},
            )
        except httpx.RequestError:
            raise BaiduTransientError(
                "百度 API 请求超时或网络错误"
            ) from None

        if response.status_code >= 500:
            raise BaiduTransientError(
                f"百度 API HTTP {response.status_code}"
            )
        if response.status_code == 429:
            raise BaiduQuotaError("百度 API 请求频率超限")
        if response.status_code in {401, 403}:
            raise BaiduAuthError(
                f"百度 API HTTP {response.status_code}"
            )
        if response.status_code >= 400:
            raise BaiduApiError(
                f"百度 API HTTP {response.status_code}"
            )

        try:
            payload = response.json()
        except ValueError:
            raise BaiduApiError(
                "百度 API 返回的内容不是 JSON"
            ) from None

        if (
            not isinstance(payload, dict)
            or type(payload.get("status")) is not int
        ):
            raise BaiduApiError(
                "百度 API 响应缺少有效的 status"
            )

        status = payload["status"]

        if status in {4, 301, 302, 401, 402}:
            raise BaiduQuotaError(
                f"百度 API 配额错误：{status}"
            )

        if status in {
            3, 5, 101, 102, 200, 201, 202, 203,
            210, 211, 220, 230, 240,
        }:
            raise BaiduAuthError(
                f"百度 API 鉴权错误：{status}"
            )

        if status == 1:
            raise BaiduTransientError(
                "百度 API 服务临时异常"
            )

        if status != 0:
            raise BaiduApiError(
                f"百度 API 错误：{status}"
            )

        return payload

    # 将 GCJ-02 或 WGS84 坐标转换为 BD-09；BD-09 输入直接返回
    async def convert_coordinates(
        self,
        points: list[tuple[float, float]],
        source_coord_type: str,
        *, refresh: bool = False,
    ) -> list[tuple[float, float]]:
        if source_coord_type == "bd09ll" or not points:
            return list(points)

        model = {
            "gcj02": 1,
            "gcj02ll": 1,
            "wgs84": 2,
            "wgs84ll": 2,
        }.get(source_coord_type)
        if model is None:
            raise BaiduApiError(
                f"不支持的坐标类型：{source_coord_type}"
            )

        payload = await self._get_json(
            "/geoconv/v2/",
            {
                "coords": ";".join(
                    f"{lng},{lat}" for lng, lat in points
                ),
                "model": model,
                "output": "json",
            },
            refresh=refresh,
        )

        result = payload.get("result")
        if not isinstance(result, list) or len(result) != len(points):
            raise BaiduApiError("坐标转换结果数量与输入不一致")

        try:
            return [
                (float(item["x"]), float(item["y"]))
                for item in result
            ]
        except (KeyError, TypeError, ValueError):
            raise BaiduApiError("坐标转换结果缺少有效坐标") from None

    # 批量查询中心点到多个目标点的步行距离和耗时
    async def route_matrix(
        self,
        origin: tuple[float, float],
        destinations: list[tuple[float, float]],
        *,
        coord_type: str = "bd09ll",
    ) -> list[dict[str, float]]:
        if not destinations:
            return []

        origin_text = f"{origin[1]},{origin[0]}"
        destinations_text = "|".join(
            f"{lat},{lng}"
            for lng, lat in destinations
        )

        payload = await self._get_json(
            "/routematrix/v2/walking",
            {
                "origins": origin_text,
                "destinations": destinations_text,
                "coord_type": coord_type,
                "output": "json",
            },
        )

        result = payload.get("result")
        if not isinstance(result, list):
            raise BaiduApiError(
                "百度 RouteMatrix 响应缺少 result"
            )
        if len(result) != len(destinations):
            raise BaiduApiError(
                "百度 RouteMatrix 结果数量与目标点不一致"
            )

        normalized: list[dict[str, float]] = []

        for item in result:
            try:
                distance = float(item["distance"]["value"])
                duration = float(item["duration"]["value"])
            except (KeyError, TypeError, ValueError):
                raise BaiduApiError(
                    "百度 RouteMatrix 结果缺少距离或耗时"
                ) from None

            if distance < 0 or duration < 0:
                raise BaiduApiError(
                    "百度 RouteMatrix 返回了负数距离或耗时"
                )

            normalized.append(
                {
                    "distanceMeters": distance,
                    "durationSeconds": duration,
                }
            )

        return normalized

    # 获取真实步行路线的 BD-09 折线；每个路段独立保留，避免跨段连线。
    async def walking_route(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        *,
        destination_uid: str | None = None,
    ) -> dict[str, Any]:
        coordinates = (*origin, *destination)
        if (not all(math.isfinite(value) for value in coordinates)
                or not all(-180 <= longitude <= 180 and -90 <= latitude <= 90
                           for longitude, latitude in (origin, destination))):
            raise BaiduApiError("步行路线起终点坐标无效")

        params: dict[str, Any] = {
            "origin": f"{origin[1]:.6f},{origin[0]:.6f}",
            "destination": f"{destination[1]:.6f},{destination[0]:.6f}",
            "coord_type": "bd09ll",
            "ret_coordtype": "bd09ll",
            "output": "json",
        }
        if destination_uid:
            params["destination_uid"] = destination_uid
        payload = await self._get_json("/direction/v2/walking", params)
        result = payload.get("result")
        routes = result.get("routes") if isinstance(result, dict) else None
        if not isinstance(routes, list) or not routes or not isinstance(routes[0], dict):
            raise BaiduApiError("百度步行路线响应缺少 routes")

        route = routes[0]
        try:
            distance_meters = float(route["distance"])
            duration_seconds = float(route["duration"])
        except (KeyError, TypeError, ValueError):
            raise BaiduApiError("百度步行路线缺少距离或耗时") from None
        if (not math.isfinite(distance_meters) or not math.isfinite(duration_seconds)
                or distance_meters < 0 or duration_seconds < 0):
            raise BaiduApiError("百度步行路线距离或耗时无效")

        steps = route.get("steps")
        if not isinstance(steps, list) or not steps:
            raise BaiduApiError("百度步行路线缺少路段")
        segments: list[list[list[float]]] = []
        for step in steps:
            if not isinstance(step, dict) or not isinstance(step.get("path"), str):
                raise BaiduApiError("百度步行路线缺少路段折线")
            points: list[list[float]] = []
            for coordinate in step["path"].split(";"):
                parts = coordinate.split(",")
                if len(parts) != 2:
                    raise BaiduApiError("百度步行路线折线坐标无效")
                try:
                    longitude, latitude = (float(part) for part in parts)
                except ValueError:
                    raise BaiduApiError("百度步行路线折线坐标无效") from None
                if (not math.isfinite(longitude) or not math.isfinite(latitude)
                        or not -180 <= longitude <= 180 or not -90 <= latitude <= 90):
                    raise BaiduApiError("百度步行路线折线坐标无效")
                point = [longitude, latitude]
                if not points or point != points[-1]:
                    points.append(point)
            if len(points) < 2:
                raise BaiduApiError("百度步行路线折线点数不足")
            segments.append(points)

        return {
            "distanceMeters": distance_meters,
            "durationSeconds": duration_seconds,
            "coordType": "bd09ll",
            "segments": segments,
        }

    # 在指定中心点和半径内查询百度 POI，并统一返回基础字段
    async def search_pois(
        self,
        query: str,
        center: tuple[float, float],
        radius_meters: int = 1000,
        *,
        region: str | None = None,
        page_num: int = 0,
        page_size: int = 20,
        coord_type: str = "bd09ll",
        refresh: bool = False,
    ) -> list[dict[str, Any]]:
        page = await self.search_poi_page(query, center, radius_meters,
                                        region=region, page_num=page_num,
                                        page_size=page_size, coord_type=coord_type, refresh=refresh)
        # Keep the original public helper's return shape for sampling callers.
        return [{key: item[key] for key in ("uid", "name", "address", "lng", "lat")}
                for item in page["items"]]

    async def search_poi_page(
        self, query: str, center: tuple[float, float], radius_meters: int = 1000,
        *, region: str | None = None, page_num: int = 0, page_size: int = 20,
        coord_type: str = "bd09ll", refresh: bool = False, cache_only: bool = False,
        radius_limit: bool = True,
    ) -> dict[str, Any] | None:
        """Paginated BD-09 POIs; cache_only returns None on a fresh-cache miss."""
        if radius_meters <= 0:
            raise BaiduApiError("POI 搜索半径必须大于 0")
        if not 1 <= page_size <= 20 or not 0 <= page_num <= 7:
            raise BaiduApiError("POI 分页参数无效")
        coordinate_code = {"wgs84ll": 1, "gcj02ll": 2, "bd09ll": 3}.get(coord_type)
        if coordinate_code is None:
            raise BaiduApiError("POI 输入坐标类型无效")

        # 百度地点检索要求 location 使用“纬度,经度”
        location = f"{center[1]},{center[0]}"

        params: dict[str, Any] = {
            "query": query,
            "location": location,
            "radius": radius_meters,
            "page_num": page_num,
            "page_size": page_size,
            "coord_type": coordinate_code,
            "scope": 2,
            "radius_limit": "true" if radius_limit else "false",
            "output": "json",
        }

        if region:
            params["region"] = region

        # 统一通过 _get_json 发送请求，自动附加 AK 和处理 status
        payload = await self._get_json(
            "/place/v2/search",
            params,
            refresh=refresh,
            cache_only=cache_only,
        )
        if payload is None:
            return None

        raw_results = payload.get("results", [])
        if not isinstance(raw_results, list):
            raise BaiduApiError(
                "百度地点检索响应缺少有效的 results"
            )

        normalized: list[dict[str, Any]] = []

        for item in raw_results:
            if not isinstance(item, dict):
                continue

            location_data = item.get("location")
            if not isinstance(location_data, dict):
                continue

            try:
                lng = float(location_data["lng"])
                lat = float(location_data["lat"])
            except (KeyError, TypeError, ValueError):
                # 单条 POI 坐标异常时跳过，不影响其他结果
                continue
            if not math.isfinite(lng) or not math.isfinite(lat) or not (-180 <= lng <= 180 and -90 < lat < 90):
                continue

            uid = item.get("uid")
            name = item.get("name")
            address = item.get("address")

            if not uid or not name:
                # 后续业务层还会记录缺少 UID 的 warning
                continue

            normalized.append(
                {
                    "uid": str(uid),
                    "name": str(name),
                    "address": str(address or ""),
                    "lng": lng,
                    "lat": lat,
                    "coordType": "bd09ll",
                    "tag": str(item["detail_info"].get("tag", "")) if isinstance(item.get("detail_info"), dict) else "",
                }
            )
            detail = item.get("detail_info")
            navi = detail.get("navi_location") if isinstance(detail, dict) else None
            if isinstance(navi, dict):
                try:
                    xy = [float(navi["lng"]), float(navi["lat"])]
                    if all(math.isfinite(v) for v in xy) and -180 <= xy[0] <= 180 and -90 < xy[1] < 90:
                        normalized[-1]["navigationPoint"] = xy
                except (KeyError, TypeError, ValueError):
                    pass
        total = payload.get("total")
        raw_count = payload.get("_cachePageRawCount", len(raw_results))
        if type(raw_count) is not int or raw_count < len(raw_results):
            raw_count = len(raw_results)
        return {"items": normalized, "rawCount": raw_count,
                "discardedCount": raw_count - len(normalized),
                "total": total if type(total) is int and total >= 0 else None,
                "coordType": "bd09ll"}
