from app.sampling import (
    DEFAULT_RADII_METERS,
    DIRECTION_ANGLES,
    generate_sampling_points,
)


# 验证中心点加 48 个环形采样点
def test_generate_sampling_points_count() -> None:
    points = generate_sampling_points((121.513, 31.337))

    assert len(points) == 49
    assert points[0]["sampleIndex"] == 0
    assert points[0]["radiusMeters"] == 0.0


# 验证方向和半径的组合数量
def test_generate_sampling_points_has_all_directions_and_radii() -> None:
    points = generate_sampling_points((121.513, 31.337))
    ring_points = points[1:]

    observed_angles = {
        point["angleDegrees"]
        for point in ring_points
    }
    observed_radii = {
        point["radiusMeters"]
        for point in ring_points
    }

    assert observed_angles == set(DIRECTION_ANGLES)
    assert observed_radii == set(DEFAULT_RADII_METERS)
    assert len(ring_points) == len(DIRECTION_ANGLES) * len(
        DEFAULT_RADII_METERS
    )


# 验证采样序号和方向顺序稳定
def test_generate_sampling_points_order_is_stable() -> None:
    points = generate_sampling_points((121.513, 31.337))

    assert points[1]["sampleIndex"] == 1
    assert points[1]["angleDegrees"] == 0
    assert points[1]["radiusMeters"] == 300.0

    assert points[5]["angleDegrees"] == 30
    assert points[5]["radiusMeters"] == 300.0