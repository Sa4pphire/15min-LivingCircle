import asyncio
from typing import Any

from app.sampled_analysis import build_sampled_isochrone


class FakeRouteMatrixClient:
    # 模拟百度 RouteMatrix 返回逐渐增加的步行耗时
    async def route_matrix(
        self,
        origin: tuple[float, float],
        destinations: list[tuple[float, float]],
    ) -> list[dict[str, float]]:
        assert origin == (121.513, 31.337)
        assert len(destinations) == 48

        return [
            {
                "distanceMeters": 300.0 + index,
                "durationSeconds": 300.0 + index * 20.0,
            }
            for index in range(48)
        ]


# 验证第一套算法能够串起采样、插值和轮廓提取
def test_build_sampled_isochrone() -> None:
    async def run() -> dict[str, Any]:
        return await build_sampled_isochrone(
            FakeRouteMatrixClient(),
            (121.513, 31.337),
            grid_step_meters=200.0,
        )

    result = asyncio.run(run())

    assert result["sourceMode"] == "baidu-sampled"
    assert result["approximate"] is True
    assert result["thresholdSeconds"] == 900.0
    assert len(result["durationSamples"]) == 49
    assert result["isochrone"]["type"] == "MultiPolygon"

# 验证组合结果已经转换为前端需要的 BD-09 MultiPolygon
def test_build_sampled_isochrone_returns_map_geometry() -> None:
    async def run() -> dict[str, Any]:
        return await build_sampled_isochrone(
            FakeRouteMatrixClient(),
            (121.513, 31.337),
            grid_step_meters=200.0,
        )

    result = asyncio.run(run())

    assert result["coordinateSystem"] == "bd09ll"
    assert result["approximate"] is True
    assert result["isochrone"]["type"] == "MultiPolygon"
    assert result["isochroneMeters"]["type"] == "MultiPolygon"