import math

import pytest

from app.sampled_geometry import (
    local_point_to_bd09,
    multipolygon_to_bd09,
)


# 验证中心点的局部坐标仍然是原始 BD-09 中心点
def test_local_point_to_bd09_keeps_origin() -> None:
    result = local_point_to_bd09(
        (0.0, 0.0),
        (121.513, 31.337),
    )

    assert result == [121.513, 31.337]


# 验证一公里偏移后仍然是合理的经纬度
def test_local_point_to_bd09_converts_meter_offset() -> None:
    result = local_point_to_bd09(
        (1000.0, 1000.0),
        (121.513, 31.337),
    )

    assert result[0] > 121.513
    assert result[1] > 31.337
    assert math.isclose(
        result[1] - 31.337,
        1000.0 / 111_320.0,
    )


# 验证 MultiPolygon 的所有环都会被转换
def test_multipolygon_to_bd09() -> None:
    geometry = {
        "type": "MultiPolygon",
        "coordinates": [
            [
                [
                    [0.0, 0.0],
                    [100.0, 0.0],
                    [100.0, 100.0],
                    [0.0, 0.0],
                ]
            ]
        ],
    }

    result = multipolygon_to_bd09(
        geometry,
        (121.513, 31.337),
    )

    assert result["type"] == "MultiPolygon"
    assert result["coordinates"][0][0][0] == [
        121.513,
        31.337,
    ]
    assert result["coordinates"][0][0][-1] == [
        121.513,
        31.337,
    ]


# 验证非法几何会被拒绝
def test_multipolygon_to_bd09_rejects_invalid_geometry() -> None:
    with pytest.raises(ValueError, match="MultiPolygon"):
        multipolygon_to_bd09(
            {"type": "Polygon", "coordinates": []},
            (121.513, 31.337),
        )