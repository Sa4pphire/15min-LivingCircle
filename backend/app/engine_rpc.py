"""Optional, loop-owned gRPC transport for the existing v2 JSON protocol."""

import asyncio
import json
import logging
from time import perf_counter
from typing import Any

from .engine_cleanup import finish_cleanup
from .engine_errors import EngineBusy, EngineError
from .engine_protocol import decode_engine_result, encode_engine_input
from .schemas import EngineHealth


MAX_JSON_INPUT_BYTES = 20_000_000
MAX_SEND_MESSAGE_BYTES = 20 * 1024 * 1024
MAX_RECEIVE_MESSAGE_BYTES = 64 * 1024 * 1024
COMPUTE_DEADLINE_SECONDS = 25
HEALTH_DEADLINE_SECONDS = 3

rpc_logger = logging.getLogger("livingcircle.engine.rpc")
rpc_logger.setLevel(logging.INFO)
rpc_logger.propagate = False
if not rpc_logger.handlers:
    rpc_logger.addHandler(logging.StreamHandler())


class GrpcTransport:
    """One channel/stub per EngineRuntime; no subprocess or retry fallback."""

    def __init__(self, port: int) -> None:
        try:
            import grpc
        except (ImportError, OSError) as exc:
            raise EngineError(
                "RPC 依赖不可用，请安装 backend/requirements-rpc.txt 并生成协议代码。",
                code="ENGINE_CONFIGURATION_ERROR") from exc
        try:
            from .engine_rpc_generated import engine_service_pb2, engine_service_pb2_grpc
        except Exception as exc:
            # Generated modules enforce their own protobuf/gRPC versions.
            # Keep this import boundary independent of private runtime APIs.
            raise EngineError(
                "RPC 协议代码缺失或版本不兼容，请使用 generate_engine_rpc.py 重新生成。",
                code="ENGINE_CONFIGURATION_ERROR") from exc

        self._grpc = grpc
        self._messages = engine_service_pb2
        self._channel = grpc.aio.insecure_channel(
            f"127.0.0.1:{port}",
            options=(("grpc.max_send_message_length", MAX_SEND_MESSAGE_BYTES),
                     ("grpc.max_receive_message_length", MAX_RECEIVE_MESSAGE_BYTES),
                     ("grpc.enable_retries", 0)),
        )
        self._stub = engine_service_pb2_grpc.EngineServiceStub(self._channel)

    def _rpc_error(self, error) -> EngineError:
        status = error.code()
        codes = self._grpc.StatusCode
        if status == codes.RESOURCE_EXHAUSTED:
            marker = dict(error.trailing_metadata() or ()).get("engine-error-code")
            if marker == "ENGINE_BUSY":
                return EngineBusy("C++ RPC 计算资源繁忙，请稍后重试")
            if marker == "ENGINE_RESPONSE_TOO_LARGE":
                return EngineError("C++ RPC 响应超过消息大小限制",
                                   code="ENGINE_RESPONSE_TOO_LARGE")
            return EngineError("C++ RPC 资源或消息大小限制已触发",
                               code="ENGINE_RESOURCE_EXHAUSTED")
        if status == codes.DEADLINE_EXCEEDED:
            return EngineError("C++ RPC 调用超时", code="ENGINE_TIMEOUT")
        if status == codes.UNAVAILABLE:
            return EngineError("C++ RPC 服务不可用", code="ENGINE_UNAVAILABLE")
        if status == codes.CANCELLED:
            return EngineError("C++ RPC 调用已被服务取消", code="ENGINE_CANCELLED")
        if status in (codes.UNIMPLEMENTED, codes.INVALID_ARGUMENT, codes.DATA_LOSS):
            return EngineError("C++ RPC 协议不匹配或响应无效", code="ENGINE_PROTOCOL_ERROR")
        return EngineError("C++ RPC 通信失败", code="ENGINE_COMMUNICATION_ERROR")

    @staticmethod
    async def _settle_local_call(call) -> None:
        # This observes the client call's terminal status, not server CPU exit.
        await call.code()

    async def _invoke(self, method: str, request, timeout: float) -> bytes:
        started = perf_counter()
        status = "CLIENT_ERROR"
        call = None
        try:
            call = getattr(self._stub, method)(request, timeout=timeout,
                                               wait_for_ready=False)
            reply = await call
            if not isinstance(reply, self._messages.JsonReply):
                status = "ENGINE_PROTOCOL_ERROR"
                raise EngineError("C++ RPC 回复类型无效", code="ENGINE_PROTOCOL_ERROR")
            status = "OK"
            return reply.response_json
        except asyncio.CancelledError:
            status = "CANCELLED"
            if call is not None:
                call.cancel()
                await finish_cleanup(asyncio.create_task(self._settle_local_call(call)))
            raise
        except self._grpc.aio.AioRpcError as exc:
            status = exc.code().name
            raise self._rpc_error(exc) from exc
        finally:
            # Status is the RPC/client status; OK can still contain a business
            # rejection. Do not print payloads or upstream exception details.
            rpc_logger.info("engine_rpc transport=grpc method=%s status=%s duration_ms=%.3f",
                            method, status, (perf_counter() - started) * 1000)

    async def run_engine(self, payload: dict[str, Any]) -> dict[str, Any]:
        started = perf_counter()
        encoded = encode_engine_input(payload)
        encoded_at = perf_counter()
        if len(encoded) > MAX_JSON_INPUT_BYTES:
            raise EngineError("INVALID_INPUT: engine input is too large", code="INVALID_INPUT")
        stdout = await self._invoke(
            "Compute", self._messages.ComputeRequest(request_json=encoded),
            COMPUTE_DEADLINE_SECONDS)
        received_at = perf_counter()
        result = decode_engine_result(stdout)
        finished_at = perf_counter()
        result["diagnostics"]["invocation"] = {
            "transport": "grpc",
            "encodeMs": round((encoded_at - started) * 1000, 3),
            "roundTripMs": round((received_at - encoded_at) * 1000, 3),
            "decodeMs": round((finished_at - received_at) * 1000, 3),
            "totalMs": round((finished_at - started) * 1000, 3),
            "inputBytes": len(encoded), "outputBytes": len(stdout),
        }
        return result

    async def check_engine(self) -> EngineHealth:
        try:
            stdout = await self._invoke("Health", self._messages.HealthRequest(),
                                        HEALTH_DEADLINE_SECONDS)
            detail = stdout.decode("utf-8").strip()
            document = json.loads(detail)
            if (not isinstance(document, dict) or document.get("status") != "ok" or
                    document.get("schemaVersion") != 2 or
                    document.get("engine") != "isochrone_engine"):
                raise ValueError("invalid health response")
        except EngineError as exc:
            return EngineHealth(status="unavailable" if exc.code == "ENGINE_UNAVAILABLE" else "error",
                                detail=str(exc))
        except (UnicodeDecodeError, ValueError):
            return EngineHealth(status="error", detail="ENGINE_PROTOCOL_ERROR: C++ RPC 健康响应无效")
        return EngineHealth(status="ok", detail=detail)

    async def aclose(self) -> None:
        # EngineRuntime has settled local calls/probes before closing this
        # channel. Closing it never owns or stops the standalone C++ process.
        await self._channel.close()
