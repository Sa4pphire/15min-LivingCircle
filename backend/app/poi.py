"""检索生活圈设施，并转换成地图可用的 GeoJSON 点。"""

from typing import Any, Protocol

from .baidu.errors import BaiduApiError


# 类别 ID 用于程序处理，中文关键词用于百度检索
POI_CATEGORIES = {
    "market": "菜市场",
    "pharmacy": "药店",
    "primary_school": "小学",
}


class PoiClient(Protocol):
    # 约定百度客户端需要提供的单页查询能力
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
        ...


# 分页查询一个类别，并在所有页之间按 UID 去重
async def _collect_category_pages(
    client: PoiClient,
    query: str,
    center: tuple[float, float],
    radius_meters: int,
    *,
    page_size: int,
    max_pages: int,
) -> tuple[list[dict[str, Any]], bool]:
    items: list[dict[str, Any]] = []
    seen_uids: set[str] = set()
    reached_end = False

    for page_num in range(max_pages):
        page_items = await client.search_pois(
            query=query,
            center=center,
            radius_meters=radius_meters,
            page_num=page_num,
            page_size=page_size,
            coord_type="bd09ll",
        )

        for item in page_items:
            uid = str(item["uid"])
            if uid in seen_uids:
                continue

            seen_uids.add(uid)
            items.append(item)

        # 少于一整页，说明百度已经返回最后一页
        if len(page_items) < page_size:
            reached_end = True
            break

    return items, reached_end


# 查询三类设施，分页去重，并返回数据完整性提示
async def collect_pois(
    client: PoiClient,
    center: tuple[float, float],
    radius_meters: int = 1000,
    *,
    page_size: int = 20,
    max_pages: int = 5,
) -> dict[str, Any]:
    if radius_meters <= 0:
        raise ValueError("POI 搜索半径必须大于 0")
    if page_size <= 0 or max_pages <= 0:
        raise ValueError("分页参数必须大于 0")

    features: list[dict[str, Any]] = []
    warnings: list[str] = []
    category_status: dict[str, str] = {}
    partial = False

    for category, query in POI_CATEGORIES.items():
        try:
            items, reached_end = await _collect_category_pages(
                client,
                query,
                center,
                radius_meters,
                page_size=page_size,
                max_pages=max_pages,
            )
        except BaiduApiError as exc:
            # 单类失败不影响其他类别，但结果必须标记为不完整
            category_status[category] = "failed"
            warnings.append(
                f"POI_SEARCH_FAILED:{category}:{type(exc).__name__}"
            )
            partial = True
            continue

        if reached_end:
            category_status[category] = "complete"
        else:
            category_status[category] = "max_pages"
            warnings.append(f"POI_MAX_PAGES_REACHED:{category}")
            partial = True

        for item in items:
            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [
                            item["lng"],
                            item["lat"],
                        ],
                    },
                    "properties": {
                        "uid": item["uid"],
                        "name": item["name"],
                        "address": item["address"],
                        "category": category,
                    },
                }
            )

    return {
        "facilities": {
            "type": "FeatureCollection",
            "features": features,
        },
        "categoryStatus": category_status,
        "warnings": warnings,
        "partial": partial,
        "coordType": "bd09ll",
    }