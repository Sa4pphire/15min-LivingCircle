import math

import pytest

from app.sampled_geometry import (
    build_blind_zone_grid,
    isochrone_search_radius_meters,
    local_point_to_bd09,
    multipolygon_to_bd09,
    point_in_isochrone,
)


def test_build_blind_zone_grid_marks_missing_service_categories() -> None:
    geometry = {
        "type": "MultiPolygon",
        "coordinates": [[[
            [-100.0, -100.0], [100.0, -100.0], [100.0, 100.0],
            [-100.0, 100.0], [-100.0, -100.0],
        ]]],
    }
    result = build_blind_zone_grid(geometry, [], (121.5, 31.3),
                                   cell_size_meters=100.0,
                                   service_radius_meters=100.0)

    assert result["type"] == "FeatureCollection"
    assert result["features"]
    assert set(result["features"][0]["properties"]["missingCategories"]) == {
        "market", "pharmacy", "primary_school",
    }
    assert result["features"][0]["properties"]["resolutionMeters"] == 100.0


def test_incomplete_poi_inventory_does_not_claim_blind_area() -> None:
    geometry = {
        "type": "MultiPolygon",
        "coordinates": [[[[-50.0, -50.0], [50.0, -50.0], [50.0, 50.0],
                           [-50.0, 50.0], [-50.0, -50.0]]]],
    }
    result = build_blind_zone_grid(
        geometry, [], (121.5, 31.3), inventory_complete=False,
    )
    assert result["features"] == []
    assert result["properties"]["status"] == "unknown"
    assert result["properties"]["resolutionMeters"] == 10.0


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


def test_isochrone_search_radius_covers_local_geometry() -> None:
    geometry = {
        "type": "MultiPolygon",
        "coordinates": [[[
            [-500.0, 0.0], [0.0, 500.0], [500.0, 0.0], [-500.0, 0.0],
        ]]],
    }

    assert isochrone_search_radius_meters(geometry) >= 530


def test_point_in_isochrone_rejects_outside_and_hole() -> None:
    geometry = {
        "type": "MultiPolygon",
        "coordinates": [[
            [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 0.0]],
            [[4.0, 2.0], [8.0, 2.0], [8.0, 6.0], [4.0, 2.0]],
        ]],
    }

    assert point_in_isochrone((2.0, 2.0), geometry) is True
    assert point_in_isochrone((5.0, 3.0), geometry) is False
    assert point_in_isochrone((20.0, 2.0), geometry) is False
