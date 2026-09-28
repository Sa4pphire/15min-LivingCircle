"""Sharing regressions: every request uses MockTransport, never a live AK."""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import httpx
import pytest

from app import pois
from app.baidu.client import BaiduClient
from app.baidu.errors import BaiduQuotaError
from app.cache import SharedBaiduCache, make_cache_key, save_cached_json
from app.poi_cache import PoiCache
from app.schemas import CenterPoint


def response():
    return {"status": 0, "total": 1, "results": [{
        "uid": "school-1", "name": "测试学校", "address": "测试地址",
        "location": {"lng": 121.5, "lat": 31.3},
        "detail_info": {"navi_location": {"lng": 121.5001, "lat": 31.3001}},
    }]}


def test_collaborator_helper_and_cpp_service_share_a_single_response(monkeypatch, tmp_path):
    monkeypatch.setattr(pois, "settings", replace(
        pois.settings, analysis_cache_dir=tmp_path,
        poi_cache_path=tmp_path / "baidu-pois.sqlite3"))
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=response())

    async def run():
        python_client = BaiduClient(ak="fake-secret", transport=httpx.MockTransport(handler),
                                    cache_dir=tmp_path, cache_enabled=True)
        cpp_client = BaiduClient(ak="fake-secret", transport=httpx.MockTransport(handler))
        center = CenterPoint(lng=121.5, lat=31.3, coordType="wgs84ll")
        grid, radius = pois._grid_query(center, 1000)
        try:
            basic = await python_client.search_pois("学校$幼儿园", grid, radius,
                                                   coord_type="wgs84ll")
            service = pois.PoiService(client=cpp_client)
            detailed, info = await service.search(center, 1000, ["education"])
            assert basic[0]["uid"] == detailed[0]["uid"]
            assert detailed[0]["navigationPoint"] == [121.5001, 31.3001]
            assert info["apiRequests"] == 0 and info["cacheHits"] == 1
        finally:
            await python_client.aclose()
            await cpp_client.aclose()

    asyncio.run(run())
    assert len(requests) == 1
    assert len(list(tmp_path.glob("*.json"))) == 1
    assert all(b"fake-secret" not in path.read_bytes() for path in tmp_path.iterdir() if path.is_file())
    # Shared responses are JSON, not a duplicate normalized SQLite store.
    import sqlite3
    with sqlite3.connect(tmp_path / "baidu-pois.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 0


def test_two_clients_coalesce_concurrent_requests_and_forced_refresh(tmp_path):
    count = 0

    async def handler(request):
        nonlocal count
        count += 1
        await asyncio.sleep(0.02)
        return httpx.Response(200, json=response())

    async def run():
        clients = [BaiduClient(ak="fake", transport=httpx.MockTransport(handler),
                               cache_dir=tmp_path, cache_enabled=True) for _ in range(2)]
        try:
            await asyncio.gather(*(client.search_pois("学校", (121.5, 31.3)) for client in clients))
            assert count == 1
            await asyncio.gather(*(client.search_poi_page("学校", (121.5, 31.3), refresh=True)
                                   for client in clients))
            assert count == 2
        finally:
            for client in clients:
                await client.aclose()

    asyncio.run(run())


def test_existing_collaborator_json_envelope_is_reused_without_fetch(tmp_path):
    params = {"query": "学校", "location": "31.3,121.5", "radius": 1000,
              "page_num": 0, "page_size": 20, "coord_type": 3, "scope": 2,
              "radius_limit": "true", "output": "json"}
    key = make_cache_key("baidu:/place/v2/search", params)
    save_cached_json(key, response(), cache_dir=tmp_path)

    def forbidden(request):
        pytest.fail("An existing shared cache must not generate an API request")

    async def run():
        client = BaiduClient(ak="fake", transport=httpx.MockTransport(forbidden),
                             cache_dir=tmp_path, cache_enabled=True)
        try:
            assert (await client.search_pois("学校", (121.5, 31.3)))[0]["uid"] == "school-1"
            assert client.cache_stats["apiRequests"] == 0
        finally:
            await client.aclose()

    asyncio.run(run())


def test_previous_normalized_demo_cache_migrates_without_fetch_or_freshness_reset(tmp_path):
    cache = SharedBaiduCache(tmp_path)
    path = "/place/v2/search"
    params = {"query": "学校", "location": "31.3,121.5", "radius": 1000,
              "page_num": 0, "page_size": 20, "coord_type": 3, "scope": 2,
              "radius_limit": "true", "output": "json"}
    legacy, _ = BaiduClient._legacy_request(path, params)
    fetched = (datetime.now(timezone.utc) - timedelta(hours=1)).timestamp()
    old = {"provider": "https://api.map.baidu.com", "version": 1, **legacy}
    PoiCache._write(cache, PoiCache.key(old), {"items": [{
        "uid": "legacy", "name": "学校", "address": "", "lng": 121.5,
        "lat": 31.3, "navigationPoint": [121.5001, 31.3001]}],
        "rawCount": 2, "discardedCount": 1, "total": 2}, fetched)

    def forbidden(request):
        pytest.fail("Legacy normalized data should migrate locally, without quota usage")

    async def run():
        client = BaiduClient(ak="fake", transport=httpx.MockTransport(forbidden))
        client.enable_cache(cache)
        try:
            page = await client.search_poi_page("学校", (121.5, 31.3))
            assert page["items"][0]["uid"] == "legacy"
            assert page["items"][0]["navigationPoint"] == [121.5001, 31.3001]
            assert page["rawCount"] == 2 and page["discardedCount"] == 1
            assert client.cache_fetched_times == [fetched]
            assert client.cache_stats["apiRequests"] == 0
        finally:
            await client.aclose()

    asyncio.run(run())


def test_different_keywords_pages_radii_and_datums_do_not_share_wrong_results(tmp_path):
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(200, json={"status": 0, "results": []})

    async def run():
        client = BaiduClient(ak="fake", transport=httpx.MockTransport(handler),
                             cache_dir=tmp_path, cache_enabled=True)
        try:
            await client.search_pois("学校", (121.5, 31.3))
            await client.search_poi_page("学校", (121.5, 31.3))
            await client.search_pois("医院", (121.5, 31.3))
            await client.search_pois("学校", (121.5, 31.3), page_num=1)
            await client.search_pois("学校", (121.5, 31.3), radius_meters=2000)
            await client.search_pois("学校", (121.5, 31.3), coord_type="wgs84ll")
        finally:
            await client.aclose()

    asyncio.run(run())
    assert count == 5


def test_json_stale_fallback_and_provider_cooldown_never_cache_failure_as_empty(tmp_path):
    cache = SharedBaiduCache(tmp_path, ttl_seconds=0.01, stale_seconds=100)
    params = {"provider": "https://api.map.baidu.com", "path": "/place/v2/search",
              "params": {"query": "学校"}}
    count = 0

    async def run():
        nonlocal count

        async def success():
            return response()

        async def failure():
            nonlocal count
            count += 1
            raise BaiduQuotaError("测试配额不足")

        await cache.get_or_fetch(params, success)
        await asyncio.sleep(0.02)
        value, source, _ = await cache.get_or_fetch(params, failure)
        assert source == "stale" and len(value["results"]) == 1
        with pytest.raises(BaiduQuotaError, match="冷却"):
            await cache.get_or_fetch({**params, "params": {"query": "医院"}}, failure)

    asyncio.run(run())
    assert count == 1
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1 and json.loads(files[0].read_text(encoding="utf-8"))["payload"]["status"] == 0
