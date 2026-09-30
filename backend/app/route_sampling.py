"""组织采样点生成、百度 RouteMatrix 请求和结果合并。"""

import math
from typing import Any, Protocol

from .baidu.errors import BaiduApiError

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


class WalkingRouteClient(Protocol):
    async def walking_route(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
    ) -> dict[str, Any]:
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


# 每个方向取“15 分钟内最远的采样点”，作为等时圈边界的道路终点。
def select_boundary_samples(
    samples: list[dict[str, float | int]],
    *,
    threshold_seconds: float = 900.0,
) -> list[dict[str, float | int]]:
    grouped: dict[float, list[dict[str, float | int]]] = {}
    for sample in samples:
        try:
            angle = float(sample["angleDegrees"])
            radius = float(sample["radiusMeters"])
            duration = float(sample["durationSeconds"])
        except (KeyError, TypeError, ValueError):
            continue
        if angle == 0 and radius == 0:
            continue
        if not all(math.isfinite(value) for value in (angle, radius, duration)):
            continue
        if duration <= threshold_seconds:
            grouped.setdefault(angle, []).append(sample)
    return [
        max(candidates, key=lambda item: float(item["radiusMeters"]))
        for angle, candidates in sorted(grouped.items())
    ]


# 沿真实步行道路绘制采样边界；失败的单点只记录，不影响等时圈结果。
async def collect_sampling_routes(
    client: WalkingRouteClient,
    origin: tuple[float, float],
    samples: list[dict[str, float | int]],
    *,
    threshold_seconds: float = 900.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    routes: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    walking_route = getattr(client, "walking_route", None)
    if not callable(walking_route):
        return routes, failures

    for sample in select_boundary_samples(samples, threshold_seconds=threshold_seconds):
        try:
            destination = (float(sample["lng"]), float(sample["lat"]))
            route = await walking_route(origin, destination)
            duration = float(route["durationSeconds"])
            if not math.isfinite(duration) or duration < 0:
                raise ValueError("步行路线耗时无效")
            if duration > threshold_seconds:
                failures.append({
                    "sampleIndex": sample.get("sampleIndex"),
                    "error": "route exceeds threshold",
                    "durationSeconds": duration,
                })
                continue
            for segment_index, points in enumerate(route["segments"]):
                if (not isinstance(points, list) or len(points) < 2 or
                        any(not isinstance(point, list) or len(point) < 2 or
                            not all(math.isfinite(float(value)) for value in point[:2])
                            for point in points)):
                    raise ValueError("步行路线折线无效")
                routes.append({
                    "id": f"sample:{sample.get('sampleIndex', segment_index)}:{segment_index}",
                    "category": "sample-boundary",
                    "sampleIndex": sample.get("sampleIndex"),
                    "angleDegrees": sample.get("angleDegrees"),
                    "points": points,
                    "distanceMeters": route["distanceMeters"],
                    "durationSeconds": duration,
                    "thresholdExceeded": False,
                })
        except (BaiduApiError, OSError, TimeoutError, KeyError, TypeError, ValueError) as exc:
            failures.append({
                "sampleIndex": sample.get("sampleIndex"),
                "error": str(exc),
            })
    return routes, failures
