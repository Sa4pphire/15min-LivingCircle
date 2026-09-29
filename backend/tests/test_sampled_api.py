from fastapi.testclient import TestClient

from app import main


client = TestClient(main.app)


class FakeBaiduClient:
    # 模拟百度客户端，不发出真实网络请求
    async def aclose(self) -> None:
        return None


async def fake_build_sampled_result(
    client: FakeBaiduClient,
    center,
) -> dict:
    assert center.coordType == "bd09ll"

    return {
        "sourceMode": "baidu-sampled",
        "coordinateSystem": "bd09ll",
        "approximate": True,
        "thresholdSeconds": 900.0,
        "durationSamples": [],
        "isochrone": {
            "type": "MultiPolygon",
            "coordinates": [],
        },
    }


# 验证第一套算法能够创建任务并返回完成结果
def test_sampled_analysis_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "BaiduClient",
        FakeBaiduClient,
    )
    monkeypatch.setattr(
        main,
        "build_sampled_result",
        fake_build_sampled_result,
    )

    response = client.post(
        "/api/v1/sampled-analyses",
        json={
            "center": {
                "lng": 121.513,
                "lat": 31.337,
                "coordType": "bd09ll",
            },
            "minutes": 15,
        },
    )

    assert response.status_code == 202
    analysis_id = response.json()["analysisId"]

    state_response = client.get(
        f"/api/v1/sampled-analyses/{analysis_id}"
    )

    assert state_response.status_code == 200

    state = state_response.json()
    assert state["status"] == "completed"
    assert state["progress"]["stage"] == "completed"
    assert state["result"]["sourceMode"] == "baidu-sampled"
    assert state["result"]["approximate"] is True