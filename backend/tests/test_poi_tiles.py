"""Geometry, category, TTL and two-stage integration checks (no online API)."""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path

import pytest

from app import main, pois
from app.cache import SharedBaiduCache, save_cached_json
from app.local_alignment import load_grid_frame
from app.poi_search import plan_tiles
from app.schemas import AnalysisState, CenterPoint, PoiSearchRequest, Progress


ROOT = Path(__file__).resolve().parents[2]


def test_tile_circles_cover_bbox_corners_and_are_independent_of_origin():
    center = CenterPoint(lng=121.5, lat=31.3)
    bounds = [121.49, 31.29, 121.51, 31.31]
    tiles, plan = plan_tiles(center, 1500, pois.settings, bounds)
    assert not plan["tileLimitReached"]
    for lng in (bounds[0], bounds[2]):
        for lat in (bounds[1], bounds[3]):
            assert any(math.hypot((lng - t["center"][0]) * 111320 * math.cos(math.radians(lat)),
                                  (lat - t["center"][1]) * 111320) <= t["radiusMeters"] for t in tiles)
    moved, _ = plan_tiles(CenterPoint(lng=121.5001, lat=31.3), 1500, pois.settings, bounds)
    assert {t["id"] for t in moved} == {t["id"] for t in tiles}
    assert {t["id"]: t["center"] for t in moved} == {t["id"]: t["center"] for t in tiles}


def test_oversized_region_is_explicitly_partial_and_invalid_bounds_are_rejected():
    center = CenterPoint(lng=121.5, lat=31.3)
    config = replace(pois.settings, poi_max_tiles=1)
    tiles, plan = plan_tiles(center, 5000, config)
    assert len(tiles) == 1 and plan["tileLimitReached"] and plan["plannedTileCount"] > 1
    for invalid in ([121.51, 31.3, 121.5, 31.4], [121.5, float("nan"), 121.6, 31.4]):
        with pytest.raises(ValueError):
            plan_tiles(center, 500, config, invalid)


def test_demo_categories_and_search_defaults_exclude_market_and_pharmacy():
    expected = ["education", "healthcare", "shopping", "public_service", "dining"]
    assert list(pois.CATEGORIES) == expected
    assert PoiSearchRequest(center={"lng": 121.5, "lat": 31.3}).categories == expected
    assert "便利店" in pois.CATEGORIES["shopping"]["keywords"]
    assert pois.CATEGORIES["dining"]["label"] == "餐饮"
    assert pois.CATEGORIES["dining"]["keywords"] == ("美食",)
    keywords = {word for category in pois.CATEGORIES.values() for word in category["keywords"]}
    assert len(keywords) == 11
    assert not keywords.intersection({"菜市场", "农贸市场", "药店", "药房"})


def test_successful_empty_cache_expires_sooner_than_positive_inventory(tmp_path):
    cache = SharedBaiduCache(tmp_path)
    params = {"provider": "https://api.map.baidu.com", "path": "/place/v2/search", "params": {"query": "超市"}}
    key = cache.key(params)
    yesterday = datetime.now(timezone.utc) - timedelta(hours=25)
    save_cached_json(key, {"status": 0, "results": []}, cache_dir=tmp_path, now=yesterday)
    assert cache.peek(params, ttl_seconds=168 * 3600) is None
    save_cached_json(key, {"status": 0, "results": [{"uid": "known"}]}, cache_dir=tmp_path, now=yesterday)
    assert cache.peek(params, ttl_seconds=168 * 3600) is not None
    save_cached_json(key, {"status": 4, "results": []}, cache_dir=tmp_path)
    assert cache.peek(params, ttl_seconds=168 * 3600) is None


def test_prepared_mesh_inverse_matches_frontend_axis_at_anchors_without_api():
    path = ROOT / "frontend/src/data/demoMap.bd09.json"
    if not path.is_file():
        pytest.skip("Prepared map mesh unavailable")
    asset = json.loads(path.read_text(encoding="utf-8"))
    lng, lat = asset["input"]["originWgs84"]
    project, kind = load_grid_frame({"coordType": "wgs84ll", "originWgs84": {"lng": lng, "lat": lat}}, path)
    assert kind == "prepared_baidu_bilinear_grid"
    assert project(asset["centerBd09"]) == pytest.approx([0, 0], abs=1e-5)
    grid = asset["alignment"]
    for index in range(0, len(grid["pointsBd09"]), 19):
        expected = [grid["minLocalMeters"][0] + index % grid["columns"] * grid["stepMeters"],
                    -(grid["minLocalMeters"][1] + index // grid["columns"] * grid["stepMeters"])]
        assert project(grid["pointsBd09"][index]) == pytest.approx(expected, abs=1e-5)
    assert project([0, 0]) is None
    assert load_grid_frame({"coordType": "wgs84ll", "originWgs84": {"lng": lng + 0.1, "lat": lat}}, path) is None


def test_engine_is_run_before_poi_search_and_roi_bounds_replace_large_radius(monkeypatch, tmp_path):
    stages = []
    monkeypatch.setattr(pois, "settings", replace(pois.settings, analysis_cache_dir=tmp_path,
                                                 poi_cache_path=tmp_path / "coordination.sqlite3"))
    payload = {"originMeters": [0, 0], "thresholdSeconds": 900, "walkingSpeedMetersPerSecond": 1.3,
               "edges": [], "facilities": []}
    metadata = {"coordType": "bd09ll", "originBd09": {"lng": 121.5, "lat": 31.3}}
    result = {"displayGeometryMeters": {"type": "MultiPolygon", "coordinates": [
        [[[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]]]]}, "reachableEdges": []}

    class FakeClient:
        cache_stats = {"apiRequests": 0, "cacheHits": 0, "cacheMisses": 0, "stalePages": 0}
        async def aclose(self):
            pass

    class FakeService:
        client = FakeClient()
        stats = client.cache_stats
        async def search(self, center, radius, categories, **kwargs):
            stages.append("poi")
            bounds = kwargs["bounds"]
            assert bounds[2] - bounds[0] < 0.002
            assert bounds[3] - bounds[1] < 0.002
            return [], {"status": "ready"}

    async def compute(data):
        stages.append("engine")
        return result

    monkeypatch.setattr(pois, "PoiService", FakeService)
    monkeypatch.setattr(main, "run_engine", compute)
    monkeypatch.setattr(main, "build_analysis_result", lambda r, m: {"metadata": {}, "warnings": []})
    identifier = "tile-integration-test"
    main._analyses[identifier] = AnalysisState(analysisId=identifier, status="queued",
                                              progress=Progress(stage="queued", percent=0))
    try:
        asyncio.run(main._run_analysis(identifier, payload, metadata, CenterPoint(lng=121.5, lat=31.3), True))
        assert main._analyses[identifier].status == "completed"
        assert stages == ["engine", "poi", "engine"]
        assert len(main._analyses[identifier].result["poiCategories"]) == 5
    finally:
        main._analyses.pop(identifier, None)
