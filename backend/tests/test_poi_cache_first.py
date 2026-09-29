"""Cache-first tiled pagination regressions; no real AK or Baidu requests."""

import asyncio
from dataclasses import replace

import httpx
import pytest

from app import pois
from app.baidu.client import BaiduClient
from app.baidu.errors import BaiduAuthError
from app.poi_search import plan_tiles
from app.schemas import CenterPoint


CENTER = CenterPoint(lng=121.5, lat=31.3, coordType="wgs84ll")
BOUNDS = [CENTER.lng, CENTER.lat, CENTER.lng, CENTER.lat]


def configure(monkeypatch, tmp_path, *, budget=2, requests=96):
    monkeypatch.setattr(pois, "settings", replace(
        pois.settings, analysis_cache_dir=tmp_path,
        poi_cache_path=tmp_path / "baidu-pois.sqlite3",
        poi_max_pages=8, poi_budget_seconds=budget, poi_max_requests=requests))


def response(start, count, *, total=None):
    payload = {"status": 0, "results": [
        {"uid": str(number), "name": f"测试设施 {number}",
         "location": {"lng": 121.5, "lat": 31.3}}
        for number in range(start, start + count)]}
    if total is not None:
        payload["total"] = total
    return payload


def client(tmp_path, handler, *, ak="fake-test-key"):
    return BaiduClient(ak=ak, transport=httpx.MockTransport(handler),
                       cache_dir=tmp_path, cache_enabled=True)


async def seed_page(tmp_path, key, number, payload):
    tile = plan_tiles(CENTER, 1000, pois.settings, BOUNDS)[0][0]
    seed = client(tmp_path, lambda request: httpx.Response(200, json=payload))
    try:
        for keyword in pois.CATEGORIES[key]["keywords"]:
            await seed.search_poi_page(keyword, tuple(tile["center"]), tile["radiusMeters"],
                                       coord_type=CENTER.coordType, page_num=number, radius_limit=False)
    finally:
        await seed.aclose()


def test_demo_search_never_requests_removed_market_or_pharmacy_keywords(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    requested = []
    def handler(request):
        requested.append(request.url.params["query"])
        return httpx.Response(200, json={"status": 0, "total": 0, "results": []})
    async def run():
        reader = client(tmp_path, handler)
        try:
            records, info = await pois.PoiService(client=reader).search(CENTER, 1000, pois.CATEGORIES, bounds=BOUNDS)
            assert not records and info["status"] == "ready"
            assert info["plannedQueries"] == 11
            assert len(info["categories"]) == 5
        finally:
            await reader.aclose()
    asyncio.run(run())
    assert set(requested) == {"学校", "幼儿园", "医院", "社区卫生服务中心", "超市", "便利店",
                              "街道办事处", "社区事务受理服务中心", "派出所", "邮局", "美食"}


def test_dining_paginates_deduplicates_and_reuses_shared_cache(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    requested = []
    def handler(request):
        assert request.url.params["query"] == "美食"
        page = int(request.url.params["page_num"])
        requested.append(page)
        return httpx.Response(200, json=response(19 if page == 1 else 0,
                                               2 if page == 1 else 20, total=22))
    async def run():
        for warm in (False, True):
            reader = client(tmp_path, handler, ak="" if warm else "fake-test-key")
            try:
                records, info = await pois.PoiService(client=reader).search(
                    CENTER, 1000, ["dining"], bounds=BOUNDS)
                assert len(records) == 21  # Repeated UID across pages is displayed only once.
                assert all(record["categories"] == ["dining"] for record in records)
                assert all(record["matchedKeywords"] == ["美食"] for record in records)
                assert info["status"] == "ready"
                assert info["apiRequests"] == (0 if warm else 2)
                assert info["cacheHits"] == (2 if warm else 0)
            finally:
                await reader.aclose()
    asyncio.run(run())
    assert requested == [0, 1]


def test_cache_only_miss_never_requires_ak_or_calls_http(tmp_path):
    async def run():
        reader = client(tmp_path, lambda request: pytest.fail("Cache-only miss used HTTP"), ak="")
        try:
            assert await reader.search_poi_page("学校", (121.5, 31.3), cache_only=True) is None
            assert all(value == 0 for value in reader.cache_stats.values())
            with pytest.raises(BaiduAuthError):
                await reader.search_poi_page("学校", (121.5, 31.3))
            assert reader.cache_stats["apiRequests"] == 0
        finally:
            await reader.aclose()
    asyncio.run(run())


def test_warm_category_pages_are_available_without_ak(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    async def run():
        for key in pois.CATEGORIES:
            await seed_page(tmp_path, key, 0, response(0, 2, total=2))
        reader = client(tmp_path, lambda request: pytest.fail("Warm search used HTTP"), ak="")
        try:
            records, info = await pois.PoiService(client=reader).search(CENTER, 1000, pois.CATEGORIES, bounds=BOUNDS)
            assert len(records) == 2
            assert all(len(record["categories"]) == len(pois.CATEGORIES) for record in records)
            assert info["status"] == "ready" and info["cacheFirst"]
            assert info["apiRequests"] == 0 and info["cacheHits"] == info["plannedQueries"]
            assert info["completedQueries"] == info["plannedQueries"]
        finally:
            await reader.aclose()
    asyncio.run(run())


def test_cached_coordinate_alignment_is_also_available_without_ak(tmp_path):
    async def run():
        writer = client(tmp_path, lambda request: httpx.Response(200, json={
            "status": 0, "result": [{"x": 121.51, "y": 31.31}]}))
        reader = client(tmp_path, lambda request: pytest.fail("Cached alignment used HTTP"), ak="")
        try:
            await writer.convert_coordinates([(121.5, 31.3)], "wgs84ll")
            assert await reader.convert_coordinates([(121.5, 31.3)], "wgs84ll") == [(121.51, 31.31)]
            assert reader.cache_stats["apiRequests"] == 0
        finally:
            await writer.aclose()
            await reader.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("timeout", [False, True])
def test_cached_later_page_and_other_category_survive_missing_first_page(monkeypatch, tmp_path, timeout):
    configure(monkeypatch, tmp_path, budget=0.05 if timeout else 2)
    requested = []
    async def handler(request):
        requested.append(dict(request.url.params))
        if timeout:
            await asyncio.sleep(10)
        return httpx.Response(200, json={"status": 1})
    async def run():
        await seed_page(tmp_path, "shopping", 1, response(20, 5, total=25))
        await seed_page(tmp_path, "education", 0, response(100, 1, total=1))
        reader = client(tmp_path, handler)
        try:
            records, info = await pois.PoiService(client=reader).search(
                CENTER, 1000, ["shopping", "education"], bounds=BOUNDS)
            assert {record["uid"] for record in records} == {"20", "21", "22", "23", "24", "100"}
            assert info["status"] == "partial" and info["cacheHits"] == 4
            shopping, education = info["categories"]
            assert all(q["missingPageNumbers"] == [0] for q in shopping["queries"])
            assert shopping["pages"] == shopping["cachedPages"] == 2
            assert education["paginationComplete"] and not education["error"]
            assert len(requested) == 2 and all(r["page_num"] == "0" for r in requested)
        finally:
            await reader.aclose()
    asyncio.run(run())


def test_only_missing_pages_use_api_then_entire_pagination_is_cached(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    requested = []
    def handler(request):
        page = int(request.url.params["page_num"])
        requested.append(page)
        return httpx.Response(200, json=response(page * 20, 2 if page == 2 else 20, total=42))
    async def run():
        await seed_page(tmp_path, "shopping", 1, response(20, 20, total=42))
        for warm in (False, True):
            reader = client(tmp_path, handler)
            try:
                records, info = await pois.PoiService(client=reader).search(CENTER, 1000, ["shopping"], bounds=BOUNDS)
                assert len(records) == 42 and info["status"] == "ready"
                assert info["apiRequests"] == (0 if warm else 4)
                assert info["cacheHits"] == (6 if warm else 2)
                assert all(not q["missingPageNumbers"] for q in info["categories"][0]["queries"])
            finally:
                await reader.aclose()
    asyncio.run(run())
    assert sorted(requested) == [0, 0, 2, 2]


def test_more_than_two_pages_are_retained(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    def handler(request):
        number = int(request.url.params["page_num"])
        return httpx.Response(200, json=response(number * 20, 7 if number == 3 else 20, total=67))
    async def run():
        reader = client(tmp_path, handler)
        try:
            records, info = await pois.PoiService(client=reader).search(CENTER, 1000, ["education"], bounds=BOUNDS)
            assert len(records) == 67 and any(record["uid"] == "66" for record in records)
            assert info["maxPagesPerQuery"] == 8 and info["status"] == "ready"
            assert info["categories"][0]["pages"] == 8
        finally:
            await reader.aclose()
    asyncio.run(run())


def test_many_supermarkets_do_not_skip_the_convenience_keyword(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    requested = []
    def handler(request):
        requested.append(request.url.params["query"])
        is_supermarket = request.url.params["query"] == "超市"
        return httpx.Response(200, json=response(0 if is_supermarket else 20, 20 if is_supermarket else 1,
                                               total=20 if is_supermarket else 1))
    async def run():
        reader = client(tmp_path, handler)
        try:
            records, info = await pois.PoiService(client=reader).search(CENTER, 1000, ["shopping"], bounds=BOUNDS)
            assert len(records) == 21 and info["apiRequests"] == 2
            assert requested == ["超市", "便利店"]
        finally:
            await reader.aclose()
    asyncio.run(run())


def test_short_or_empty_intermediate_pages_do_not_hide_later_pages(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    requested = []
    def handler(request):
        page = int(request.url.params["page_num"])
        requested.append(page)
        return httpx.Response(200, json=response(page * 20, [20, 0, 10][page], total=50))
    async def run():
        reader = client(tmp_path, handler)
        try:
            records, info = await pois.PoiService(client=reader).search(CENTER, 1000, ["shopping"], bounds=BOUNDS)
            assert len(records) == 30 and "49" in {r["uid"] for r in records}
            assert info["status"] == "partial"
            assert info["categories"][0]["providerCountMismatch"]
        finally:
            await reader.aclose()
    asyncio.run(run())
    assert sorted(requested) == [0, 0, 1, 1, 2, 2]


def test_provider_cap_is_never_claimed_to_be_a_complete_inventory(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    def handler(request):
        page = int(request.url.params["page_num"])
        return httpx.Response(200, json=response(page * 20, 10 if page == 7 else 20, total=150))
    async def run():
        reader = client(tmp_path, handler)
        try:
            records, info = await pois.PoiService(client=reader).search(CENTER, 1000, ["shopping"], bounds=BOUNDS)
            assert len(records) == 150 and info["apiRequests"] == 16
            assert info["status"] == "partial" and not info["inventoryVerified"]
            assert info["categories"][0]["providerLimitReached"]
            assert not info["categories"][0]["paginationComplete"]
        finally:
            await reader.aclose()
    asyncio.run(run())


def test_invalid_coordinates_do_not_hide_valid_pois_or_claim_complete_data(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    payload = response(0, 2, total=2)
    payload["results"][1]["location"]["lng"] = "nan"
    async def run():
        reader = client(tmp_path, lambda request: httpx.Response(200, json=payload))
        try:
            records, info = await pois.PoiService(client=reader).search(CENTER, 1000, ["healthcare"], bounds=BOUNDS)
            assert len(records) == 1 and records[0]["uid"] == "0"
            assert info["status"] == "partial" and info["categories"][0]["discardedCount"] == 2
        finally:
            await reader.aclose()
    asyncio.run(run())


def test_budget_is_hard_and_next_search_only_fills_missing_queries(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path, requests=1)
    observed = []
    def handler(request):
        observed.append(request.url.params["query"])
        data = response(0, 1, total=1)
        data["results"][0]["uid"] = request.url.params["query"]
        return httpx.Response(200, json=data)
    async def run():
        for budget, expected in [(1, 1), (96, 1), (96, 0)]:
            configure(monkeypatch, tmp_path, requests=budget)
            reader = client(tmp_path, handler)
            try:
                records, info = await pois.PoiService(client=reader).search(CENTER, 1000, ["shopping"], bounds=BOUNDS)
                assert info["apiRequests"] == expected
                assert info["status"] == ("partial" if budget == 1 else "ready")
                assert len(records) == (1 if budget == 1 else 2)
            finally:
                await reader.aclose()
    asyncio.run(run())
    assert sorted(observed) == ["便利店", "超市"]


def test_fixed_tile_keys_reuse_cache_after_small_center_moves(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    tile = plan_tiles(CENTER, 100, pois.settings, BOUNDS)[0][0]
    observed = []
    def handler(request):
        observed.append(request)
        assert request.url.params["radius_limit"] == "false"
        return httpx.Response(200, json={"status": 0, "total": 0, "results": []})
    async def run():
        for delta in (0, 0.0001):
            reader = client(tmp_path, handler)
            try:
                _, info = await pois.PoiService(client=reader).search(
                    CenterPoint(lng=tile["center"][0] + delta, lat=tile["center"][1], coordType="wgs84ll"),
                    100, ["shopping"])
                assert info["tileCount"] == 1
                if delta:
                    assert info["apiRequests"] == 0
            finally:
                await reader.aclose()
    asyncio.run(run())
    assert len(observed) == 2


def test_quota_failure_stops_workers_and_exposes_reason_without_caching_empty(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    observed = []
    def handler(request):
        observed.append(request)
        return httpx.Response(200, json={"status": 401, "results": []})
    async def run():
        reader = client(tmp_path, handler)
        try:
            records, info = await pois.PoiService(client=reader).search(CENTER, 1000, ["shopping"], bounds=BOUNDS)
            assert not records and info["status"] == "unavailable"
            assert info["quotaLimited"] and not info["authFailed"]
            assert "401" in info["error"]
            assert info["apiRequests"] == 1
        finally:
            await reader.aclose()
    asyncio.run(run())
    assert len(observed) == 1
    assert not list(tmp_path.glob("*.json"))
