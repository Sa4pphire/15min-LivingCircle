"""百度地图 Web API 客户端。"""

from typing import Any

import httpx

from ..settings import settings
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
    ) -> None:
        self._ak = settings.baidu_server_ak if ak is None else ak
        self._http = httpx.AsyncClient(
            base_url=settings.baidu_api_base_url,
            timeout=settings.baidu_timeout_seconds,
            transport=transport,
        )

    # 异步关闭 HTTP 客户端，释放网络连接
    async def aclose(self) -> None:
        await self._http.aclose()

    # 统一发送百度 API 请求，解析 JSON，并分类处理错误
    async def _get_json(
        self, path: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        if not self._ak:
            raise BaiduAuthError("未配置百度服务端 AK")

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
    ) -> list[dict[str, Any]]:
        if radius_meters <= 0:
            raise BaiduApiError("POI 搜索半径必须大于 0")

        # 百度地点检索要求 location 使用“纬度,经度”
        location = f"{center[1]},{center[0]}"

        params: dict[str, Any] = {
            "query": query,
            "location": location,
            "radius": radius_meters,
            "page_num": page_num,
            "page_size": page_size,
            "coord_type": coord_type,
            "output": "json",
        }

        if region:
            params["region"] = region

        # 统一通过 _get_json 发送请求，自动附加 AK 和处理 status
        payload = await self._get_json(
            "/place/v2/search",
            params,
        )

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
                }
            )

        return normalized