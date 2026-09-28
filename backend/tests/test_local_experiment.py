"""The short-range experiment stays separate from the 900-second main report."""

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app import engine, local_experiment, main, network
from app.schemas import CenterPoint


REPO_ROOT = Path(__file__).resolve().parents[2]
CLIENT = TestClient(main.app)


def _local_fixture(tmp_path: Path, *, topology_status: str = "verified") -> Path:
    payload = json.loads((REPO_ROOT / "contracts" / "engine-input.example.json").read_text(
        encoding="utf-8"))
    payload["localExperiment"] = {
        "boundaryNodeIds": ["sw2", "nw2", "turn1", "far1", "shared1"],
        "topologyStatus": topology_status,
    }
    payload["serviceCategories"][0]["localInventoryStatus"] = "verified"
    payload["serviceCategories"][1]["localInventoryStatus"] = "incomplete"
    path = tmp_path / "synthetic-local.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _fake_result() -> dict:
    segment = {
        "edgeId": "west_south_sidewalk",
        "kind": "sidewalk",
        "pathMeters": [[0, 0], [100, 0]],
    }
    return {
        "reachableEdges": [segment],
        "localGrayZones": [
            {"category": "shopping", "coveredEdges": [segment],
             "candidateUncoveredEdges": [], "unknownEdges": []},
            {"category": "healthcare", "coveredEdges": [],
             "candidateUncoveredEdges": [], "unknownEdges": [segment]},
        ],
        "diagnostics": {"warnings": ["LOCAL_REACHABILITY_MAY_BE_TRUNCATED"]},
    }


def test_local_loader_uses_independent_threshold_and_review_states(tmp_path: Path) -> None:
    path = _local_fixture(tmp_path)
    payload, metadata = local_experiment.load_local_experiment_request(
        CenterPoint(lng=121.5, lat=31.3),
        origin_edge_id="west_south_sidewalk", network_path=path)
    assert payload["thresholdSeconds"] == 180
    assert payload["maxOriginSnapMeters"] == 5
    assert payload["allowOffNetworkOrigin"] is False
    assert payload["localExperiment"]["boundaryNodeIds"][0] == "sw2"
    assert payload["localExperiment"]["topologyStatus"] == "verified"
    assert metadata["networkSource"] == "synthetic"
    assert payload["serviceCategories"][1]["localInventoryStatus"] == "incomplete"


def test_main_loader_cannot_use_a_local_experiment_graph(tmp_path: Path) -> None:
    with pytest.raises(network.UnsupportedAreaError, match="local-experiments"):
        network.load_engine_request(
            CenterPoint(lng=121.5, lat=31.3), network_path=_local_fixture(tmp_path))


def test_local_result_contains_only_exact_segment_layers(tmp_path: Path) -> None:
    _, metadata = local_experiment.load_local_experiment_request(
        CenterPoint(lng=121.5, lat=31.3),
        origin_edge_id="west_south_sidewalk", network_path=_local_fixture(tmp_path))
    result = local_experiment.build_local_experiment_result(_fake_result(), metadata)
    assert result["mode"] == "local_experiment"
    assert result["thresholdSeconds"] == 180
    assert result["notForMainReport"] is True
    assert "isochrone" not in result and "blindZones" not in result
    assert "uncoveredLengthRatio" not in json.dumps(result)
    assert {feature["properties"]["classification"] for feature in
            result["categorySegments"]["features"]} == {"covered", "unknown"}
    assert "LOCAL_REACHABILITY_MAY_BE_TRUNCATED" in result["warnings"]
    assert "SYNTHETIC_NETWORK_NOT_REAL_WORLD" in result["warnings"]


def test_unmarked_synthetic_boundaries_stay_incomplete_and_invalid_ids_fail(tmp_path: Path) -> None:
    path = _local_fixture(tmp_path)
    fixture = json.loads(path.read_text(encoding="utf-8"))
    fixture.pop("localExperiment")
    path.write_text(json.dumps(fixture), encoding="utf-8")
    payload, _ = local_experiment.load_local_experiment_request(
        CenterPoint(lng=121.5, lat=31.3), network_path=path)
    assert payload["localExperiment"] == {
        "boundaryNodeIds": [], "topologyStatus": "incomplete"}
    fixture["localExperiment"] = {
        "boundaryNodeIds": ["not-a-node"], "topologyStatus": "verified"}
    path.write_text(json.dumps(fixture), encoding="utf-8")
    with pytest.raises(network.UnsupportedAreaError, match="unknown node"):
        local_experiment.load_local_experiment_request(
            CenterPoint(lng=121.5, lat=31.3), network_path=path)


def test_default_model_is_reused_without_supplementing_any_data() -> None:
    path = REPO_ROOT / "data/networks/synthetic-preview.json"
    source = json.loads(path.read_text(encoding="utf-8"))
    center = CenterPoint(lng=121.504429458, lat=31.331174183, coordType="wgs84ll")
    payload, metadata = local_experiment.load_local_experiment_request(
        center, "w:154811345:2:0", network_path=path)
    assert payload["nodes"] == source["nodes"]
    assert payload["edges"] == source["edges"]
    assert payload["facilities"] == source["facilities"] == []
    assert payload["serviceCategories"] == source["serviceCategories"] == []
    assert payload["localExperiment"] == {
        "boundaryNodeIds": [], "topologyStatus": "incomplete"}
    report = local_experiment.build_local_experiment_result({
        "reachableEdges": [{"edgeId": "w:154811345:2:0", "kind": "shared_way",
                            "pathMeters": [[-54.25, -203.25], [-54, -204]]}],
        "localGrayZones": [], "diagnostics": {"warnings": []},
    }, metadata)
    assert report["metadata"]["coordType"] == "wgs84ll"
    assert report["metadata"]["networkFile"] == "synthetic-preview.json"
    assert report["metadata"]["grayZoneStatus"] == "data_insufficient"
    assert report["categorySegments"]["features"] == []
    assert "LOCAL_FACILITY_DATA_NOT_PROVIDED" in report["warnings"]
    assert "LOCAL_BOUNDARY_NOT_MARKED" in report["warnings"]


def test_local_api_is_separate_from_main_analysis(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _local_fixture(tmp_path)
    monkeypatch.setattr(local_experiment, "settings", replace(
        local_experiment.settings, local_experiment_network_path=path))

    async def fake_engine(payload: dict) -> dict:
        assert payload["thresholdSeconds"] == 180
        assert "localExperiment" in payload
        return _fake_result()

    monkeypatch.setattr(main, "run_engine", fake_engine)
    created = CLIENT.post("/api/v1/local-experiments", json={
        "center": {"lng": 121.5, "lat": 31.3, "coordType": "bd09ll"},
        "originEdgeId": "west_south_sidewalk",
    })
    assert created.status_code == 202
    experiment_id = created.json()["analysisId"]
    state = CLIENT.get(f"/api/v1/local-experiments/{experiment_id}").json()
    assert state["status"] == "completed"
    assert state["result"]["mode"] == "local_experiment"
    assert CLIENT.get(f"/api/v1/analyses/{experiment_id}").status_code == 404


def test_local_api_without_network_returns_unsupported(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_experiment, "settings", replace(
        local_experiment.settings,
        local_experiment_network_path=tmp_path / "missing.json"))
    response = CLIENT.post("/api/v1/local-experiments", json={
        "center": {"lng": 121.5, "lat": 31.3, "coordType": "bd09ll"},
    })
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "UNSUPPORTED_AREA"


def test_python_cpp_local_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    binary = Path(os.getenv("CPP_ENGINE_PATH", "cpp-engine/build/isochrone_engine"))
    if not binary.is_absolute():
        binary = REPO_ROOT / binary
    if not binary.is_file() and binary.with_suffix(".exe").is_file():
        binary = binary.with_suffix(".exe")
    if not binary.is_file():
        pytest.skip("C++ engine has not been built")

    fixture = REPO_ROOT / "contracts" / "engine-local-experiment.input.example.json"
    source = json.loads(fixture.read_text(encoding="utf-8"))
    center = CenterPoint(**source["demoCenterBd09"], coordType="bd09ll")
    payload, metadata = local_experiment.load_local_experiment_request(
        center, source["originEdgeId"], network_path=fixture)
    assert payload["originMeters"]["xMeters"] == pytest.approx(200, abs=0.01)
    monkeypatch.setattr(engine, "settings", replace(
        engine.settings, cpp_engine_path=binary))
    result = asyncio.run(engine.run_engine(payload))
    assert result["grayZones"] == []
    assert len(result["localGrayZones"]) == 2
    shopping = next(zone for zone in result["localGrayZones"]
                    if zone["category"] == "shopping")
    assert shopping["coveredEdges"]
    assert shopping["candidateUncoveredEdges"]
    assert shopping["unknownEdges"]
    report = local_experiment.build_local_experiment_result(result, metadata)
    assert {feature["properties"]["classification"] for feature in
            report["categorySegments"]["features"]} == {
                "covered", "candidate_uncovered", "unknown"}
    # This origin cannot reach x=600 within 180s, but outside facilities
    # can still influence the eastern part of its reachable streets.
    assert "LOCAL_REACHABILITY_MAY_BE_TRUNCATED" not in report["warnings"]
    monkeypatch.setattr(local_experiment, "settings", replace(
        local_experiment.settings, local_experiment_network_path=fixture))
    accepted = CLIENT.post("/api/v1/local-experiments", json={
        "center": center.model_dump(), "originEdgeId": source["originEdgeId"]})
    assert accepted.status_code == 202
    completed = CLIENT.get(
        f"/api/v1/local-experiments/{accepted.json()['analysisId']}").json()
    assert completed["status"] == "completed"
    assert completed["result"]["notForMainReport"] is True
    assert len(completed["result"]["categorySegments"]["features"]) > 0
    payload["originMeters"] = {"xMeters": 500, "yMeters": 0}
    payload["originEdgeId"] = "east_lane"
    near_cut = asyncio.run(engine.run_engine(payload))
    assert "LOCAL_REACHABILITY_MAY_BE_TRUNCATED" in near_cut["diagnostics"]["warnings"]
