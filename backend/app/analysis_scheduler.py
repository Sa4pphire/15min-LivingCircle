"""Single-event-loop admission and execution shared by all four analysis types."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import json
import logging
import math
from time import perf_counter

from .analysis_history import AnalysisHistory
from .analysis_errors import analysis_error_code
from .schemas import AnalysisState, Progress


logger = logging.getLogger("livingcircle.analysis")


@dataclass
class ScheduledAnalysis:
    # The API store and scheduler share this authoritative state object.
    state: AnalysisState
    run: Callable[[], Awaitable[None]]
    enqueued_at: float = 0.0
    store: dict[str, AnalysisState] | None = None


class AnalysisScheduler:
    """All methods must be called on the service's event-loop thread.

    A reservation covers input preparation and queuing. Its caller owns it
    until successful submit(); dequeue transfers the job into a worker slot.
    Queued jobs expire after queue_timeout; preparation and running jobs keep
    their own lifetimes.
    """

    def __init__(self, workers: int = 2, pending_limit: int = 8,
                 queue_timeout: float = 30.0, sweep_interval: float = 1.0,
                 *, clock: Callable[[], float] = perf_counter,
                 history_ttl: float = 600.0, history_capacity: int = 100,
                 forget_context: Callable[[str], None] | None = None):
        if workers < 1 or pending_limit < 1:
            raise ValueError("workers and pending_limit must be positive")
        if (not math.isfinite(queue_timeout) or queue_timeout <= 0
                or not math.isfinite(sweep_interval) or sweep_interval <= 0):
            raise ValueError("queue_timeout and sweep_interval must be finite and positive")
        self.worker_count = workers
        self.queue_timeout = queue_timeout
        self.sweep_interval = sweep_interval
        self._clock = clock
        self.history = AnalysisHistory(history_ttl, history_capacity,
                                       clock=clock, on_remove=forget_context)
        self.queue: asyncio.Queue[tuple[object, ScheduledAnalysis]] = asyncio.Queue(
            maxsize=pending_limit)
        self._reservations: dict[object, str] = {}
        self._workers: list[asyncio.Task[None]] = []
        self._sweeper: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self.accepting = False
        self.running = 0

    @property
    def pending(self) -> int:
        return len(self._reservations)

    async def start(self) -> None:
        if self._close_task is not None:
            raise RuntimeError("a closed scheduler cannot be restarted")
        if self._workers:
            return
        self._workers = [asyncio.create_task(self._worker(), name=f"analysis-worker-{i}")
                         for i in range(self.worker_count)]
        self._sweeper = asyncio.create_task(self._sweep_loop(), name="analysis-queue-sweeper")
        self.accepting = True

    def reserve(self) -> object | None:
        if not self.accepting or self.pending >= self.queue.maxsize:
            return None
        reservation = object()
        self._reservations[reservation] = "reserved"
        return reservation

    def submit(self, reservation: object, job: ScheduledAnalysis) -> bool:
        if self._reservations.get(reservation) != "reserved":
            raise ValueError("reservation is unknown, released, or already submitted")
        if not self.accepting:
            return False
        try:
            self.queue.put_nowait((reservation, job))
        except asyncio.QueueFull:
            return False
        job.enqueued_at = self._clock()
        self._reservations[reservation] = "queued"
        return True

    def release(self, reservation: object) -> None:
        state = self._reservations.get(reservation)
        if state == "queued":
            raise ValueError("a queued reservation belongs to the scheduler")
        if state == "reserved":
            del self._reservations[reservation]

    def _release_queued(self, reservation: object) -> None:
        if self._reservations.get(reservation) != "queued":
            raise RuntimeError("queue and reservation registry are inconsistent")
        del self._reservations[reservation]

    def _is_expired(self, job: ScheduledAnalysis, now: float) -> bool:
        return now - job.enqueued_at >= self.queue_timeout

    def _expire_queued(self, reservation: object, job: ScheduledAnalysis, now: float) -> None:
        self._release_queued(reservation)
        job.state.timingsMs["queueWait"] = round((now - job.enqueued_at) * 1000, 3)
        self._fail(job.state,
                   f"QUEUE_TIMEOUT: 排队等待达到{self.queue_timeout:g}秒，任务未执行",
                   code="QUEUE_TIMEOUT")
        self._finished(job)

    def expire_queued(self) -> int:
        """Remove expired queued jobs without touching preparation or running jobs."""
        now = self._clock()
        expired = 0
        # No await: rotate the current queue once, preserving survivor order.
        for _ in range(self.queue.qsize()):
            reservation, job = self.queue.get_nowait()
            try:
                if self._is_expired(job, now):
                    self._expire_queued(reservation, job, now)
                    expired += 1
                else:
                    # Reinsert BEFORE task_done so join cannot observe false idle.
                    self.queue.put_nowait((reservation, job))
            finally:
                self.queue.task_done()
        return expired

    async def _sweep_loop(self) -> None:
        while True:
            await asyncio.sleep(self.sweep_interval)
            self.expire_queued()
            self.history.prune()

    @staticmethod
    def _fail(state: AnalysisState, message: str, *, code: str) -> None:
        state.status = "failed"
        state.error = message
        state.errorCode = code
        state.progress = Progress(stage="failed", percent=100)

    def _finished(self, job: ScheduledAnalysis) -> None:
        state = job.state
        logger.info(json.dumps({"event": "analysis.finished",
                                "analysisId": state.analysisId,
                                "status": state.status,
                                "timingsMs": state.timingsMs,
                                "errorCode": state.errorCode,
                                "error": state.error}, ensure_ascii=False))
        if job.store is not None:
            self.history.remember(state, job.store)

    async def _worker(self) -> None:
        while True:
            reservation, job = await self.queue.get()
            try:
                await self._run_job(reservation, job)
            finally:
                # An idle worker must not retain the last runnable's input closure.
                del job

    async def _run_job(self, reservation: object, job: ScheduledAnalysis) -> None:
        started = self._clock()
        # A delayed sweep must never allow an expired job to start.
        if self._is_expired(job, started):
            try:
                self._expire_queued(reservation, job, started)
            finally:
                self.queue.task_done()
            return
        self.running += 1
        try:
            self._release_queued(reservation)
            job.state.timingsMs["queueWait"] = round(
                (started - job.enqueued_at) * 1000, 3)
            job.state.status = "running"
            logger.info(json.dumps({"event": "analysis.started",
                                    "analysisId": job.state.analysisId,
                                    "status": "running"}, ensure_ascii=False))
            await job.run()
            # Business functions can catch a failure and return normally.
            # Its shared state's failed/completed value remains authoritative.
            if job.state.status not in ("completed", "failed"):
                self._fail(job.state, "ANALYSIS_INCOMPLETE: 执行结束但未记录终态",
                           code="ANALYSIS_INCOMPLETE")
        except asyncio.CancelledError:
            # The adapter finishes child cleanup before cancellation reaches us.
            self._fail(job.state, "SCHEDULER_SHUTDOWN: 服务关闭，任务已取消",
                       code="SCHEDULER_SHUTDOWN")
            raise
        except Exception as exc:
            self._fail(job.state, str(exc), code=analysis_error_code(exc))
        finally:
            job.state.timingsMs.setdefault(
                "total", round((self._clock() - started) * 1000, 3))
            self.running -= 1
            self.queue.task_done()
            self._finished(job)

    async def wait_idle(self) -> None:
        """Wait for enqueued jobs, not external preparation reservations."""
        await self.queue.join()

    def stop_accepting(self) -> None:
        """Keep accepted jobs running while rejecting reservations/submissions."""
        self.accepting = False

    async def close(self) -> None:
        if self._close_task is None:
            self.stop_accepting()
            self._close_task = asyncio.create_task(self._close())
        await asyncio.shield(self._close_task)

    async def _close(self) -> None:
        tasks = list(self._workers)
        if self._sweeper is not None:
            tasks.append(self._sweeper)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._workers.clear()
        self._sweeper = None
        while True:
            try:
                reservation, job = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            try:
                self._release_queued(reservation)
                job.state.timingsMs["queueWait"] = round(
                    (self._clock() - job.enqueued_at) * 1000, 3)
                self._fail(job.state, "SCHEDULER_SHUTDOWN: 服务关闭，任务尚未开始",
                           code="SCHEDULER_SHUTDOWN")
            finally:
                self.queue.task_done()
                self._finished(job)
        # In-flight preparation reservations still belong to their API callers.
