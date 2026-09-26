"""组织采样点生成、百度 RouteMatrix 请求和结果合并。"""

from typing import Protocol

from .sampling import (
    build_route_matrix_inputs,
    generate_sampling_points,
    merge_route_results,
)


class RouteMatrixClient(Protocol):
    async def route_matrix(
        self,
        origin: tuple[float, float],
        destinations: list[tuple[float, float]],
    ) -> list[dict[str, float]]:
        ...


# 生成 48 个目的地、调用一次 RouteMatrix 并合并结果
async def collect_route_samples(
    client: RouteMatrixClient,
    center: tuple[float, float],
) -> list[dict[str, float | int]]:
    samples = generate_sampling_points(center)
    origin, destinations = build_route_matrix_inputs(samples)

    route_results = await client.route_matrix(
        origin=origin,
        destinations=destinations,
    )

    return merge_route_results(samples, route_results)