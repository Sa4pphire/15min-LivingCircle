"""Persistent request cache and cross-worker single-flight, without storing AKs."""

import asyncio
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4
from .baidu.errors import BaiduAuthError, BaiduQuotaError


class PoiCache:
    def __init__(self, path: Path, ttl_seconds: float, stale_seconds: float):
        self.path = path
        self.ttl = ttl_seconds
        self.stale = max(stale_seconds, ttl_seconds)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS entries (key TEXT PRIMARY KEY, fetched REAL NOT NULL, value TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS leases (key TEXT PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS failures (key TEXT PRIMARY KEY, expires REAL NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL)")
            db.execute("DELETE FROM entries WHERE fetched < ?", (time.time() - self.stale,))

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=3)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def key(parameters: dict) -> str:
        # Callers pass only public request parameters, never credentials.
        encoded = json.dumps(parameters, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(encoded.encode()).hexdigest()

    def _read(self, key: str, max_age: float):
        with self._connection() as db:
            row = db.execute("SELECT fetched, value FROM entries WHERE key=?", (key,)).fetchone()
        if row and 0 <= time.time() - row[0] <= max_age:
            try:
                return json.loads(row[1]), row[0]
            except (ValueError, TypeError):
                return None
        return None

    def _write(self, key: str, value, fetched: float):
        with self._connection() as db:
            db.execute("INSERT OR REPLACE INTO entries VALUES (?, ?, ?)",
                       (key, fetched, json.dumps(value, ensure_ascii=False, allow_nan=False)))

    async def get_or_fetch(self, parameters: dict, fetch, *, refresh: bool = False,
                           ttl_seconds: float | None = None):
        key = self.key(parameters)
        provider_key = str(parameters.get("provider", "baidu"))
        ttl = self.ttl if ttl_seconds is None else ttl_seconds
        started = time.time()
        cached = self._read(key, ttl)
        if cached and not refresh:
            return cached[0], "hit", cached[1]
        owner = uuid4().hex
        acquired = False
        while not acquired:
            with self._connection() as db:
                db.execute("DELETE FROM leases WHERE key=? AND expires < ?", (key, time.time()))
                cursor = db.execute("INSERT OR IGNORE INTO leases VALUES (?, ?, ?)",
                                    (key, owner, time.time() + 30))
                acquired = cursor.rowcount == 1
            if acquired:
                break
            await asyncio.sleep(0.1)
            cached = self._read(key, ttl)
            if cached and (not refresh or cached[1] >= started):
                return cached[0], "hit", cached[1]
            if time.time() - started > 30:
                raise TimeoutError("POI 缓存请求合并等待超时")
        try:
            # A worker may have filled the cache immediately before our lease.
            cached = self._read(key, ttl)
            if cached and (not refresh or cached[1] >= started):
                return cached[0], "hit", cached[1]
            with self._connection() as db:
                failure = db.execute("SELECT kind, message FROM failures WHERE key=? AND expires>?",
                                     (provider_key, time.time())).fetchone()
            if failure:
                stale = self._read(key, self.stale)
                if stale:
                    return stale[0], "stale", stale[1]
                error_type = BaiduQuotaError if failure[0] == "quota" else BaiduAuthError
                raise error_type(f"{failure[1]}（短时冷却中，未重复调用）")
            try:
                value = await fetch()
            except Exception as exc:
                if isinstance(exc, (BaiduAuthError, BaiduQuotaError)):
                    with self._connection() as db:
                        db.execute("INSERT OR REPLACE INTO failures VALUES (?, ?, ?, ?)",
                                   (provider_key, time.time() + 60,
                                    "quota" if isinstance(exc, BaiduQuotaError) else "auth", str(exc)))
                stale = self._read(key, self.stale)
                if stale:
                    return stale[0], "stale", stale[1]
                raise
            fetched = time.time()
            self._write(key, value, fetched)
            return value, "miss", fetched
        finally:
            with self._connection() as db:
                db.execute("DELETE FROM leases WHERE key=? AND owner=?", (key, owner))
