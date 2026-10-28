"""One application-wide drain window, followed by owned resource cleanup."""

import asyncio
import math

from .analysis_scheduler import AnalysisScheduler
from .engine import EngineRuntime


async def _shutdown_services(scheduler: AnalysisScheduler, runtime: EngineRuntime,
                             deadline: float) -> None:
    try:
        try:
            # Cancel only these idle waiters at the deadline. The actual jobs
            # and engine calls continue until close explicitly cancels them.
            async with asyncio.timeout_at(deadline):
                await asyncio.gather(scheduler.wait_idle(), runtime.wait_idle())
        except TimeoutError:
            pass
    finally:
        # Start both cancellations at the same deadline. Waiting for analysis
        # cleanup first would give direct calls/probes an extra run window.
        results = await asyncio.gather(scheduler.close(), runtime.close(),
                                       return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result
        # Workers may register terminal history during cancellation cleanup.
        # Clear it only after both owners have really finished their cleanup.
        scheduler.history.clear()


async def shutdown_services(scheduler: AnalysisScheduler, runtime: EngineRuntime,
                            *, grace_seconds: float = 5.0) -> None:
    """Stop admission, drain accepted work, then cancel and await real cleanup.

    The grace window is shared, not a separate budget for each owner. It is
    not a hard limit on process reaping or other local cleanup. RPC cleanup
    does not acknowledge remote CPU completion or stop the C++ server. Caller
    cancellation is propagated only after the owned shutdown task finishes.
    External input-preparation reservations still belong to their API callers.
    """
    if not math.isfinite(grace_seconds) or grace_seconds < 0:
        raise ValueError("grace_seconds must be finite and non-negative")
    # No await between stopping the two admissions: an accepted job may still
    # start later engine stages via engine_wait_scope during this drain.
    scheduler.stop_accepting()
    runtime.begin_drain()
    deadline = asyncio.get_running_loop().time() + grace_seconds
    cleanup = asyncio.create_task(_shutdown_services(scheduler, runtime, deadline),
                                  name="application-shutdown")
    cancelled = False
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            if cleanup.cancelled():
                raise
            cancelled = True
    cleanup.result()
    if cancelled:
        raise asyncio.CancelledError
