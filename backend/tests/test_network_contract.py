"""Synthetic v2 contract tests; no real demonstration-area network is implied."""

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path

import pytest

from app import engine, network
from app.schemas import CenterPoint


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_output_example_is_self_consistent() -> None:
    example = json.loads((REPO_ROOT / "contracts" /
                          "engine-output.example.json").read_text(encoding="utf-8"))
    assert example["schemaVersion"] == 2
    assert example["success"] is True
    result = example["result"]
    assert result["displayGeometryMeters"]["type"] == "MultiPolygon"
    assert result["displayGeometryMeters"]["coordinates"] == result["displayPolygonMeters"]
    for polygon in result["displayPolygonMeters"]:
        for ring in polygon:
            assert len(ring) >= 4 and ring[0] == ring[-1]


def test_polygon_holes_and_connected_over_time() -> None:
    engine_result = {
        "displayPolygonMeters": [[
            [[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]],
            [[20, 20], [20, 80], [80, 80], [80, 20], [20, 20]],
        ]],
        "displayGeometryMeters": {
            "type": "MultiPolygon",
            "coordinates": [[
                [[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]],
                [[20, 20], [20, 80], [80, 80], [80, 20], [20, 20]],
            ]],
        },
        "reachableEdges": [],
        "frontierMeters": [],
        "facilityTravelTimes": [{
            "id": "shop", "accessEdgeId": "walk", "reachable": False,
            "travelTimeSeconds": 1000.0,
        }],
        "snapDistanceMeters": 0,
        "diagnostics": {"reachableNodeCount": 1,
                        "reachableCrossingCount": 0, "warnings": []},
    }
    metadata = {
        "originBd09": {"lng": 121.5, "lat": 31.3},
        "networkSource": "synthetic",
        "facilities": [{"id": "shop", "accessEdgeId": "walk",
                        "accessPointMeters": [50, 50]}],
    }
    result = network.build_analysis_result(engine_result, metadata)
    assert len(result["isochrone"]["geometry"]["coordinates"][0]) == 2
    assert result["facilities"]["features"][0]["properties"]["travelTimeSeconds"] == 1000
    assert result["metrics"]["reachableFacilityCount"] == 0


def test_python_cpp_synthetic_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    binary = Path(os.getenv("CPP_ENGINE_PATH", "cpp-engine/build/isochrone_engine"))
    if not binary.is_absolute():
        binary = REPO_ROOT / binary
    if not binary.is_file() and (binary.with_suffix(".exe")).is_file():
        binary = binary.with_suffix(".exe")
    if not binary.is_file():
        pytest.skip("C++ engine has not been built; CI builds it before this test")
    fixture = REPO_ROOT / "contracts" / "engine-input.example.json"
    monkeypatch.setattr(network, "settings", replace(
        network.settings, walking_network_path=fixture))
    monkeypatch.setattr(engine, "settings", replace(
        engine.settings, cpp_engine_path=binary))
    payload, metadata = network.load_engine_request(
        CenterPoint(lng=121.5, lat=31.3))
    assert payload["originEdgeId"] == "west_south_sidewalk"
    assert any(edge["kind"] == "shared_way" for edge in payload["edges"])
    result = asyncio.run(engine.run_engine(payload))
    report = network.build_analysis_result(result, metadata)
    assert report["metadata"]["networkSource"] == "synthetic"
    assert report["metrics"]["facilityCount"] == 1
    assert report["metrics"]["reachableFacilityCount"] == 1
    assert report["facilities"]["features"][0]["properties"]["travelTimeSeconds"] > 0
    assert any(feature["properties"]["kind"] == "shared_way"
               for feature in report["reachableWalkways"]["features"])
    ambiguous = dict(payload)
    ambiguous.pop("originEdgeId")
    ambiguous["originMeters"] = {"xMeters": 0, "yMeters": 10}
    with pytest.raises(engine.EngineError, match="AMBIGUOUS_ORIGIN_SIDE"):
        asyncio.run(engine.run_engine(ambiguous))

    minimal_input = json.loads((REPO_ROOT / "contracts" /
                                "engine-input.minimal.example.json").read_text(encoding="utf-8"))
    expected_output = json.loads((REPO_ROOT / "contracts" /
                                  "engine-output.example.json").read_text(encoding="utf-8"))
    minimal_result = asyncio.run(engine.run_engine(minimal_input))
    assert set(minimal_result) == set(expected_output["result"])
    assert minimal_result["displayGeometryMeters"]["type"] == "MultiPolygon"
    assert (minimal_result["displayGeometryMeters"]["coordinates"] ==
            minimal_result["displayPolygonMeters"])
    assert minimal_result["facilityTravelTimes"][0]["travelTimeSeconds"] == pytest.approx(
        expected_output["result"]["facilityTravelTimes"][0]["travelTimeSeconds"]
    )
