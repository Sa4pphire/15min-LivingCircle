import asyncio

import httpx
import pytest

from app.baidu.client import BaiduClient
from app.baidu.errors import BaiduApiError


def test_walking_route_preserves_bd09_steps_and_uid() -> None:
    observed = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed.update(dict(request.url.params))
        assert request.url.path == "/direction/v2/walking"
        return httpx.Response(200, json={
            "status": 0,
            "result": {"routes": [{
                "distance": 420,
                "duration": 360,
                "steps": [
                    {"path": "121.513,31.337;121.514,31.338"},
                    {"path": "121.514,31.338;121.515,31.339"},
                ],
            }]},
        })

    async def run():
        client = BaiduClient(ak="fake-ak", transport=httpx.MockTransport(handler))
        try:
            return await client.walking_route(
                (121.513, 31.337), (121.515, 31.339),
                destination_uid="poi-1",
            )
        finally:
            await client.aclose()

    route = asyncio.run(run())
    assert observed["origin"] == "31.337000,121.513000"
    assert observed["destination"] == "31.339000,121.515000"
    assert observed["coord_type"] == "bd09ll"
    assert observed["ret_coordtype"] == "bd09ll"
    assert observed["destination_uid"] == "poi-1"
    assert route == {
        "distanceMeters": 420.0,
        "durationSeconds": 360.0,
        "coordType": "bd09ll",
        "segments": [
            [[121.513, 31.337], [121.514, 31.338]],
            [[121.514, 31.338], [121.515, 31.339]],
        ],
    }


def test_walking_route_rejects_missing_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "status": 0,
            "result": {"routes": [{
                "distance": 420,
                "duration": 360,
                "steps": [{"distance": 420}],
            }]},
        })

    async def run():
        client = BaiduClient(ak="fake-ak", transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(BaiduApiError, match="路段折线"):
                await client.walking_route((121.513, 31.337), (121.515, 31.339))
        finally:
            await client.aclose()

    asyncio.run(run())
