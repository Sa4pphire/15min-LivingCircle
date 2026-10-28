import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import math
import os
from time import perf_counter
from pathlib import Path
from typing import Any, Callable, Iterator

from .schemas import EngineHealth
from .settings import settings
from .engine_cleanup import finish_cleanup as _finish_cleanup
from .engine_errors import EngineBusy, EngineError
from .engine_protocol import decode_engine_result, encode_engine_input
from .engine_transport import EngineTransport


_engine_wait_timeout: ContextVar[float] = ContextVar("engine_wait_timeout", default=0.0)
_engine_admitted_analysis: ContextVar[bool] = ContextVar("engine_admitted_analysis", default=False)


@contextmanager
def engine_wait_scope(wait_timeout: float = 5.0) -> Iterator[None]:
    """Only admitted analysis jobs may wait for a business call permit."""
    if not math.isfinite(wait_timeout) or wait_timeout < 0:
        raise ValueError("wait_timeout must be finite and non-negative")
    token = _engine_wait_timeout.set(wait_timeout)
    admitted_token = _engine_admitted_analysis.set(True)
    try:
        yield
    finally:
        _engine_admitted_analysis.reset(admitted_token)
        _engine_wait_timeout.reset(token)


def _engine_path() -> Path:
    path = settings.cpp_engine_path
    windows_path = path.with_suffix(".exe")
    if os.name == "nt" and not path.is_file() and windows_path.is_file():
        return windows_path
    return path


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


async def _run_engine(payload: dict[str, Any]) -> dict[str, Any]:
    started = perf_counter()
    encoded = encode_engine_input(payload)
    encoded_at = perf_counter()
    engine_path = _engine_path()
    if not engine_path.is_file():
        raise EngineError(f"C++ 引擎不可用：{engine_path}", code="ENGINE_UNAVAILABLE")

    try:
        process = await _start_process(
            str(engine_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise EngineError(f"无法启动 C++ 引擎：{exc}", code="ENGINE_START_FAILED") from exc
    spawned_at = perf_counter()
    try:
        stdout, stderr = await _communicate(process, encoded, timeout=25)
    except TimeoutError as exc:
        raise EngineError("C++ 引擎计算超时", code="ENGINE_TIMEOUT") from exc
    except OSError as exc:
        raise EngineError(f"C++ 引擎通信失败：{exc}", code="ENGINE_COMMUNICATION_ERROR") from exc
    received_at = perf_counter()

    result = decode_engine_result(stdout, returncode=process.returncode, stderr=stderr)
    diagnostics = result["diagnostics"]
    finished_at = perf_counter()
    diagnostics["invocation"] = {
        "encodeMs": round((encoded_at - started) * 1000, 3),
        "spawnMs": round((spawned_at - encoded_at) * 1000, 3),
        "roundTripMs": round((received_at - spawned_at) * 1000, 3),
        "decodeMs": round((finished_at - received_at) * 1000, 3),
        "totalMs": round((finished_at - started) * 1000, 3),
        "inputBytes": len(encoded), "outputBytes": len(stdout),
    }
    return result


async def _check_engine() -> EngineHealth:
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


class SubprocessTransport:
    """Keep the original process entry points and their cleanup behavior."""

    async def run_engine(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await _run_engine(payload)

    async def check_engine(self) -> EngineHealth:
        return await _check_engine()

    async def aclose(self) -> None:
        # Each invocation already owns and reaps its child.
        pass


@dataclass
class _HealthProbe:
    task: asyncio.Task[EngineHealth]
    waiters: int = 0
    retirement: asyncio.Task[None] | None = None


class EngineRuntime:
    """One event loop owns a transport, its permits and a shared health probe."""

    def __init__(self, capacity: int = 2, health_cache_ttl: float = 5.0,
                 clock: Callable[[], float] = perf_counter, *,
                 transport: EngineTransport | None = None) -> None:
        if (not isinstance(capacity, int) or capacity < 1
                or not math.isfinite(health_cache_ttl) or health_cache_ttl < 0):
            raise ValueError("capacity must be a positive integer and health_cache_ttl finite and non-negative")
        self._transport = transport if transport is not None else SubprocessTransport()
        self._permits = asyncio.Semaphore(capacity)
        self._health_cache_ttl = health_cache_ttl
        self._clock = clock
        self._calls: set[asyncio.Task[Any]] = set()
        self._health_probe: _HealthProbe | None = None
        self._health_cache: tuple[float, EngineHealth] | None = None
        self._idle = asyncio.Event()
        self._idle.set()
        self._draining = False
        self._closing = False
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    @property
    def closed(self) -> bool:
        return self._closed

    def begin_drain(self) -> None:
        """Reject new direct calls while admitted analyses finish their stages."""
        self._draining = True

    def _sync_idle(self) -> None:
        probe_running = self._health_probe is not None and not self._health_probe.task.done()
        if not self._calls and not probe_running:
            self._idle.set()
        else:
            self._idle.clear()

    async def wait_idle(self) -> None:
        # This observes current calls, not future stages of admitted analyses.
        # The shutdown coordinator also waits for the scheduler's whole jobs.
        # In RPC mode idle describes local calls, not remote CPU completion.
        await self._idle.wait()

    async def run_engine(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._closing or (self._draining and not _engine_admitted_analysis.get()):
            raise EngineBusy("服务正在关闭")
        caller = asyncio.current_task()
        assert caller is not None
        self._calls.add(caller)
        self._sync_idle()
        acquired = False
        waiting_since = perf_counter()
        try:
            wait_timeout = _engine_wait_timeout.get()
            if wait_timeout <= 0:
                # No await can interleave locked() and the immediate acquire on
                # this event loop; Semaphore also preserves existing waiters.
                if self._permits.locked():
                    raise EngineBusy()
                await self._permits.acquire()
            else:
                try:
                    await asyncio.wait_for(self._permits.acquire(), timeout=wait_timeout)
                except TimeoutError as exc:
                    raise EngineBusy("等待计算许可超时") from exc
            acquired = True
            permit_wait_ms = round((perf_counter() - waiting_since) * 1000, 3)
            if self._closing:
                raise EngineBusy("服务正在关闭")
            result = await self._transport.run_engine(payload)
            # Keep totalMs scoped to the original invocation; permit waiting
            # is a separate Python diagnostic and never enters the C++ wire.
            result.setdefault("diagnostics", {}).setdefault("invocation", {})[
                "permitWaitMs"] = permit_wait_ms
            return result
        finally:
            # Subprocess work has reaped its child; RPC work has finished its
            # local call cleanup. Only the C++ service owns its remote CPU slot.
            if acquired:
                self._permits.release()
            self._calls.discard(caller)
            self._sync_idle()

    async def _probe_health(self) -> EngineHealth:
        result = await self._transport.check_engine()
        if not self._closing:
            self._health_cache = (self._clock(), result.model_copy())
        return result

    async def _retire_probe(self, probe: _HealthProbe) -> None:
        await asyncio.gather(probe.task, return_exceptions=True)
        if self._health_probe is probe:
            self._health_probe = None
        self._sync_idle()

    def _begin_probe_retirement(self, probe: _HealthProbe) -> asyncio.Task[None]:
        if probe.retirement is None:
            probe.task.cancel()
            probe.retirement = asyncio.create_task(self._retire_probe(probe))
        return probe.retirement

    async def check_engine(self) -> EngineHealth:
        while True:
            if self._closing or self._draining:
                return EngineHealth(status="error", detail="ENGINE_BUSY: 服务正在关闭")
            probe = self._health_probe
            if probe is not None and probe.retirement is not None:
                # The last prior waiter cancelled its probe. Do not start a
                # replacement while that probe's local cleanup is still pending.
                await asyncio.shield(probe.retirement)
                continue
            if probe is not None and probe.task.done():
                # Earlier waiters may not have resumed yet. A finished task
                # must not bypass an expired cache by being joined again.
                if self._health_probe is probe:
                    self._health_probe = None
                probe = None
            if self._health_cache is not None:
                finished_at, cached = self._health_cache
                if self._clock() - finished_at < self._health_cache_ttl:
                    return cached.model_copy()
            if probe is None:
                probe = _HealthProbe(asyncio.create_task(self._probe_health()))
                self._health_probe = probe
                probe.task.add_done_callback(lambda _: self._sync_idle())
                self._sync_idle()
            probe.waiters += 1
            try:
                # Cancelling one caller must not cancel other callers' probe.
                return (await asyncio.shield(probe.task)).model_copy()
            finally:
                probe.waiters -= 1
                if probe.waiters == 0:
                    if not probe.task.done():
                        self._begin_probe_retirement(probe)
                    if probe.retirement is not None:
                        await _finish_cleanup(probe.retirement)
                    elif self._health_probe is probe:
                        self._health_probe = None

    async def _close(self) -> None:
        pending: list[asyncio.Task[Any]] = list(self._calls)
        for task in pending:
            task.cancel()
        if self._health_probe is not None:
            pending.append(self._begin_probe_retirement(self._health_probe))
        try:
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        finally:
            self._health_cache = None
            await self._transport.aclose()
            self._closed = True

    async def close(self) -> None:
        if self._close_task is None:
            self.begin_drain()
            self._closing = True
            self._close_task = asyncio.create_task(self._close())
        await _finish_cleanup(self._close_task)


def get_engine_runtime(*, restart: bool = False) -> EngineRuntime:
    # An event-loop-owned value has no external registry retaining a closed
    # loop through its semaphore/tasks. Only lifespan explicitly restarts it.
    loop = asyncio.get_running_loop()
    runtime: EngineRuntime | None = getattr(loop, "_livingcircle_engine_runtime", None)
    if runtime is None or (restart and runtime.closed):
        if settings.cpp_engine_transport == "grpc":
            # Optional dependencies and generated modules are never imported
            # by the default transport. Construct the channel on this loop.
            from .engine_rpc import GrpcTransport
            runtime = EngineRuntime(capacity=settings.cpp_engine_rpc_capacity,
                                    transport=GrpcTransport(settings.cpp_engine_rpc_port))
        else:
            runtime = EngineRuntime()
        setattr(loop, "_livingcircle_engine_runtime", runtime)
    return runtime


async def run_engine(payload: dict[str, Any]) -> dict[str, Any]:
    return await get_engine_runtime().run_engine(payload)


async def check_engine() -> EngineHealth:
    return await get_engine_runtime().check_engine()
