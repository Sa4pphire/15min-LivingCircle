from dataclasses import replace
import os
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app import engine, network
from app.main import app


client = TestClient(app)


def test_health_endpoint() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"ok", "degraded"}
    assert payload["engine"]["status"] in {"ok", "unavailable", "error"}


def test_analysis_rejects_unannotated_area() -> None:
    create_response = client.post(
        "/api/v1/analyses",
        json={
            "center": {"lng": 0, "lat": 0, "coordType": "bd09ll"},
            "minutes": 15,
        },
    )
    assert create_response.status_code == 422
    assert create_response.json()["detail"]["code"] == "UNSUPPORTED_AREA"


def test_analysis_api_with_synthetic_network(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[2]
    binary = Path(os.getenv("CPP_ENGINE_PATH", "cpp-engine/build/isochrone_engine"))
    if not binary.is_absolute():
        binary = root / binary
    if not binary.is_file() and binary.with_suffix(".exe").is_file():
        binary = binary.with_suffix(".exe")
    if not binary.is_file():
        pytest.skip("C++ engine has not been built")
    monkeypatch.setattr(network, "settings", replace(
        network.settings,
        walking_network_path=root / "contracts" / "engine-input.example.json"))
    monkeypatch.setattr(engine, "settings", replace(
        engine.settings, cpp_engine_path=binary))
    created = client.post("/api/v1/analyses", json={
        "center": {"lng": 121.5, "lat": 31.3, "coordType": "bd09ll"},
        "originEdgeId": "west_south_sidewalk", "minutes": 15})
    assert created.status_code == 202
    state = client.get(f"/api/v1/analyses/{created.json()['analysisId']}").json()
    assert state["status"] == "completed"
    result = state["result"]
    assert result["metrics"]["reachableFacilityCount"] == 1
    assert len(result["metrics"]["grayZonesByCategory"]) == 2
    assert result["facilities"]["features"][0]["properties"]["bestEntranceId"]
