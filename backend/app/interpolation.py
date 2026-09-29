"""根据离散步行耗时样本估计局部网格耗时。"""

import math
from typing import Any


# 使用距离反比权重估计一个位置的步行耗时
def interpolate_duration(
    samples: list[dict[str, float | int]],
    point: tuple[float, float],
    *,
    power: float = 2.0,
) -> float:
    if not samples:
        raise ValueError("至少需要一个耗时样本")
    if power <= 0:
        raise ValueError("IDW 幂次必须大于 0")

    target_x, target_y = point
    weighted_duration = 0.0
    total_weight = 0.0

    for sample in samples:
        try:
            sample_x = float(sample["xMeters"])
            sample_y = float(sample["yMeters"])
            duration = float(sample["durationSeconds"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("耗时样本缺少有效字段") from None

        if not all(
            math.isfinite(value)
            for value in (sample_x, sample_y, duration)
        ):
            raise ValueError("耗时样本不能包含非有限数值")
        if duration < 0:
            raise ValueError("耗时不能为负数")

        distance = math.hypot(
            target_x - sample_x,
            target_y - sample_y,
        )

        # 目标点正好落在采样点上时，直接使用采样值
        if distance <= 1e-9:
            return duration

        weight = 1.0 / (distance**power)
        weighted_duration += duration * weight
        total_weight += weight

    return weighted_duration / total_weight


# 在指定局部米制范围内生成规则网格
def generate_grid(
    bounds: tuple[float, float, float, float],
    *,
    step_meters: float = 100.0,
) -> list[tuple[float, float]]:
    min_x, max_x, min_y, max_y = bounds

    if min_x > max_x or min_y > max_y:
        raise ValueError("网格范围无效")
    if step_meters <= 0:
        raise ValueError("网格步长必须大于 0")

    points: list[tuple[float, float]] = []
    x = min_x

    while x <= max_x + 1e-9:
        y = min_y

        while y <= max_y + 1e-9:
            points.append((x, y))
            y += step_meters

        x += step_meters

    return points


# 为网格中的每个点计算 IDW 估计耗时
def interpolate_duration_grid(
    samples: list[dict[str, float | int]],
    bounds: tuple[float, float, float, float],
    *,
    step_meters: float = 100.0,
    power: float = 2.0,
) -> list[dict[str, float]]:
    return [
        {
            "xMeters": x,
            "yMeters": y,
            "durationSeconds": interpolate_duration(
                samples,
                (x, y),
                power=power,
            ),
        }
        for x, y in generate_grid(
            bounds,
            step_meters=step_meters,
        )
    ]