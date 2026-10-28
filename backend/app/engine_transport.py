"""Transport contract; importing this module does not require gRPC."""

from typing import Any, Protocol

from .schemas import EngineHealth


class EngineTransport(Protocol):
    async def run_engine(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    async def check_engine(self) -> EngineHealth: ...

    async def aclose(self) -> None: ...
