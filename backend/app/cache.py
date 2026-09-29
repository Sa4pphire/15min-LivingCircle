"""提供基于 JSON 文件的短期缓存。"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any

from .settings import REPO_ROOT, settings
from .poi_cache import PoiCache


# 根据接口名称和规范化参数生成稳定缓存键
def make_cache_key(namespace: str, payload: Any) -> str:
    normalized = json.dumps(
        {
            "namespace": namespace,
            "payload": payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _cache_path(
    key: str,
    cache_dir: Path | None,
) -> Path:
    directory = Path(cache_dir or settings.analysis_cache_dir)
    if not directory.is_absolute():
        directory = REPO_ROOT / directory
    return directory / f"{key}.json"


# 读取未过期的缓存，缓存损坏或过期时返回 None
def get_cached_json(
    key: str,
    *,
    cache_dir: Path | None = None,
    ttl_hours: int | None = None,
    now: datetime | None = None,
) -> Any | None:
    entry = _get_cached_entry(key, cache_dir=cache_dir,
                              max_age_seconds=(settings.cache_ttl_hours if ttl_hours is None else ttl_hours) * 3600,
                              now=now)
    return entry[0] if entry is not None else None


def _get_cached_entry(key: str, *, cache_dir: Path | None,
                      max_age_seconds: float, now: datetime | None = None):
    path = _cache_path(key, cache_dir)

    if not path.is_file():
        return None

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        created_at = datetime.fromisoformat(document["createdAt"])
        payload = document["payload"]
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None

    current_time = now or datetime.now(timezone.utc)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)

    age = (current_time - created_at).total_seconds()
    if not 0 <= age <= max_age_seconds:
        return None

    return payload, created_at.timestamp()


# 使用临时文件替换正式文件，避免写入中断产生半个 JSON
def save_cached_json(
    key: str,
    payload: Any,
    *,
    cache_dir: Path | None = None,
    now: datetime | None = None,
) -> None:
    path = _cache_path(key, cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    created_at = now or datetime.now(timezone.utc)
    document = {
        "createdAt": created_at.astimezone(timezone.utc).isoformat(),
        "payload": payload,
    }
    content = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    )

    temporary_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f"{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_file.write(content)
            temporary_path = Path(temporary_file.name)

        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


class SharedBaiduCache(PoiCache):
    """One JSON response store for all BaiduClient callers.

    Keep the collaborator's key/envelope and atomic file replacement. SQLite
    only coordinates leases/cooldowns; normalized v1 entries are read-only
    migration sources, not a second active POI response cache.
    """

    def __init__(self, cache_dir: Path, coordination_path: Path | None = None,
                 ttl_seconds: float | None = None, stale_seconds: float | None = None):
        self.cache_dir = Path(cache_dir)
        if not self.cache_dir.is_absolute():
            self.cache_dir = REPO_ROOT / self.cache_dir
        path = Path(coordination_path or self.cache_dir / "baidu-pois.sqlite3")
        if not path.is_absolute():
            path = REPO_ROOT / path
        super().__init__(path,
                         settings.cache_ttl_hours * 3600 if ttl_seconds is None else ttl_seconds,
                         settings.poi_cache_stale_hours * 3600 if stale_seconds is None else stale_seconds)

    @staticmethod
    def key(parameters: dict) -> str:
        provider = parameters["provider"].rstrip("/")
        namespace = f"baidu:{parameters['path']}"
        if provider != "https://api.map.baidu.com":
            namespace = f"baidu:{provider}:{parameters['path']}"
        return make_cache_key(namespace, parameters["params"])

    def _read(self, key: str, max_age: float):
        entry = _get_cached_entry(key, cache_dir=self.cache_dir, max_age_seconds=max_age)
        if (entry and isinstance(entry[0], dict) and
                type(entry[0].get("status")) is int and entry[0]["status"] == 0):
            # Successful empty Place responses are reusable, but less stable
            # than positive inventories. Never cache an API failure as empty.
            if (entry[0].get("results") == [] and
                    (datetime.now(timezone.utc).timestamp() - entry[1]) >
                    settings.poi_empty_cache_ttl_hours * 3600):
                return None
            return entry
        return None

    def _write(self, key: str, value, fetched: float):
        save_cached_json(key, value, cache_dir=self.cache_dir,
                         now=datetime.fromtimestamp(fetched, timezone.utc))

    def migrate_legacy(self, parameters: dict, legacy_parameters: dict, transform,
                       ttl_seconds: float):
        key = self.key(parameters)
        if self._read(key, ttl_seconds) is not None:
            return
        legacy = PoiCache._read(self, PoiCache.key(legacy_parameters), ttl_seconds)
        if legacy is None:
            return
        try:
            value = transform(legacy[0])
            if not isinstance(value, dict) or value.get("status") != 0:
                return
            # Preserve original freshness, rather than refreshing old data by copying.
            self._write(key, value, legacy[1])
        except (KeyError, TypeError, ValueError):
            return
