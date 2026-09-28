"""All file/stdio demo entry points use the existing modeled synthetic graph."""

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess

import pytest
from fastapi.testclient import TestClient

from app import engine, local_experiment, main, network
from app.schemas import CenterPoint

ROOT = Path(__file__).resolve().parents[2]
NETWORK = ROOT / "data/networks/synthetic-preview.json"
ORIGIN = CenterPoint(lng=121.505, lat=31.333, coordType="wgs84ll")
EDGE_CENTER = CenterPoint(lng=121.504429458, lat=31.331174183, coordType="wgs84ll")


@pytest.fixture
def binary(monkeypatch: pytest.MonkeyPatch) -> Path:
    path = Path(os.getenv("CPP_ENGINE_PATH", str(ROOT / "cpp-engine/build/isochrone_engine")))
    if not path.is_file() and path.with_suffix(".exe").is_file():
        path = path.with_suffix(".exe")
    if not path.is_file():
        pytest.skip("C++ engine has not been built")
    path = path.resolve()
    monkeypatch.setattr(engine, "settings", replace(engine.settings, cpp_engine_path=path))
    monkeypatch.setenv("SYNTHETIC_NETWORK_PATH", str(NETWORK))
    return path


def invoke(binary: Path, *arguments: str, cwd: Path) -> tuple[int, dict]:
    process = subprocess.run([str(binary), *arguments], cwd=cwd,
                             capture_output=True, timeout=25)
    return process.returncode, json.loads(process.stdout.decode("utf-8"))


def test_graph_file_and_stdio_return_the_same_computation(binary: Path, tmp_path: Path) -> None:
    payload, _ = network.load_engine_request(ORIGIN, network_path=NETWORK)
    expected = asyncio.run(engine.run_engine(payload))
    code, response = invoke(binary, "--network", str(NETWORK), cwd=tmp_path)
    assert code == 0 and response["success"] is True
    result = response["result"]
    assert "SYNTHETIC_NETWORK_NOT_REAL_WORLD" in result["diagnostics"]["warnings"]
    result["diagnostics"]["warnings"].remove("SYNTHETIC_NETWORK_NOT_REAL_WORLD")
    assert result == expected
    code, demo = invoke(binary, "--demo", cwd=tmp_path)
    assert code == 0 and demo["success"] is True
    demo["result"]["diagnostics"]["warnings"].remove("SYNTHETIC_NETWORK_NOT_REAL_WORLD")
    assert demo["result"] == expected
    # --input reads a normalized request, not another set of road data.
    request_file = tmp_path / "request with spaces.json"
    request_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    code, response = invoke(binary, "--input", str(request_file), cwd=tmp_path)
    assert code == 0
    assert response["result"] == expected


def test_local_graph_file_keeps_missing_data_explicit(binary: Path, tmp_path: Path) -> None:
    code, response = invoke(binary, "--network", str(NETWORK), "--local",
                            "--origin-meters", "-54.25", "-203.25",
                            "--origin-edge", "w:154811345:2:0", cwd=tmp_path)
    assert code == 0 and response["success"] is True
    result = response["result"]
    assert result["reachableEdges"]
    assert result["localGrayZones"] == [] and result["grayZones"] == []
    assert result["facilityTravelTimes"] == []
    assert result["displayGeometryMeters"]["coordinates"] == []
    assert "LOCAL_FACILITY_DATA_NOT_PROVIDED" in result["diagnostics"]["warnings"]
    assert "LOCAL_BOUNDARY_NOT_MARKED" in result["diagnostics"]["warnings"]


def test_missing_file_and_bad_arguments_do_not_fall_back(binary: Path, tmp_path: Path) -> None:
    code, response = invoke(binary, "--network", str(tmp_path / "missing.json"), cwd=tmp_path)
    assert code == 2 and response["error"]["code"] == "INPUT_FILE_ERROR"
    for arguments in [("--network",), ("--unknown",), ("--local",),
                      ("--network", str(NETWORK), "--origin-meters", "nan", "0")]:
        code, response = invoke(binary, *arguments, cwd=tmp_path)
        assert code == 2 and response["error"]["code"] == "INVALID_INPUT"


def test_both_demo_apis_use_the_same_existing_model(binary: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "settings", replace(main.settings, synthetic_network_path=NETWORK))
    monkeypatch.setattr(local_experiment, "settings", replace(
        local_experiment.settings, local_experiment_network_path=NETWORK))
    with TestClient(main.app) as client:
        created = client.post("/api/v1/local-experiments", json={
            "center": EDGE_CENTER.model_dump(), "originEdgeId": "w:154811345:2:0"})
        assert created.status_code == 202
        state = client.get(f"/api/v1/local-experiments/{created.json()['analysisId']}").json()
        assert state["status"] == "completed"
        result = state["result"]
        assert result["reachableWalkways"]["features"]
        assert result["metadata"]["networkFile"] == NETWORK.name
        stored_graph = json.loads(NETWORK.read_text(encoding="utf-8"))
        assert result["metadata"]["networkNodeCount"] == len(stored_graph["nodes"])
        assert result["metadata"]["networkEdgeCount"] == len(stored_graph["edges"])
        assert result["metadata"]["grayZoneStatus"] == "data_insufficient"
        assert result["categorySegments"]["features"] == []
        off_network = client.post("/api/v1/local-experiments", json={"center": ORIGIN.model_dump()})
        assert off_network.status_code == 202
        failed = client.get(f"/api/v1/local-experiments/{off_network.json()['analysisId']}").json()
        assert failed["status"] == "failed"
        assert "ORIGIN_NOT_ON_WALKWAY" in failed["error"]

    main_payload, _ = network.load_engine_request(EDGE_CENTER, network_path=NETWORK)
    local_payload, _ = local_experiment.load_local_experiment_request(EDGE_CENTER, network_path=NETWORK)
    for key in ("nodes", "edges", "facilities", "serviceCategories"):
        assert main_payload[key] == local_payload[key]
    assert main_payload["thresholdSeconds"] == 900 and local_payload["thresholdSeconds"] == 180
