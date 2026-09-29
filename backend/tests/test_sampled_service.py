import asyncio

from app.schemas import CenterPoint
from app.sampled_service import run_sampled_analysis


class FakeBaiduClient:
    # 模拟坐标转换和 RouteMatrix
    async def convert_coordinates(
        self,
        points: list[tuple[float, float]],
        source_coord_type: str,
    ) -> list[tuple[float, float]]:
        assert source_coord_type == "wgs84ll"
        return [
            (
                points[0][0] + 0.0065,
                points[0][1] + 0.006,
            )
        ]

    async def route_matrix(
        self,
        origin: tuple[float, float],
        destinations: list[tuple[float, float]],
    ) -> list[dict[str, float]]:
        assert len(destinations) == 48

        return [
            {
                "distanceMeters": 300.0,
                "durationSeconds": 300.0,
            }
            for _ in destinations
        ]


# 验证 WGS-84 中心点会先转换成 BD-09
def test_run_sampled_analysis_normalizes_wgs84_center() -> None:
    async def run() -> dict:
        return await run_sampled_analysis(
            FakeBaiduClient(),
            CenterPoint(
                lng=121.500,
                lat=31.330,
                coordType="wgs84ll",
            ),
            grid_step_meters=200.0,
        )

    result = asyncio.run(run())

    assert result["requestedCenter"]["coordType"] == "wgs84ll"
    assert result["analysisCenter"]["coordType"] == "bd09ll"
    assert result["analysisCenter"]["lng"] == 121.5065
    assert result["analysisCenter"]["lat"] == 31.336
    assert len(result["durationSamples"]) == 49


# 验证 BD-09 中心点不会额外调用坐标转换
def test_run_sampled_analysis_keeps_bd09_center() -> None:
    async def run() -> dict:
        return await run_sampled_analysis(
            FakeBaiduClient(),
            CenterPoint(
                lng=121.513,
                lat=31.337,
                coordType="bd09ll",
            ),
            grid_step_meters=200.0,
        )

    result = asyncio.run(run())

    assert result["analysisCenter"] == {
        "lng": 121.513,
        "lat": 31.337,
        "coordType": "bd09ll",
    }