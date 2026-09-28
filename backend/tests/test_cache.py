from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.cache import (
    get_cached_json,
    make_cache_key,
    save_cached_json,
)


# 验证相同参数顺序不同也能生成相同缓存键
def test_make_cache_key_is_stable() -> None:
    first = make_cache_key(
        "baidu-route-matrix",
        {"origin": [121.5, 31.3], "minutes": 15},
    )
    second = make_cache_key(
        "baidu-route-matrix",
        {"minutes": 15, "origin": [121.5, 31.3]},
    )

    assert first == second
    assert len(first) == 64


# 验证缓存可以写入并读取
def test_cache_round_trip(tmp_path: Path) -> None:
    key = make_cache_key(
        "baidu-poi",
        {"query": "药店", "center": [121.5, 31.3]},
    )
    created_at = datetime(
        2026,
        9,
        28,
        10,
        0,
        tzinfo=timezone.utc,
    )
    payload = {"status": 0, "results": [{"uid": "demo-1"}]}

    save_cached_json(
        key,
        payload,
        cache_dir=tmp_path,
        now=created_at,
    )

    assert get_cached_json(
        key,
        cache_dir=tmp_path,
        ttl_hours=24,
        now=created_at + timedelta(hours=1),
    ) == payload


# 验证超过 TTL 后不会继续使用旧缓存
def test_expired_cache_returns_none(tmp_path: Path) -> None:
    key = make_cache_key("baidu-poi", {"query": "小学"})
    created_at = datetime(
        2026,
        9,
        28,
        10,
        0,
        tzinfo=timezone.utc,
    )

    save_cached_json(
        key,
        {"status": 0},
        cache_dir=tmp_path,
        now=created_at,
    )

    assert get_cached_json(
        key,
        cache_dir=tmp_path,
        ttl_hours=24,
        now=created_at + timedelta(hours=24, minutes=1),
    ) is None


# 验证损坏的缓存不会让程序崩溃
def test_corrupted_cache_returns_none(tmp_path: Path) -> None:
    key = make_cache_key("baidu-route-matrix", {"origin": [1, 2]})
    (tmp_path / f"{key}.json").write_text(
        "{not valid json",
        encoding="utf-8",
    )

    assert get_cached_json(
        key,
        cache_dir=tmp_path,
        ttl_hours=24,
    ) is None
