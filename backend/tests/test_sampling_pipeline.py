import pytest

from app.sampling import (
    build_route_matrix_inputs,
    generate_sampling_points,
    merge_route_results,
)


# 验证采样点能拆成一个起点和 48 个目的地点
def test_build_route_matrix_inputs() -> None:
    samples = generate_sampling_points((121.513, 31.337))

    origin, destinations = build_route_matrix_inputs(samples)

    assert origin == (121.513, 31.337)
    assert len(destinations) == 48
    assert destinations[0][0] > origin[0]


# 验证百度结果能按原顺序合并回采样点
def test_merge_route_results_preserves_order() -> None:
    samples = generate_sampling_points((121.513, 31.337))
    route_results = [
        {
            "distanceMeters": 100.0 + index,
            "durationSeconds": 200.0 + index,
        }
        for index in range(48)
    ]

    merged = merge_route_results(samples, route_results)

    assert len(merged) == 49
    assert merged[0]["durationSeconds"] == 0.0
    assert merged[1]["durationSeconds"] == 200.0
    assert merged[2]["durationSeconds"] == 201.0
    assert merged[48]["durationSeconds"] == 247.0


# 验证百度结果数量不一致时立即报错
def test_merge_route_results_rejects_wrong_count() -> None:
    samples = generate_sampling_points((121.513, 31.337))

    with pytest.raises(ValueError, match="结果数量"):
        merge_route_results(samples, [])