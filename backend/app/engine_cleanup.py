"""Cancellation-safe ownership of local subprocess or RPC cleanup."""

import asyncio


async def finish_cleanup(cleanup: asyncio.Task[None]) -> None:
    """Await owned local cleanup even through repeated caller cancellation."""
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
