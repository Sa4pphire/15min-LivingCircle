"""Bounded, cache-first POI discovery over stable geographic tiles.

Circle searches deliberately use radius_limit=false. With strict radius
filtering Baidu warns that total and short pages are unreliable; containment is
therefore decided separately on the actual C++ polygon, never on query ranking.
"""

import asyncio
from collections import deque
import math
import sqlite3

from .baidu.errors import BaiduApiError, BaiduAuthError, BaiduQuotaError


METERS_PER_DEGREE = 111_320.0


def plan_tiles(center, radius, config, bounds=None):
    size = max(250, min(2000, config.poi_tile_meters))
    if bounds is None:
        dy = radius / METERS_PER_DEGREE
        dx = dy / max(0.01, math.cos(math.radians(center.lat)))
        bounds = [center.lng - dx, center.lat - dy, center.lng + dx, center.lat + dy]
    west, south, east, north = bounds
    if (not all(math.isfinite(v) for v in bounds) or west > east or south > north or
            west < -180 or east > 180 or south <= -85 or north >= 85):
        raise ValueError("POI 检索范围无效或超出支持纬度")
    lat_step = size / METERS_PER_DEGREE
    tiles = []
    for row in range(math.floor(south / lat_step), math.floor(north / lat_step) + 1):
        lat = (row + 0.5) * lat_step
        lng_step = size / (METERS_PER_DEGREE * math.cos(math.radians(lat)))
        for column in range(math.floor(west / lng_step), math.floor(east / lng_step) + 1):
            lng = (column + 0.5) * lng_step
            tiles.append({"id": f"{size}:{row}:{column}",
                          "center": [round(lng, 9), round(lat, 9)],
                          "radiusMeters": math.ceil(size / math.sqrt(2)) + 20})
    tiles.sort(key=lambda tile: (
        ((tile["center"][0] - center.lng) * math.cos(math.radians(center.lat))) ** 2 +
        (tile["center"][1] - center.lat) ** 2, tile["id"]))
    count = len(tiles)
    maximum = max(1, min(256, config.poi_max_tiles))
    return tiles[:maximum], {"bounds": list(bounds), "tileMeters": size,
                             "plannedTileCount": count, "tileCount": min(count, maximum),
                             "tileLimitReached": count > maximum}


def _terminal(number, page):
    # Only single-keyword, non-strict circle queries use this rule. A short page
    # must not hide later pages if the provider reports a larger total.
    total = page["total"]
    if total is not None:
        return (number + 1) * 20 >= total and page["rawCount"] <= 20
    return page["rawCount"] < 20


async def search_tiles(client, registry, center, radius, config, *, bounds=None,
                       refresh=False, seed_records=(), cache_only=False):
    if cache_only and refresh:
        raise ValueError("cache-only POI searches cannot force a refresh")
    tiles, plan = plan_tiles(center, radius, config, bounds)
    pages = max(1, min(8, config.poi_max_pages))
    order = list(registry)
    records = {}

    def merge(item, category, keyword=None):
        record = records.get(item["uid"])
        if record is None:
            record = records[item["uid"]] = {**item, "category": category,
                                            "categories": [], "matchedKeywords": []}
        if category not in record["categories"]:
            record["categories"].append(category)
        if keyword and keyword not in record["matchedKeywords"]:
            record["matchedKeywords"].append(keyword)
        if "navigationPoint" in item:
            record.setdefault("navigationPoint", item["navigationPoint"])

    for item in seed_records:
        for category in item["categories"]:
            if category in registry:
                merge(item, category)

    # Round-robin keywords/categories within each tile. Counts never stop
    # synonyms; breadth-first page fetching prevents one dense query starving
    # every other category. All fresh pages are inspected before any HTTP call.
    queries = []
    for tile in tiles:
        for index in range(max(len(registry[key]["keywords"]) for key in order)):
            for key in order:
                if index < len(registry[key]["keywords"]):
                    queries.append({"tile": tile, "category": key,
                                    "keyword": registry[key]["keywords"][index],
                                    "data": {}, "cachedPages": 0, "error": None})

    async def read(query, number, cache_only=False):
        return await client.search_poi_page(
            query["keyword"], tuple(query["tile"]["center"]), query["tile"]["radiusMeters"],
            page_num=number, coord_type=center.coordType, radius_limit=False,
            cache_only=cache_only, refresh=refresh and not cache_only)

    if not refresh:
        for query in queries:
            await asyncio.sleep(0)
            for number in range(pages):
                try:
                    page = await read(query, number, True)
                except (BaiduApiError, OSError, TimeoutError, sqlite3.Error):
                    continue
                if page is not None:
                    query["data"][number] = page
                    query["cachedPages"] += 1

    def next_missing(query):
        for number in range(pages):
            page = query["data"].get(number)
            if page is None:
                return number
            if _terminal(number, page):
                return None
        return None

    queue = deque(query for query in queries if next_missing(query) is not None)
    http_started = client.cache_stats["apiRequests"]
    stale_started = client.cache_stats["stalePages"]
    client.limit_requests(max(0, config.poi_max_requests))
    failure_kinds = set()

    async def worker():
        while queue:
            if client.cache_stats["apiRequests"] - http_started >= config.poi_max_requests:
                break
            query = queue.popleft()
            number = next_missing(query)
            if number is None:
                continue
            try:
                query["data"][number] = await read(query, number)
            except (BaiduApiError, OSError, TimeoutError, sqlite3.Error) as exc:
                query["error"] = str(exc)
                if isinstance(exc, BaiduQuotaError):
                    failure_kinds.add("quota")
                    break  # Keep all cache hits, but stop this online worker.
                if isinstance(exc, BaiduAuthError):
                    failure_kinds.add("auth")
                    break
                continue
            if next_missing(query) is not None:
                queue.append(query)

    tasks = [] if cache_only else [asyncio.create_task(worker()) for _ in range(max(1, min(4, config.baidu_max_concurrency)))]
    timed_out = False
    try:
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=max(0, config.poi_budget_seconds))
            timed_out = bool(pending)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        completed = await asyncio.gather(*tasks, return_exceptions=True)
        client.limit_requests(None)

    outcomes = []
    for key in order:
        detail = []
        discarded = 0
        for query in (query for query in queries if query["category"] == key):
            data = query["data"]
            terminal = next((n for n, page in sorted(data.items()) if _terminal(n, page)), None)
            last = pages - 1 if terminal is None else terminal
            missing = [n for n in range(last + 1) if n not in data]
            capped = any(page["total"] is not None and page["total"] >= 150 for page in data.values())
            totals = [page["total"] for n, page in data.items() if n <= last and page["total"] is not None]
            count_mismatch = (terminal is not None and not missing and bool(totals) and
                              sum(page["rawCount"] for n, page in data.items() if n <= last) != max(totals))
            for page in data.values():
                discarded += page["discardedCount"]
                for item in page["items"]:
                    merge(item, key, query["keyword"])
            detail.append({"tileId": query["tile"]["id"], "keyword": query["keyword"],
                           "pages": len(data), "cachedPages": query["cachedPages"], "missingPageNumbers": missing,
                           "paginationComplete": terminal is not None and not missing and not capped and not count_mismatch,
                           "providerLimitReached": capped, "providerCountMismatch": count_mismatch,
                           "error": query["error"]})
        complete = not plan["tileLimitReached"] and all(q["paginationComplete"] for q in detail)
        error = next((q["error"] for q in detail if q["error"]), None)
        if error is None and (not complete or discarded):
            error = "检索受预算、分页、范围上限或无效点位影响，清单可能不完整"
        outcomes.append({"category": key, "paginationComplete": complete,
                         "pages": sum(q["pages"] for q in detail),
                         "cachedPages": sum(q["cachedPages"] for q in detail),
                         "plannedQueries": len(detail),
                         "completedQueries": sum(q["paginationComplete"] for q in detail),
                         "discardedCount": discarded, "error": error,
                         "providerLimitReached": any(q["providerLimitReached"] for q in detail),
                         "providerCountMismatch": any(q["providerCountMismatch"] for q in detail),
                         "queries": detail})
    failed_worker = any(isinstance(result, Exception) for result in completed)
    unavailable = not records and not any(outcome["pages"] for outcome in outcomes)
    partial = failed_worker or any(outcome["error"] for outcome in outcomes) or client.cache_stats["stalePages"] > stale_started
    for record in records.values():
        record["categories"].sort(key=order.index)
        record["category"] = record["categories"][0]
    return sorted(records.values(), key=lambda item: item["uid"]), {
        "provider": "baidu_place_v2", "status": "unavailable" if unavailable else "partial" if partial else "ready",
        "strategy": "tiled_circle_keywords", "coordinateSystem": "bd09ll",
        "queryInputCoordType": center.coordType, "queryBounds": plan["bounds"],
        "gridMeters": plan["tileMeters"], "tiles": tiles, **plan,
        "radiusLimit": False, "maxPagesPerQuery": pages,
        "maxApiRequests": config.poi_max_requests, "budgetSeconds": config.poi_budget_seconds,
        "maxRequestsPerSecond": config.baidu_max_qps,
        "quotaLimited": "quota" in failure_kinds, "authFailed": "auth" in failure_kinds,
        "error": next((q["error"] for q in queries if q["error"]), None),
        "timeBudgetReached": timed_out,
        "requestBudgetReached": (client.cache_stats["apiRequests"] - http_started >= config.poi_max_requests
                                 and any(next_missing(query) is not None for query in queries)),
        "plannedQueries": len(queries), "completedQueries": sum(o["completedQueries"] for o in outcomes),
        "cachedSeedCount": len(seed_records), "cacheBackend": "shared_baidu_json", "cacheFirst": not refresh,
        "cacheTtlHours": config.poi_cache_ttl_hours, "emptyCacheTtlHours": config.poi_empty_cache_ttl_hours,
        "cacheOnly": cache_only, "refreshRequired": cache_only and (partial or unavailable),
        "oldestFetchedAt": min(client.cache_fetched_times) if client.cache_fetched_times else None,
        "categories": outcomes, "inventoryVerified": False, **client.cache_stats,
    }
