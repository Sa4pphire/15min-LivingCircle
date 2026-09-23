import asyncio
import json
import os
from pathlib import Path
from typing import Any

from .schemas import EngineHealth
from .settings import settings


class EngineError(RuntimeError):
    pass


def _engine_path() -> Path:
    path = settings.cpp_engine_path
    windows_path = path.with_suffix(".exe")
    if os.name == "nt" and not path.is_file() and windows_path.is_file():
        return windows_path
    return path


async def run_engine(payload: dict[str, Any]) -> dict[str, Any]:
    engine_path = _engine_path()
    if not engine_path.is_file():
        raise EngineError(f"C++ 引擎不可用：{engine_path}")

    try:
        process = await asyncio.create_subprocess_exec(
            str(engine_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise EngineError(f"无法启动 C++ 引擎：{exc}") from exc
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(encoded), timeout=25)
    except TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise EngineError("C++ 引擎计算超时") from exc

    try:
        response = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        detail = stderr.decode("utf-8", errors="replace").strip()[:300]
        raise EngineError(f"C++ 引擎返回了无效 JSON：{detail}") from exc
    if not isinstance(response, dict) or response.get("schemaVersion") != 2:
        raise EngineError("C++ 引擎响应版本不匹配")
    if process.returncode != 0 or response.get("success") is not True:
        error = response.get("error") or {}
        if not isinstance(error, dict):
            error = {}
        raise EngineError(f"{error.get('code', 'ENGINE_ERROR')}: "
                          f"{error.get('message', 'C++ 引擎计算失败')}")
    if not isinstance(response.get("result"), dict):
        raise EngineError("C++ 引擎缺少结果对象")
    return response["result"]


async def check_engine() -> EngineHealth:
    engine_path = _engine_path()
    if not engine_path.exists():
        return EngineHealth(status="unavailable", detail=str(engine_path))

    try:
        process = await asyncio.create_subprocess_exec(
            str(engine_path),
            "--health",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=3)
    except (OSError, TimeoutError) as exc:
        return EngineHealth(status="error", detail=str(exc))

    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        return EngineHealth(status="error", detail=detail or "non-zero exit")

    return EngineHealth(status="ok", detail=stdout.decode().strip())
