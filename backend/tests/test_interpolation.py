import math

import pytest

from app.interpolation import (
    generate_grid,
    interpolate_duration,
    interpolate_duration_grid,
)


SAMPLES = [
    {
        "xMeters": 0.0,
        "yMeters": 0.0,
        "durationSeconds": 300.0,
    },
    {
        "xMeters": 200.0,
        "yMeters": 0.0,
        "durationSeconds": 700.0,
    },
]


# 验证命中采样点时直接返回原始耗时
def test_interpolate_duration_returns_exact_sample() -> None:
    result = interpolate_duration(
        SAMPLES,
        (0.0, 0.0),
    )

    assert result == 300.0


# 验证两个等距离样本会得到合理的加权结果
def test_interpolate_duration_uses_distance_weights() -> None:
    result = interpolate_duration(
        SAMPLES,
        (100.0, 0.0),
    )

    assert math.isclose(result, 500.0)


# 验证规则网格的点数和范围
def test_generate_grid() -> None:
    grid = generate_grid(
        (0.0, 200.0, 0.0, 200.0),
        step_meters=100.0,
    )

    assert len(grid) == 9
    assert (0.0, 0.0) in grid
    assert (200.0, 200.0) in grid


# 验证网格中的每个点都得到耗时
def test_interpolate_duration_grid() -> None:
    grid = interpolate_duration_grid(
        SAMPLES,
        (0.0, 200.0, 0.0, 100.0),
        step_meters=100.0,
    )

    assert len(grid) == 6
    assert all("durationSeconds" in point for point in grid)
    assert grid[0]["durationSeconds"] == 300.0


# 验证非法参数会被拒绝
def test_interpolation_rejects_invalid_input() -> None:
    with pytest.raises(ValueError, match="至少需要"):
        interpolate_duration([], (0.0, 0.0))

    with pytest.raises(ValueError, match="步长"):
        generate_grid(
            (0.0, 100.0, 0.0, 100.0),
            step_meters=0,
        )