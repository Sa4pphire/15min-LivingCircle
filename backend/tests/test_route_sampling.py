import asyncio

from app.route_sampling import collect_route_samples


class FakeRouteMatrixClient:
    def __init__(self) -> None:
        self.origin: tuple[float, float] | None = None
        self.destinations: list[tuple[float, float]] = []

    # 模拟百度 RouteMatrix，不发送真实网络请求
    async def route_matrix(
        self,
        origin: tuple[float, float],
        destinations: list[tuple[float, float]],
    ) -> list[dict[str, float]]:
        self.origin = origin
        self.destinations = destinations

        return [
            {
                "distanceMeters": 100.0 + index,
                "durationSeconds": 200.0 + index,
            }
            for index in range(len(destinations))
        ]


# 验证完整采样流程只发送一次 RouteMatrix 请求
def test_collect_route_samples() -> None:
    client = FakeRouteMatrixClient()

    async def run() -> list[dict[str, float | int]]:
        return await collect_route_samples(
            client,
            (121.513, 31.337),
        )

    samples = asyncio.run(run())

    assert client.origin == (121.513, 31.337)
    assert len(client.destinations) == 48
    assert len(samples) == 49

    assert samples[0]["durationSeconds"] == 0.0
    assert samples[1]["durationSeconds"] == 200.0
    assert samples[48]["durationSeconds"] == 247.0