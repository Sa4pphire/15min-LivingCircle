"""提供基于 JSON 文件的短期缓存。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any

from .settings import settings


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
    return directory / f"{key}.json"


# 读取未过期的缓存，缓存损坏或过期时返回 None
def get_cached_json(
    key: str,
    *,
    cache_dir: Path | None = None,
    ttl_hours: int | None = None,
    now: datetime | None = None,
) -> Any | None:
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

    ttl = settings.cache_ttl_hours if ttl_hours is None else ttl_hours
    if current_time - created_at > timedelta(hours=ttl):
        return None

    return payload


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