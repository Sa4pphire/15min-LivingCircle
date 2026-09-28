import asyncio
from typing import Any

import pytest

from app.baidu.errors import BaiduTransientError
from app.poi import POI_CATEGORIES, collect_pois


class FakePoiClient:
    def __init__(
        self,
        fail_query: str | None = None,
        always_full: bool = False,
    ) -> None:
        self.fail_query = fail_query
        self.always_full = always_full
        self.calls: list[tuple[str, int, int]] = []

    # 模拟百度分页查询
    async def search_pois(
        self,
        query: str,
        center: tuple[float, float],
        radius_meters: int = 1000,
        *,
        page_num: int = 0,
        page_size: int = 20,
        coord_type: str = "bd09ll",
    ) -> list[dict[str, Any]]:
        self.calls.append((query, page_num, page_size))

        assert center == (121.513, 31.337)
        assert radius_meters == 1000
        assert coord_type == "bd09ll"

        if query == self.fail_query:
            raise BaiduTransientError("模拟网络失败")

        def item(uid: str) -> dict[str, Any]:
            return {
                "uid": uid,
                "name": "测试设施",
                "address": "测试地址",
                "lng": 121.514,
                "lat": 31.338,
            }

        if self.always_full:
            return [
                item(f"{query}-{page_num}-{index}")
                for index in range(page_size)
            ]

        if page_size == 2 and page_num == 0:
            return [
                item(f"{query}-1"),
                item(f"{query}-2"),
            ]

        if page_size == 2 and page_num == 1:
            return [
                item(f"{query}-2"),
                item(f"{query}-3"),
           ]

        if page_size == 2 and page_num >= 2:
            return []


        return [
            item(f"{query}-1"),
            item(f"{query}-1"),
            item(f"{query}-2"),
    ]


# 验证三类设施查询、UID 去重和 GeoJSON 坐标顺序
def test_collect_pois_categories_and_deduplication() -> None:
    client = FakePoiClient()

    result = asyncio.run(
        collect_pois(client, (121.513, 31.337))
    )

    assert [call[0] for call in client.calls] == [
        *POI_CATEGORIES.values(),
    ]

    features = result["facilities"]["features"]
    assert len(features) == 6
    assert result["partial"] is False

    for category in POI_CATEGORIES:
        category_features = [
            feature
            for feature in features
            if feature["properties"]["category"] == category
        ]
        assert len(category_features) == 2
        assert result["categoryStatus"][category] == "complete"

    assert features[0]["geometry"]["coordinates"] == [
        121.514,
        31.338,
    ]


# 验证跨页查询和跨页 UID 去重
def test_collect_pois_paginates_and_deduplicates_across_pages() -> None:
    client = FakePoiClient()

    result = asyncio.run(
        collect_pois(
            client,
            (121.513, 31.337),
            page_size=2,
            max_pages=3,
        )
    )

    features = result["facilities"]["features"]

    # 每个类别最终是 uid 1、2、3 三个设施
    assert len(features) == 9
    assert result["partial"] is False

    for category in POI_CATEGORIES:
        category_calls = [
            call for call in client.calls if call[0] == POI_CATEGORIES[category]
        ]
        assert [call[1] for call in category_calls] == [0, 1, 2]
        assert result["categoryStatus"][category] == "complete"


# 验证达到最大页数时必须标记 partial
def test_collect_pois_marks_max_pages_as_partial() -> None:
    client = FakePoiClient(always_full=True)

    result = asyncio.run(
        collect_pois(
            client,
            (121.513, 31.337),
            page_size=2,
            max_pages=2,
        )
    )

    assert result["partial"] is True
    assert set(result["categoryStatus"].values()) == {"max_pages"}
    assert any(
        warning.startswith("POI_MAX_PAGES_REACHED:")
        for warning in result["warnings"]
    )


# 验证某个类别失败时，其他类别仍然保留
def test_collect_pois_preserves_successful_categories() -> None:
    client = FakePoiClient(fail_query="药店")

    result = asyncio.run(
        collect_pois(client, (121.513, 31.337))
    )

    assert len(result["facilities"]["features"]) == 4
    assert result["partial"] is True
    assert result["categoryStatus"]["pharmacy"] == "failed"
    assert result["categoryStatus"]["market"] == "complete"
    assert any(
        warning.startswith("POI_SEARCH_FAILED:pharmacy:")
        for warning in result["warnings"]
    )


# 验证无效半径和无效分页参数不会触发查询
def test_collect_pois_rejects_invalid_parameters() -> None:
    client = FakePoiClient()

    with pytest.raises(ValueError, match="半径"):
        asyncio.run(
            collect_pois(
                client,
                (121.513, 31.337),
                radius_meters=0,
            )
        )

    with pytest.raises(ValueError, match="分页"):
        asyncio.run(
            collect_pois(
                client,
                (121.513, 31.337),
                page_size=0,
            )
        )

    assert client.calls == []
