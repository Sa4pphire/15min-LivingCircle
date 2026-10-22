import asyncio
import json
import os
from time import perf_counter
from pathlib import Path
from typing import Any

from .schemas import EngineHealth
from .settings import settings
from .walking_types import normalize_graph


class EngineError(RuntimeError):
    pass


def _engine_path() -> Path:
    path = settings.cpp_engine_path
    windows_path = path.with_suffix(".exe")
    if os.name == "nt" and not path.is_file() and windows_path.is_file():
        return windows_path
    return path


async def _finish_cleanup(cleanup: asyncio.Task[None]) -> None:
    """Finish reaping even if shutdown cancels the caller more than once."""
    cancelled = False
    while True:
        try:
            await asyncio.shield(cleanup)
            break
        except asyncio.CancelledError:
            if cleanup.cancelled():
                raise
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError


async def _terminate_and_reap(
    process: asyncio.subprocess.Process,
    communication: asyncio.Task[tuple[bytes, bytes]] | None = None,
) -> None:
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass  # The child may exit between checking returncode and kill().
    try:
        if communication is None:
            await process.communicate()
        else:
            await communication
    except (Exception, asyncio.CancelledError):
        pass  # Preserve the caller's original failure after reaping the child.
    finally:
        await process.wait()


async def _start_process(*args: str, **kwargs: Any) -> asyncio.subprocess.Process:
    # Retain the startup task: cancellation can arrive after the OS creates the
    # child but before create_subprocess_exec returns its Process handle.
    startup = asyncio.create_task(asyncio.create_subprocess_exec(*args, **kwargs))
    try:
        return await asyncio.shield(startup)
    except asyncio.CancelledError:
        async def cleanup_startup() -> None:
            try:
                process = await startup
            except OSError:
                return
            await _terminate_and_reap(process)

        await _finish_cleanup(asyncio.create_task(cleanup_startup()))
        raise


async def _communicate(
    process: asyncio.subprocess.Process, input_data: bytes | None = None,
    *, timeout: float,
) -> tuple[bytes, bytes]:
    # Keep one pipe reader alive through timeout/cancellation, then drain it
    # after kill rather than starting a second communicate() on the same pipes.
    communication = asyncio.create_task(process.communicate(input_data))
    try:
        return await asyncio.wait_for(asyncio.shield(communication), timeout=timeout)
    except (Exception, asyncio.CancelledError):
        await _finish_cleanup(asyncio.create_task(
            _terminate_and_reap(process, communication)))
        raise


async def run_engine(payload: dict[str, Any]) -> dict[str, Any]:
    started = perf_counter()
    # Keep annotations in Python; compact only the v2 wire representation.
    # Meter coordinates retain their original precision.
    payload = normalize_graph(payload)
    edge_fields = {"id", "from", "to", "kind", "accessMode", "pathMeters", "streetBlockId",
                   "side", "sharedWayType", "widthMeters", "waitSeconds"}
    wire = {**payload, "edges": [
        {key: value for key, value in edge.items() if key in edge_fields}
        for edge in payload.get("edges", [])]}
    encoded = json.dumps(wire, ensure_ascii=False, allow_nan=False,
                         separators=(",", ":")).encode("utf-8")
    encoded_at = perf_counter()
    engine_path = _engine_path()
    if not engine_path.is_file():
        raise EngineError(f"C++ 引擎不可用：{engine_path}")

    try:
        process = await _start_process(
            str(engine_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise EngineError(f"无法启动 C++ 引擎：{exc}") from exc
    spawned_at = perf_counter()
    try:
        stdout, stderr = await _communicate(process, encoded, timeout=25)
    except TimeoutError as exc:
        raise EngineError("C++ 引擎计算超时") from exc
    received_at = perf_counter()

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
    finished_at = perf_counter()
    response["result"].setdefault("diagnostics", {})["invocation"] = {
        "encodeMs": round((encoded_at - started) * 1000, 3),
        "spawnMs": round((spawned_at - encoded_at) * 1000, 3),
        "roundTripMs": round((received_at - spawned_at) * 1000, 3),
        "decodeMs": round((finished_at - received_at) * 1000, 3),
        "totalMs": round((finished_at - started) * 1000, 3),
        "inputBytes": len(encoded), "outputBytes": len(stdout),
    }
    return response["result"]


async def check_engine() -> EngineHealth:
    engine_path = _engine_path()
    if not engine_path.exists():
        return EngineHealth(status="unavailable", detail=str(engine_path))

    try:
        process = await _start_process(
            str(engine_path),
            "--health",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await _communicate(process, timeout=3)
    except (OSError, TimeoutError) as exc:
        return EngineHealth(status="error", detail=str(exc))

    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        return EngineHealth(status="error", detail=detail or "non-zero exit")

    return EngineHealth(status="ok", detail=stdout.decode().strip())
