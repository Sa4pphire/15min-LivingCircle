"""Tests for the explicitly non-real preview-graph -> engine-v2 adapter."""

import asyncio
from dataclasses import replace
import json
import math
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import engine, network
from app.main import app
from app.schemas import CenterPoint
from app.synthetic_converter import convert_preview_graph


ROOT = Path(__file__).resolve().parents[2]
GENERATED = ROOT / "data/networks/synthetic-preview.json"
SAMPLE_CENTER = CenterPoint(lng=121.501132, lat=31.333337, coordType="wgs84ll")


def _engine_binary() -> Path | None:
    specified = os.getenv("CPP_ENGINE_PATH")
    candidates = ([Path(specified)] if specified else [
        ROOT / "cpp-engine/build/isochrone_engine_synthetic.exe",
        ROOT / "cpp-engine/build/isochrone_engine.exe",
        ROOT / "cpp-engine/build/isochrone_engine",
    ])
    return next((path for path in candidates if path.is_file()), None)


def test_major_sides_and_shared_branches_require_a_charged_crossing() -> None:
    graph = {
        "schemaVersion": 1, "kind": "synthetic-road-graph",
        "coordinateSystem": "preview-local-v1",
        "selectionBoundary": [[[-20, -20], [20, -20], [20, 20],
                               [-20, 20], [-20, -20]]],
        "nodes": [{"id": name, "x": x, "y": y} for name, x, y in (
            ("north", 0, -10), ("junction", 0, 0), ("south", 0, 10),
            ("west", -10, 0), ("east", 10, 0))],
        "edges": [
            {"id": "major-n", "from": "north", "to": "junction",
             "kind": "roadMajor", "sourceWayId": 1},
            {"id": "major-s", "from": "junction", "to": "south",
             "kind": "roadMajor", "sourceWayId": 1},
            {"id": "local-w", "from": "west", "to": "junction",
             "kind": "roadLocal", "sourceWayId": 2},
            {"id": "path-e", "from": "junction", "to": "east",
             "kind": "roadPath", "sourceWayId": 3},
        ],
    }
    converted = convert_preview_graph(graph, [121.505, 31.333])
    assert converted["synthetic"] is True
    assert converted["originWgs84"] == {"lng": 121.505, "lat": 31.333}
    assert len([edge for edge in converted["edges"] if edge["kind"] == "sidewalk"]) == 4
    assert len([edge for edge in converted["edges"] if edge["kind"] == "shared_way"]) == 2
    crossings = [edge for edge in converted["edges"] if edge["kind"] == "crossing"]
    assert len(crossings) == 1
    assert crossings[0]["id"] in converted["sourceGraph"]["syntheticCrossingIds"]
    crossing_x = [point[0] for point in crossings[0]["pathMeters"]]
    assert min(crossing_x) < 0 < max(crossing_x)
    # Shared branches have distinct stubs at the centreline; no free transfer.
    shared = [edge for edge in converted["edges"] if edge["kind"] == "shared_way"]
    assert shared[0]["to"] != shared[1]["from"]
    assert all(not (edge["kind"] == "turn" and edge["from"].endswith(":left")
                    and edge["to"].endswith(":right")) for edge in converted["edges"])


def test_generated_full_map_is_reproducible_and_topologically_valid() -> None:
    graph = json.loads((ROOT / "frontend/src/data/demoRoadGraph.local.json")
                       .read_text(encoding="utf-8"))
    origin = json.loads((ROOT / "frontend/src/data/demoContext.extended.wgs84.json")
                        .read_text(encoding="utf-8"))["originWgs84"]
    generated = json.loads(GENERATED.read_text(encoding="utf-8"))
    assert generated == convert_preview_graph(graph, origin)
    assert generated["sourceGraph"]["nodes"] == 8518
    assert generated["sourceGraph"]["edges"] == 9232
    assert len(generated["nodes"]) > len(graph["nodes"])
    node_ids = {node["id"] for node in generated["nodes"]}
    edge_ids = {edge["id"] for edge in generated["edges"]}
    assert len(node_ids) == len(generated["nodes"])
    assert len(edge_ids) == len(generated["edges"])
    for edge in generated["edges"]:
        assert edge["from"] in node_ids and edge["to"] in node_ids
        assert edge["pathMeters"][0] != edge["pathMeters"][-1]
        if edge["kind"] == "sidewalk":
            assert edge["side"] in ("left", "right")
            assert edge["streetBlockId"].startswith("major:")
        elif edge["kind"] == "shared_way":
            assert edge["sharedWayType"] == "shared_alley"
            assert edge["widthMeters"] > 0
    assert generated["sourceGraph"]["syntheticCrossingIds"]


def test_full_network_python_cpp_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    binary = _engine_binary()
    if binary is None:
        pytest.skip("C++ engine binary is not available")
    monkeypatch.setattr(network, "settings", replace(
        network.settings, walking_network_path=GENERATED))
    monkeypatch.setattr(engine, "settings", replace(
        engine.settings, cpp_engine_path=binary))
    payload, metadata = network.load_engine_request(SAMPLE_CENTER)
    assert payload["maxOriginSnapMeters"] == pytest.approx(1170)
    assert payload["allowOffNetworkOrigin"] is True
    assert len(payload["nodes"]) > 8518
    engine_result = asyncio.run(engine.run_engine(payload))
    assert engine_result["reachableEdges"]
    assert engine_result["displayGeometryMeters"]["type"] == "MultiPolygon"
    report = network.build_analysis_result(engine_result, metadata)
    assert report["metadata"]["networkSource"] == "synthetic"
    assert report["metadata"]["coordType"] == "wgs84ll"
    assert report["metadata"]["originAccessSeconds"] == pytest.approx(
        report["metadata"]["originSnapMeters"] / 1.3)
    assert report["isochrone"]["properties"]["coordType"] == "wgs84ll"
    assert "SYNTHETIC_NETWORK_NOT_REAL_WORLD" in report["warnings"]
    assert len(report["reachableWalkways"]["features"]) > 100
    assert report["metrics"]["facilityCount"] == 0
    # The middle of the selection region is roughly 200 m from the nearest
    # mapped street. It must now produce a shorter road-network isochrone.
    offroad = CenterPoint(lng=121.505, lat=31.333, coordType="wgs84ll")
    offroad_payload, offroad_meta = network.load_engine_request(offroad)
    offroad_result = asyncio.run(engine.run_engine(offroad_payload))
    offroad_report = network.build_analysis_result(offroad_result, offroad_meta)
    assert offroad_report["metadata"]["originSnapMeters"] > 30
    assert offroad_report["metadata"]["originAccessSeconds"] > 0
    assert offroad_report["isochrone"]["geometry"]["type"] == "MultiPolygon"
    assert offroad_report["isochrone"]["geometry"]["coordinates"]
    assert "UNVERIFIED_STRAIGHT_LINE_ORIGIN_ACCESS" in offroad_report["warnings"]
    with pytest.raises(network.UnsupportedAreaError, match="需要 wgs84ll"):
        network.load_engine_request(CenterPoint(lng=SAMPLE_CENTER.lng,
                                                lat=SAMPLE_CENTER.lat))
    # This point is inside the bounding rectangle but outside the four-road
    # selection polygon; API callers cannot bypass the front-end hit test.
    with pytest.raises(network.UnsupportedAreaError, match="合成演示选区"):
        network.load_engine_request(CenterPoint(
            lng=121.505 - 1300 / (111320 * math.cos(math.radians(31.333))),
            lat=31.333 + 1200 / 111320, coordType="wgs84ll"))


def test_http_api_exposes_the_synthetic_cpp_result(monkeypatch: pytest.MonkeyPatch,
                                                   tmp_path: Path) -> None:
    binary = _engine_binary()
    if binary is None:
        pytest.skip("C++ engine binary is not available")
    monkeypatch.setattr(network, "settings", replace(
        network.settings, walking_network_path=tmp_path / "no-real-network.json"))
    monkeypatch.setattr(engine, "settings", replace(
        engine.settings, cpp_engine_path=binary))
    with TestClient(app) as client:
        default = client.post("/api/v1/analyses", json={
            "center": SAMPLE_CENTER.model_dump(), "minutes": 15,
        })
        assert default.status_code == 422
        accepted = client.post("/api/v1/synthetic-analyses", json={
            "center": SAMPLE_CENTER.model_dump(), "minutes": 15,
        })
        assert accepted.status_code == 202
        state = client.get(f"/api/v1/analyses/{accepted.json()['analysisId']}")
        offroad_accepted = client.post("/api/v1/synthetic-analyses", json={
            "center": {"lng": 121.505, "lat": 31.333,
                       "coordType": "wgs84ll"}, "minutes": 15,
        })
        assert offroad_accepted.status_code == 202
        offroad_state = client.get(
            f"/api/v1/analyses/{offroad_accepted.json()['analysisId']}")
    assert state.status_code == 200
    body = state.json()
    assert body["status"] == "completed"
    assert body["result"]["metadata"]["coordType"] == "wgs84ll"
    assert body["result"]["reachableWalkways"]["features"]
    assert offroad_state.status_code == 200
    assert offroad_state.json()["status"] == "completed"
    assert offroad_state.json()["result"]["metadata"]["originSnapMeters"] > 30
    assert offroad_state.json()["result"]["isochrone"]["geometry"]["coordinates"]
