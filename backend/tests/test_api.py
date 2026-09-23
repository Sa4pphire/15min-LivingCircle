from fastapi.testclient import TestClient

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
