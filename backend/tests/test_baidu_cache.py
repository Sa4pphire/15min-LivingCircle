import asyncio
from pathlib import Path

import httpx

from app.baidu.client import BaiduClient


# 验证相同 POI 请求第二次直接读取缓存
def test_baidu_client_uses_cache_on_second_request(
    tmp_path: Path,
) -> None:
    requests_seen = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests_seen
        requests_seen += 1

        return httpx.Response(
            200,
            json={
                "status": 0,
                "results": [
                    {
                        "uid": "demo-pharmacy-1",
                        "name": "测试药店",
                        "address": "测试地址",
                        "location": {
                            "lng": 121.514,
                            "lat": 31.338,
                        },
                    }
                ],
            },
        )

    async def run() -> list[dict[str, object]]:
        client = BaiduClient(
            ak="fake-ak",
            transport=httpx.MockTransport(handler),
            cache_dir=tmp_path,
            cache_enabled=True,
        )

        try:
            first = await client.search_pois(
                query="药店",
                center=(121.513, 31.337),
            )
            second = await client.search_pois(
                query="药店",
                center=(121.513, 31.337),
            )
            return first + second
        finally:
            await client.aclose()

    result = asyncio.run(run())

    assert len(result) == 2
    assert result[0]["uid"] == "demo-pharmacy-1"
    assert result[1]["uid"] == "demo-pharmacy-1"
    assert requests_seen == 1