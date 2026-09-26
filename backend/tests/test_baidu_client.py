import asyncio

import httpx

from app.baidu.client import BaiduClient

import pytest

from app.baidu.errors import (
    BaiduApiError,
    BaiduAuthError,
    BaiduQuotaError,
)

def test_get_json_uses_mock_transport() -> None:
    observed: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "status": 0,
                "result": {"ok": True},
            },
        )

    # 在异步上下文中执行客户端请求并确保关闭连接
    async def run() -> dict:
        client = BaiduClient(
            ak="fake-ak",
            transport=httpx.MockTransport(handler),
        )
        try:
            return await client._get_json(
                "/test",
                {"foo": "bar"},
            )
        finally:
            await client.aclose()

    payload = asyncio.run(run())

    assert payload["status"] == 0
    assert payload["result"]["ok"] is True
    assert "ak=fake-ak" in observed["url"]
    assert "foo=bar" in observed["url"]

def test_get_json_maps_auth_error_without_exposing_key() -> None:
    # 在异步上下文中执行鉴权错误测试并确保关闭连接
    async def run() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "status": 210,
                    "message": "APP IP校验失败",
                },
            )

        client = BaiduClient(
            ak="secret-test-ak",
            transport=httpx.MockTransport(handler),
        )
        try:
            with pytest.raises(BaiduAuthError) as exc_info:
                await client._get_json("/test", {})
        finally:
            await client.aclose()

        assert "secret-test-ak" not in str(exc_info.value)
        assert "210" in str(exc_info.value)

    asyncio.run(run())
def test_get_json_rejects_missing_ak_without_request() -> None:
    requests_seen = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests_seen
        requests_seen += 1
        return httpx.Response(200, json={"status": 0})

    # 在异步上下文中验证缺少 AK 时不会发送请求
    async def run() -> None:
        client = BaiduClient(
            ak="",
            transport=httpx.MockTransport(handler),
        )
        try:
            with pytest.raises(BaiduAuthError, match="未配置"):
                await client._get_json("/test", {})
        finally:
            await client.aclose()

    asyncio.run(run())
    assert requests_seen == 0
    async def convert_coordinates(
        self,
        points: list[tuple[float, float]],
        source_coord_type: str,
    ) -> list[tuple[float, float]]:
        if source_coord_type == "bd09ll":
            return list(points)

        model_by_type = {
            "gcj02": 1,
            "gcj02ll": 1,
            "wgs84": 2,
            "wgs84ll": 2,
        }
        model = model_by_type.get(source_coord_type)
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
        if not isinstance(result, list):
            raise BaiduApiError(
                "百度坐标转换响应缺少 result"
            )
        if len(result) != len(points):
            raise BaiduApiError(
                "百度坐标转换结果数量与输入不一致"
            )

        converted: list[tuple[float, float]] = []
        for item in result:
            if not isinstance(item, dict):
                raise BaiduApiError(
                    "百度坐标转换结果格式错误"
                )
            try:
                converted.append(
                    (float(item["x"]), float(item["y"]))
                )
            except (KeyError, TypeError, ValueError):
                raise BaiduApiError(
                    "百度坐标转换结果缺少有效坐标"
                ) from None

        return converted

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

def test_convert_bd09ll_without_request() -> None:
    requests_seen = 0
    points = [(121.513, 31.337)]

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests_seen
        requests_seen += 1
        return httpx.Response(200, json={"status": 0})

    # 在异步上下文中执行 BD-09 直接返回测试
    async def run() -> list[tuple[float, float]]:
        client = BaiduClient(
            ak="fake-ak",
            transport=httpx.MockTransport(handler),
        )
        try:
            return await client.convert_coordinates(
                points,
                "bd09ll",
            )
        finally:
            await client.aclose()

    converted = asyncio.run(run())

    assert converted == points
    assert requests_seen == 0

@pytest.mark.parametrize(
    ("source_coord_type", "expected_model"),
    [
        ("gcj02", "1"),
        ("wgs84", "2"),
    ],
)
def test_convert_coordinates_uses_correct_model(
    source_coord_type: str,
    expected_model: str,
) -> None:
    observed: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["path"] = request.url.path
        observed["coords"] = request.url.params["coords"]
        observed["model"] = request.url.params["model"]
        return httpx.Response(
            200,
            json={
                "status": 0,
                "result": [
                    {"x": 121.51, "y": 31.33},
                    {"x": 121.52, "y": 31.34},
                ],
            },
        )

    # 在异步上下文中执行坐标转换 mock 请求
    async def run() -> list[tuple[float, float]]:
        client = BaiduClient(
            ak="fake-ak",
            transport=httpx.MockTransport(handler),
        )
        try:
            return await client.convert_coordinates(
                [(121.50, 31.32), (121.51, 31.33)],
                source_coord_type,
            )
        finally:
            await client.aclose()

    converted = asyncio.run(run())

    assert observed["path"] == "/geoconv/v2/"
    assert observed["coords"] == "121.5,31.32;121.51,31.33"
    assert observed["model"] == expected_model
    assert converted == [(121.51, 31.33), (121.52, 31.34)]

def test_route_matrix_normalizes_results() -> None:
    observed: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["origins"] = request.url.params["origins"]
        observed["destinations"] = request.url.params["destinations"]
        observed["coord_type"] = request.url.params["coord_type"]

        return httpx.Response(
            200,
            json={
                "status": 0,
                "result": [
                    {
                        "distance": {"value": 1168},
                        "duration": {"value": 996},
                    },
                    {
                        "distance": {"value": 744},
                        "duration": {"value": 636},
                    },
                ],
            },
        )

    # 在异步上下文中执行 RouteMatrix mock 请求
    async def run() -> list[dict[str, float]]:
        client = BaiduClient(
            ak="fake-ak",
            transport=httpx.MockTransport(handler),
        )
        try:
            return await client.route_matrix(
                origin=(121.513, 31.337),
                destinations=[
                    (121.514553, 31.329866),
                    (121.509, 31.334),
                ],
            )
        finally:
            await client.aclose()

    result = asyncio.run(run())

    assert observed["origins"] == "31.337,121.513"
    assert (
        observed["destinations"]
        == "31.329866,121.514553|31.334,121.509"
    )
    assert observed["coord_type"] == "bd09ll"
    assert result == [
        {
            "distanceMeters": 1168.0,
            "durationSeconds": 996.0,
        },
        {
            "distanceMeters": 744.0,
            "durationSeconds": 636.0,
        },
    ]

def test_search_pois_normalizes_results() -> None:
    observed: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["query"] = request.url.params["query"]
        observed["location"] = request.url.params["location"]
        observed["radius"] = request.url.params["radius"]
        observed["region"] = request.url.params["region"]

        return httpx.Response(
            200,
            json={
                "status": 0,
                "message": "ok",
                "results": [
                    {
                        "uid": "poi-1",
                        "name": "测试药店",
                        "address": "测试地址",
                        "location": {
                            "lng": 121.514553,
                            "lat": 31.329866,
                        },
                    },
                    {
                        "uid": "poi-invalid",
                        "name": "缺少坐标的 POI",
                    },
                ],
            },
        )

    async def run() -> list[dict[str, object]]:
        # 在异步上下文中执行 POI 查询 mock 测试
        client = BaiduClient(
            ak="fake-ak",
            transport=httpx.MockTransport(handler),
        )
        try:
            return await client.search_pois(
                query="药店",
                center=(121.513, 31.337),
                radius_meters=1000,
                region="上海",
            )
        finally:
            await client.aclose()

    result = asyncio.run(run())

    assert observed["query"] == "药店"
    assert observed["location"] == "31.337,121.513"
    assert observed["radius"] == "1000"
    assert observed["region"] == "上海"
    assert result == [
        {
            "uid": "poi-1",
            "name": "测试药店",
            "address": "测试地址",
            "lng": 121.514553,
            "lat": 31.329866,
        }
    ]

def test_get_json_maps_quota_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # 模拟百度返回“每日配额超限”
        return httpx.Response(
            200,
            json={
                "status": 302,
                "message": "当天配额超限，限制访问",
            },
        )

    async def run() -> None:
        # 在异步上下文中执行配额错误测试
        client = BaiduClient(
            ak="fake-ak",
            transport=httpx.MockTransport(handler),
        )
        try:
            with pytest.raises(BaiduQuotaError, match="302"):
                await client._get_json("/test", {})
        finally:
            await client.aclose()

    asyncio.run(run())

def test_get_json_rejects_non_json_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # 模拟百度返回无法解析的文本
        return httpx.Response(
            200,
            content=b"not-json",
        )

    async def run() -> None:
        # 在异步上下文中执行非 JSON 响应测试
        client = BaiduClient(
            ak="fake-ak",
            transport=httpx.MockTransport(handler),
        )
        try:
            with pytest.raises(
                BaiduApiError,
                match="不是 JSON",
            ):
                await client._get_json("/test", {})
        finally:
            await client.aclose()

    asyncio.run(run())

def test_route_matrix_rejects_missing_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # 模拟百度返回缺少 duration 的路线结果
        return httpx.Response(
            200,
            json={
                "status": 0,
                "result": [
                    {
                        "distance": {"value": 100},
                    }
                ],
            },
        )

    async def run() -> None:
        # 在异步上下文中执行字段缺失测试
        client = BaiduClient(
            ak="fake-ak",
            transport=httpx.MockTransport(handler),
        )
        try:
            with pytest.raises(
                BaiduApiError,
                match="缺少距离或耗时",
            ):
                await client.route_matrix(
                    origin=(121.513, 31.337),
                    destinations=[(121.514, 31.338)],
                )
        finally:
            await client.aclose()

    asyncio.run(run())
