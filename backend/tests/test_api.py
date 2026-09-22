from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_endpoint() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"ok", "degraded"}
    assert payload["engine"]["status"] in {"ok", "unavailable", "error"}


def test_analysis_round_trip() -> None:
    create_response = client.post(
        "/api/v1/analyses",
        json={
            "center": {"lng": 121.5, "lat": 31.3, "coordType": "bd09ll"},
            "minutes": 15,
        },
    )
    assert create_response.status_code == 202
    analysis_id = create_response.json()["analysisId"]

    get_response = client.get(f"/api/v1/analyses/{analysis_id}")
    assert get_response.status_code == 200
    assert get_response.json()["analysisId"] == analysis_id
