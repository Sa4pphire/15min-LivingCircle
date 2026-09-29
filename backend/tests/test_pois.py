import asyncio
from dataclasses import replace
import json
from pathlib import Path
import time

import httpx
from fastapi.testclient import TestClient
import pytest

from app import engine, local_experiment, main, pois
from app.baidu.client import BaiduClient
from app.baidu.errors import BaiduQuotaError
from app.poi_cache import PoiCache
from app.schemas import CenterPoint
from app.poi_search import plan_tiles


ROOT = Path(__file__).resolve().parents[2]


def configure(monkeypatch, tmp_path, **kwargs):
    value = replace(pois.settings, poi_cache_path=tmp_path / "pois.sqlite3",
                    analysis_cache_dir=tmp_path,
                    poi_max_pages=2, poi_budget_seconds=2, **kwargs)
    monkeypatch.setattr(pois, "settings", value)
    return value


def test_cache_persists_empty_results_refreshes_and_coalesces(tmp_path):
    path = tmp_path / "cache.sqlite3"
    calls = 0

    async def run():
        nonlocal calls
        async def fetch():
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.02)
            return {"items": []}
        first = PoiCache(path, 3600, 7200)
        second = PoiCache(path, 3600, 7200)
        outputs = await asyncio.gather(first.get_or_fetch({"query": "学校"}, fetch),
                                       second.get_or_fetch({"query": "学校"}, fetch))
        assert {value[1] for value in outputs} == {"hit", "miss"}
        assert calls == 1
        assert (await second.get_or_fetch({"query": "学校"}, fetch))[1] == "hit"
        assert (await first.get_or_fetch({"query": "学校"}, fetch, refresh=True))[1] == "miss"
        assert calls == 2
    asyncio.run(run())


def test_expired_cache_uses_stale_data_on_error_without_storing_error_as_empty(tmp_path):
    cache = PoiCache(tmp_path / "cache.sqlite3", 0.02, 100)

    async def run():
        async def success(): return {"items": [{"uid": "known"}]}
        async def failure(): raise BaiduQuotaError("配额不足")
        await cache.get_or_fetch({"provider": "baidu", "query": "学校"}, success)
        await asyncio.sleep(0.03)
        value, source, _ = await cache.get_or_fetch({"provider": "baidu", "query": "学校"}, failure)
        assert value["items"][0]["uid"] == "known" and source == "stale"
        with pytest.raises(BaiduQuotaError, match="冷却"):
            await cache.get_or_fetch({"provider": "baidu", "query": "医院"}, success)
    asyncio.run(run())


def test_poi_pages_use_correct_datum_detail_and_navigation_fields():
    observed = []
    def handler(request):
        observed.append(dict(request.url.params))
        return httpx.Response(200, json={"status": 0, "total": 2, "results": [
            {"uid": "p", "name": "学校", "location": {"lng": 121.5, "lat": 31.3},
             "detail_info": {"tag": "教育", "navi_location": {"lng": 121.501, "lat": 31.301}}},
            {"uid": "bad", "name": "bad", "location": {"lng": "nan", "lat": 31.3}}]})
    async def run():
        client = BaiduClient(ak="test-only-server-key", transport=httpx.MockTransport(handler))
        try:
            page = await client.search_poi_page("学校", (121.5, 31.3), coord_type="wgs84ll")
            assert page["discardedCount"] == 1
            assert page["items"][0]["navigationPoint"] == [121.501, 31.301]
            assert page["items"][0]["coordType"] == "bd09ll"
        finally: await client.aclose()
    asyncio.run(run())
    assert observed[0]["coord_type"] == "1"
    assert observed[0]["scope"] == "2" and observed[0]["radius_limit"] == "true"
    assert observed[0]["location"] == "31.3,121.5"


def test_search_caps_pages_deduplicates_and_reuses_disk_cache(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"status": 0, "total": 150, "results": [
            {"uid": str(i), "name": "候选", "location": {"lng": 121.5, "lat": 31.3}}
            for i in range(20)]})
    async def once():
        client = BaiduClient(ak="test-key", transport=httpx.MockTransport(handler))
        service = pois.PoiService(client=client)
        try: return await service.search(CenterPoint(lng=121.5, lat=31.3), 1000, pois.CATEGORIES,
                                        bounds=[121.5, 31.3, 121.5, 31.3])
        finally: await client.aclose()
    records, info = asyncio.run(once())
    planned_pages = sum(len(cat["keywords"]) for cat in pois.CATEGORIES.values()) * 2
    assert len(records) == 20 and len(requests) == planned_pages
    assert all(len(record["categories"]) == len(pois.CATEGORIES) for record in records)
    assert info["status"] == "partial" and not info["inventoryVerified"]
    _, second = asyncio.run(once())
    assert second["apiRequests"] == 0 and second["cacheHits"] == planned_pages
    assert len(requests) == planned_pages
    # The cache contains normalized public data, not the request's AK.
    assert b"test-key" not in pois.settings.poi_cache_path.read_bytes()


def test_neighboring_points_reuse_same_spatial_query(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    count = 0
    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(200, json={"status": 0, "results": []})
    seed = CenterPoint(lng=121.5, lat=31.3)
    grid = plan_tiles(seed, 100, pois.settings, [121.5, 31.3, 121.5, 31.3])[0][0]["center"]
    async def run():
        client = BaiduClient(ak="test", transport=httpx.MockTransport(handler))
        service = pois.PoiService(client=client)
        try:
            await service.search(CenterPoint(lng=grid[0], lat=grid[1]), 100, ["shopping"])
            _, info = await service.search(CenterPoint(lng=grid[0] + 0.0001, lat=grid[1]), 100, ["shopping"])
            assert info["cacheHits"] == 2
        finally: await client.aclose()
    asyncio.run(run())
    assert count == 2


def test_alignment_uses_cached_forward_conversion_not_unsupported_gps_reverse(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    requests = []
    def handler(request):
        requests.append(request)
        assert request.url.params["model"] == "2"
        points = [[float(value) for value in text.split(",")] for text in request.url.params["coords"].split(";")]
        return httpx.Response(200, json={"status": 0, "result": [
            {"x": point[0] + 0.01, "y": point[1] + 0.01} for point in points]})
    async def run():
        client = BaiduClient(ak="test", transport=httpx.MockTransport(handler))
        service = pois.PoiService(client=client)
        try:
            project, kind = await service.frame({"coordType": "wgs84ll", "originWgs84": {"lng": 121.5, "lat": 31.3}})
            assert project([121.51, 31.31]) == pytest.approx([0, 0], abs=1e-6)
            assert kind.startswith("approximate")
            await service.frame({"coordType": "wgs84ll", "originWgs84": {"lng": 121.5, "lat": 31.3}})
        finally: await client.aclose()
    asyncio.run(run())
    assert len(requests) == 1


def test_access_rejects_wrong_side_and_does_not_connect_centroids():
    edges = [{"id": "left", "kind": "sidewalk", "streetBlockId": "road", "side": "left", "pathMeters": [[0, -1], [100, -1]]},
             {"id": "right", "kind": "sidewalk", "streetBlockId": "road", "side": "right", "pathMeters": [[0, 1], [100, 1]]}]
    index = pois.AccessIndex(edges, 3)
    assert index.match([50, 0]) == (None, "ambiguous_side")
    assert index.match([50, 20]) == (None, "not_on_modeled_way")
    assert index.match([50, -1])[0][0]["id"] == "left"


def test_display_counts_preserve_polygon_holes_and_do_not_imply_reachability():
    geometry = {"type": "MultiPolygon", "coordinates": [[[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
                                                         [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]]]}
    meta = {"poi": {"status": "ready"}, "poiRecords": [
        {"uid": "inside", "name": "候选", "address": "", "category": "shopping", "categories": ["shopping"],
         "lng": 121.5, "lat": 31.3, "localPointMeters": [2, 2], "accessStatus": "missing_navigation_point"},
        {"uid": "hole", "name": "内洞", "address": "", "category": "shopping", "categories": ["shopping"],
         "lng": 121.5, "lat": 31.3, "localPointMeters": [5, 5]}]}
    report = pois.add_poi_result({"metadata": {}, "warnings": []}, {"displayGeometryMeters": geometry}, meta)
    shopping = next(item for item in report["poiCategories"] if item["category"] == "shopping")
    assert shopping["insideDisplayCount"] == 1 and shopping["modelReachableCount"] == 0
    assert report["poiFacilities"]["features"][0]["properties"]["modelReachable"] is None


def test_enrichment_does_not_rewrite_graph_and_real_cpp_roundtrip(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    fixture = ROOT / "contracts/engine-local-experiment.input.example.json"
    original = fixture.read_bytes()
    payload, metadata = local_experiment.load_local_experiment_request(
        CenterPoint(lng=121.502102644, lat=31.3), "west_lane", network_path=fixture)
    lng = 121.5 + 100 / (111320 * __import__("math").cos(__import__("math").radians(31.3)))
    def handler(request):
        query = request.url.params["query"]
        data = {"uid": query, "name": query, "location": {"lng": lng, "lat": 31.3}}
        if query.startswith("超市"):
            data["detail_info"] = {"navi_location": {"lng": lng, "lat": 31.3}}
        return httpx.Response(200, json={"status": 0, "results": [data]})
    factory = pois.PoiService
    monkeypatch.setattr(pois, "PoiService", lambda: factory(client=BaiduClient(ak="test", transport=httpx.MockTransport(handler))))
    asyncio.run(pois.enrich_engine_pois(payload, metadata, CenterPoint(lng=121.502102644, lat=31.3)))
    assert len(payload["serviceCategories"]) == len(pois.CATEGORIES)
    assert len(payload["facilities"]) == 2  # Existing annotated shop plus the API navigation point.
    assert all(category["localInventoryStatus"] == "incomplete" for category in payload["serviceCategories"])
    assert fixture.read_bytes() == original
    assert sum(record["accessStatus"] == "missing_navigation_point" for record in metadata["poiRecords"]) == 10
    binary = ROOT / "cpp-engine/build/demo-launcher/isochrone_engine.exe"
    if not binary.is_file(): pytest.skip("C++ executable unavailable")
    monkeypatch.setattr(engine, "settings", replace(engine.settings, cpp_engine_path=binary))
    result = asyncio.run(engine.run_engine(payload))
    report = pois.add_poi_result(local_experiment.build_local_experiment_result(result, metadata), result, metadata)
    assert len(result["localGrayZones"]) == len(pois.CATEGORIES)
    assert all(not item["candidateUncoveredEdges"] for item in result["localGrayZones"])
    assert any(feature["properties"]["modelReachable"] is True for feature in report["poiFacilities"]["features"])


def test_standalone_search_endpoint_and_missing_credentials_are_not_empty_census(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    factory = pois.PoiService
    monkeypatch.setattr(main, "PoiService", lambda: factory(client=BaiduClient(ak="")))
    client = TestClient(main.app)
    result = client.post("/api/v1/pois/search", json={"center": {"lng": 121.5, "lat": 31.3}, "categories": ["education"]})
    assert result.status_code == 200
    assert result.json()["metadata"]["status"] == "unavailable"
    assert result.json()["metadata"]["apiRequests"] == 0
    assert result.json()["metadata"]["categories"][0]["error"]
    for unsupported in ("bad", "market", "pharmacy"):
        assert client.post("/api/v1/pois/search", json={"center": {"lng": 121.5, "lat": 31.3}, "categories": [unsupported]}).status_code == 422


def test_standalone_dining_search_uses_food_category_and_warm_cache(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    requested = []
    def handler(request):
        requested.append(request.url.params["query"])
        return httpx.Response(200, json={"status": 0, "total": 1, "results": [
            {"uid": "test-dining", "name": "测试餐饮店", "location": {"lng": 121.5, "lat": 31.3}}]})
    factory = pois.PoiService
    monkeypatch.setattr(main, "PoiService", lambda: factory(client=BaiduClient(
        ak="test", transport=httpx.MockTransport(handler))))
    client = TestClient(main.app)
    request = {"center": {"lng": 121.5, "lat": 31.3}, "radiusMeters": 100, "categories": ["dining"]}
    first = client.post("/api/v1/pois/search", json=request)
    assert first.status_code == 200
    body = first.json()
    assert body["categoryLabels"] == {"dining": "餐饮"}
    assert body["items"][0]["category"] == "dining"
    assert body["items"][0]["matchedKeywords"] == ["美食"]
    assert requested and set(requested) == {"美食"}
    calls = len(requested)
    second = client.post("/api/v1/pois/search", json=request)
    assert second.status_code == 200
    assert second.json()["items"] == body["items"]
    assert second.json()["metadata"]["apiRequests"] == 0
    assert len(requested) == calls
