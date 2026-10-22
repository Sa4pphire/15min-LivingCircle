"""Single-event-loop admission and execution for ordinary/synthetic analyses."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import json
import logging
from time import perf_counter

from .schemas import AnalysisState, Progress


logger = logging.getLogger("livingcircle.analysis")


@dataclass
class ScheduledAnalysis:
    # The API store and scheduler share this authoritative state object.
    state: AnalysisState
    run: Callable[[], Awaitable[None]]
    enqueued_at: float = 0.0


class AnalysisScheduler:
    """All methods must be called on the service's event-loop thread.

    A reservation covers input preparation and queuing. Its caller owns it
    until successful submit(); dequeue transfers the job into a worker slot.
    """

    def __init__(self, workers: int = 2, pending_limit: int = 8):
        if workers < 1 or pending_limit < 1:
            raise ValueError("workers and pending_limit must be positive")
        self.worker_count = workers
        self.queue: asyncio.Queue[tuple[object, ScheduledAnalysis]] = asyncio.Queue(
            maxsize=pending_limit)
        self._reservations: dict[object, str] = {}
        self._workers: list[asyncio.Task[None]] = []
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
        job.enqueued_at = perf_counter()
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

    @staticmethod
    def _fail(state: AnalysisState, message: str) -> None:
        state.status = "failed"
        state.error = message
        state.progress = Progress(stage="failed", percent=100)

    @staticmethod
    def _finished(state: AnalysisState) -> None:
        logger.info(json.dumps({"event": "analysis.finished",
                                "analysisId": state.analysisId,
                                "status": state.status,
                                "timingsMs": state.timingsMs,
                                "error": state.error}, ensure_ascii=False))

    async def _worker(self) -> None:
        while True:
            reservation, job = await self.queue.get()
            started = perf_counter()
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
                # _run_analysis can catch an engine failure and return normally.
                # Its shared state's failed/completed value remains authoritative.
                if job.state.status not in ("completed", "failed"):
                    self._fail(job.state, "ANALYSIS_INCOMPLETE: 执行结束但未记录终态")
            except asyncio.CancelledError:
                # The adapter finishes child cleanup before cancellation reaches us.
                self._fail(job.state, "SCHEDULER_SHUTDOWN: 服务关闭，任务已取消")
                raise
            except Exception as exc:
                self._fail(job.state, str(exc))
            finally:
                job.state.timingsMs.setdefault(
                    "total", round((perf_counter() - started) * 1000, 3))
                self.running -= 1
                self.queue.task_done()
                self._finished(job.state)

    async def wait_idle(self) -> None:
        """Wait for enqueued jobs, not external preparation reservations."""
        await self.queue.join()

    async def close(self) -> None:
        if self._close_task is None:
            self.accepting = False
            self._close_task = asyncio.create_task(self._close())
        await asyncio.shield(self._close_task)

    async def _close(self) -> None:
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        while True:
            try:
                reservation, job = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            try:
                self._release_queued(reservation)
                job.state.timingsMs["queueWait"] = round(
                    (perf_counter() - job.enqueued_at) * 1000, 3)
                self._fail(job.state, "SCHEDULER_SHUTDOWN: 服务关闭，任务尚未开始")
            finally:
                self.queue.task_done()
                self._finished(job.state)
        # In-flight preparation reservations still belong to their API callers.
