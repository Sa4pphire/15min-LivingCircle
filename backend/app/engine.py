import asyncio

from .schemas import EngineHealth
from .settings import settings


async def check_engine() -> EngineHealth:
    engine_path = settings.cpp_engine_path
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
