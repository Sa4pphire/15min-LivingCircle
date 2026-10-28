"""Errors shared by engine transports and the existing business entry point."""


class EngineError(RuntimeError):
    def __init__(self, message: str, *, code: str = "ENGINE_ERROR") -> None:
        super().__init__(message)
        self.code = code


class EngineBusy(EngineError):
    def __init__(self, detail: str = "计算资源繁忙，请稍后重试") -> None:
        super().__init__(f"ENGINE_BUSY: {detail}", code="ENGINE_BUSY")
