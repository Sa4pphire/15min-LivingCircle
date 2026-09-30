import asyncio

from app.route_sampling import (
    collect_route_samples,
    collect_sampling_routes,
    select_boundary_samples,
)


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


def test_select_boundary_samples_uses_farthest_reachable_point_per_direction() -> None:
    samples = [
        {"sampleIndex": 0, "angleDegrees": 0, "radiusMeters": 0, "durationSeconds": 0,
         "lng": 121.5, "lat": 31.3},
        {"sampleIndex": 1, "angleDegrees": 0, "radiusMeters": 300, "durationSeconds": 500,
         "lng": 121.501, "lat": 31.3},
        {"sampleIndex": 2, "angleDegrees": 0, "radiusMeters": 600, "durationSeconds": 950,
         "lng": 121.502, "lat": 31.3},
        {"sampleIndex": 3, "angleDegrees": 90, "radiusMeters": 300, "durationSeconds": 400,
         "lng": 121.5, "lat": 31.303},
    ]

    selected = select_boundary_samples(samples)

    assert [item["sampleIndex"] for item in selected] == [1, 3]


class FakeWalkingClient:
    def __init__(self) -> None:
        self.calls = []

    async def walking_route(self, origin, destination):
        self.calls.append((origin, destination))
        return {
            "distanceMeters": 800.0,
            "durationSeconds": 600.0,
            "segments": [[[origin[0], origin[1]], [destination[0], destination[1]]]],
        }


def test_collect_sampling_routes_returns_real_boundary_segments() -> None:
    client = FakeWalkingClient()
    samples = [
        {"sampleIndex": 1, "angleDegrees": 0, "radiusMeters": 300,
         "durationSeconds": 500, "lng": 121.501, "lat": 31.3},
        {"sampleIndex": 2, "angleDegrees": 90, "radiusMeters": 600,
         "durationSeconds": 700, "lng": 121.5, "lat": 31.306},
    ]

    async def run():
        return await collect_sampling_routes(client, (121.5, 31.3), samples)

    routes, failures = asyncio.run(run())

    assert failures == []
    assert len(routes) == 2
    assert [route["sampleIndex"] for route in routes] == [1, 2]
    assert len(client.calls) == 2


def test_collect_sampling_routes_drops_walking_routes_over_threshold() -> None:
    class SlowWalkingClient(FakeWalkingClient):
        async def walking_route(self, origin, destination):
            self.calls.append((origin, destination))
            return {
                "distanceMeters": 1400.0,
                "durationSeconds": 901.0,
                "segments": [[[origin[0], origin[1]], [destination[0], destination[1]]]],
            }

    client = SlowWalkingClient()
    samples = [{
        "sampleIndex": 1, "angleDegrees": 0, "radiusMeters": 600,
        "durationSeconds": 700, "lng": 121.501, "lat": 31.3,
    }]

    async def run():
        return await collect_sampling_routes(client, (121.5, 31.3), samples)

    routes, failures = asyncio.run(run())

    assert routes == []
    assert failures == [{
        "sampleIndex": 1,
        "error": "route exceeds threshold",
        "durationSeconds": 901.0,
    }]
