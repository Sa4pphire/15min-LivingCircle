"""HTTP boundary for the optional, query-only life-circle assistant."""

import asyncio

from fastapi import APIRouter, HTTPException, Request

from .agent_config import AgentConfig
from .agent_runtime import AgentError
from .agent_schemas import AgentChatRequest

router = APIRouter(prefix="/api/v1/agent", tags=["life-circle-agent"])


@router.get("/status")
async def agent_status(request: Request) -> dict:
    runtime = getattr(request.app.state, "agent_runtime", None)
    if runtime is not None:
        return runtime.status()
    config = AgentConfig.from_env()
    return {"enabled": config.enabled, "configured": config.configured}


@router.post("/chat")
async def agent_chat(request: Request, payload: AgentChatRequest) -> dict:
    runtime = getattr(request.app.state, "agent_runtime", None)
    if runtime is None:
        raise HTTPException(503, detail={"code": "AGENT_UNAVAILABLE", "message": "助手服务尚未就绪。"})

    async def watch_disconnect():
        while not await request.is_disconnected():
            await asyncio.sleep(0.25)

    turn = asyncio.create_task(runtime.chat(payload), name="agent-http-turn")
    disconnect = asyncio.create_task(watch_disconnect(), name="agent-http-disconnect")
    try:
        done, _ = await asyncio.wait((turn, disconnect), return_when=asyncio.FIRST_COMPLETED)
        if turn in done:
            return turn.result()
        raise HTTPException(499, detail={"code": "AGENT_CLIENT_DISCONNECTED", "message": "客户端已取消对话。"})
    except AgentError as exc:
        raise HTTPException(exc.status, detail={"code": exc.code, "message": str(exc)},
                            headers={"Retry-After": "1"} if exc.status == 503 else None) from exc
    finally:
        # Cancellation propagates into the tool loop. Its C++ calls retain the
        # engine adapter's existing terminate-and-reap cleanup before release.
        for task in (turn, disconnect):
            if not task.done():
                task.cancel()
        await asyncio.gather(turn, disconnect, return_exceptions=True)
