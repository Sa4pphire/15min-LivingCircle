import pytest

from app.contour import extract_isoline


def _grid_with_inner_reachable_point() -> list[dict[str, float]]:
    return [
        {"xMeters": 0.0, "yMeters": 0.0, "durationSeconds": 1000.0},
        {"xMeters": 100.0, "yMeters": 0.0, "durationSeconds": 1000.0},
        {"xMeters": 200.0, "yMeters": 0.0, "durationSeconds": 1000.0},
        {"xMeters": 0.0, "yMeters": 100.0, "durationSeconds": 1000.0},
        {"xMeters": 100.0, "yMeters": 100.0, "durationSeconds": 500.0},
        {"xMeters": 200.0, "yMeters": 100.0, "durationSeconds": 1000.0},
        {"xMeters": 0.0, "yMeters": 200.0, "durationSeconds": 1000.0},
        {"xMeters": 100.0, "yMeters": 200.0, "durationSeconds": 1000.0},
        {"xMeters": 200.0, "yMeters": 200.0, "durationSeconds": 1000.0},
    ]


# 验证内部可达区域能够生成闭合 MultiPolygon
def test_extract_isoline_returns_closed_ring() -> None:
    geometry = extract_isoline(
        _grid_with_inner_reachable_point(),
        threshold_seconds=900.0,
    )

    assert geometry["type"] == "MultiPolygon"
    assert len(geometry["coordinates"]) == 1

    ring = geometry["coordinates"][0][0]
    assert len(ring) >= 4
    assert ring[0] == ring[-1]

    for x, y in ring:
        assert 0.0 <= x <= 200.0
        assert 0.0 <= y <= 200.0


# 验证所有网格都超过阈值时不会生成等时圈
def test_extract_isoline_returns_empty_geometry_when_unreachable() -> None:
    grid = [
        {
            "xMeters": float(x),
            "yMeters": float(y),
            "durationSeconds": 1000.0,
        }
        for x in (0, 100, 200)
        for y in (0, 100, 200)
    ]

    geometry = extract_isoline(
        grid,
        threshold_seconds=900.0,
    )

    assert geometry == {
        "type": "MultiPolygon",
        "coordinates": [],
    }


# 验证非法网格会被拒绝
def test_extract_isoline_rejects_invalid_grid() -> None:
    with pytest.raises(ValueError, match="不能为空"):
        extract_isoline([])

    with pytest.raises(ValueError, match="至少需要"):
        extract_isoline(
            [
                {
                    "xMeters": 0.0,
                    "yMeters": 0.0,
                    "durationSeconds": 300.0,
                }
            ]
        )