"""Optional, loop-owned gRPC transport for full or fixed-graph v2 requests."""

import asyncio
import json
import logging
from time import perf_counter
from typing import Any

from .engine_cleanup import finish_cleanup
from .engine_errors import EngineBusy, EngineError
from .engine_protocol import (
    decode_engine_result, encode_analysis_request, encode_engine_input,
    is_graph_snapshot_id,
)
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

    def __init__(self, port: int, *, expected_graph_id: str | None = None) -> None:
        if expected_graph_id is not None and not is_graph_snapshot_id(expected_graph_id):
            raise EngineError("固定图配置中的 graph_id 格式无效",
                              code="ENGINE_CONFIGURATION_ERROR")
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
        self._expected_graph_id = expected_graph_id
        if expected_graph_id is not None:
            self._require_graph_protocol()
        self._channel = grpc.aio.insecure_channel(
            f"127.0.0.1:{port}",
            options=(("grpc.max_send_message_length", MAX_SEND_MESSAGE_BYTES),
                     ("grpc.max_receive_message_length", MAX_RECEIVE_MESSAGE_BYTES),
                     ("grpc.enable_retries", 0)),
        )
        self._stub = engine_service_pb2_grpc.EngineServiceStub(self._channel)

    def _require_graph_protocol(self) -> None:
        if (not hasattr(self._messages, "GraphComputeRequest") or
                not hasattr(self._messages, "GraphComputeReply") or
                "graph_snapshot" not in self._messages.JsonReply.DESCRIPTOR.fields_by_name):
            raise EngineError(
                "RPC 协议代码尚不支持固定图，请使用 generate_engine_rpc.py 重新生成。",
                code="ENGINE_CONFIGURATION_ERROR")

    def _rpc_error(self, error) -> EngineError:
        status = error.code()
        codes = self._grpc.StatusCode
        marker = dict(error.trailing_metadata() or ()).get("engine-error-code")
        # Only accept the known marker/status pairs; upstream message text is
        # never promoted into an application error or used to select a fallback.
        if status == codes.INVALID_ARGUMENT and marker == "INVALID_GRAPH_ID":
            return EngineError("C++ RPC 拒绝了无效的图版本标识", code="INVALID_GRAPH_ID")
        if status == codes.FAILED_PRECONDITION:
            if marker == "GRAPH_NOT_LOADED":
                return EngineError("C++ RPC 尚未加载固定图", code="GRAPH_NOT_LOADED")
            if marker == "GRAPH_VERSION_MISMATCH":
                return EngineError("Python 与 C++ RPC 的固定图版本不一致",
                                   code="GRAPH_VERSION_MISMATCH")
        if status == codes.RESOURCE_EXHAUSTED:
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
        if status in (codes.UNIMPLEMENTED, codes.INVALID_ARGUMENT,
                      codes.FAILED_PRECONDITION, codes.DATA_LOSS):
            return EngineError("C++ RPC 协议不匹配或响应无效", code="ENGINE_PROTOCOL_ERROR")
        return EngineError("C++ RPC 通信失败", code="ENGINE_COMMUNICATION_ERROR")

    @staticmethod
    async def _settle_local_call(call) -> None:
        # This observes the client call's terminal status, not server CPU exit.
        await call.code()

    async def _invoke(self, method: str, request, timeout: float, *,
                      reply_type=None, graph_id: str | None = None):
        started = perf_counter()
        status = "CLIENT_ERROR"
        call = None
        try:
            invoke = getattr(self._stub, method, None)
            if invoke is None:
                raise EngineError("RPC 绑定缺少所需方法，请重新生成协议代码。",
                                  code="ENGINE_CONFIGURATION_ERROR")
            call = invoke(request, timeout=timeout, wait_for_ready=False)
            reply = await call
            expected_type = reply_type if reply_type is not None else self._messages.JsonReply
            if not isinstance(reply, expected_type):
                status = "ENGINE_PROTOCOL_ERROR"
                raise EngineError("C++ RPC 回复类型无效", code="ENGINE_PROTOCOL_ERROR")
            if graph_id is not None and reply.graph_id != graph_id:
                status = "GRAPH_VERSION_MISMATCH"
                raise EngineError("C++ RPC 回复的图版本与请求不一致",
                                  code="GRAPH_VERSION_MISMATCH")
            status = "OK"
            return reply
        except asyncio.CancelledError:
            status = "CANCELLED"
            if call is not None:
                call.cancel()
                await finish_cleanup(asyncio.create_task(self._settle_local_call(call)))
            raise
        except self._grpc.aio.AioRpcError as exc:
            status = exc.code().name
            raise self._rpc_error(exc) from exc
        except EngineError as exc:
            status = exc.code
            raise
        finally:
            # Status is the RPC/client status; OK can still contain a business
            # rejection. Do not print payloads or upstream exception details.
            rpc_logger.info(
                "engine_rpc transport=grpc method=%s graph_id=%s status=%s duration_ms=%.3f",
                method, graph_id or "-", status, (perf_counter() - started) * 1000)

    async def run_engine(self, payload: dict[str, Any], *,
                         graph_id: str | None = None) -> dict[str, Any]:
        started = perf_counter()
        if graph_id is None:
            method = "Compute"
            encoded = encode_engine_input(payload)
            reply_type = self._messages.JsonReply
        else:
            if not is_graph_snapshot_id(graph_id):
                raise EngineError("固定图请求的 graph_id 格式无效", code="INVALID_GRAPH_ID")
            if self._expected_graph_id is not None and graph_id != self._expected_graph_id:
                raise EngineError("请求图版本与 Python 固定上下文配置不一致",
                                  code="GRAPH_VERSION_MISMATCH")
            self._require_graph_protocol()
            method = "ComputeOnGraph"
            # Local graph fields remain available to POI binding. Project only
            # the request fields onto the wire, without touching shared geometry.
            analysis_request = {key: value for key, value in payload.items()
                                if key not in ("nodes", "edges")}
            encoded = encode_analysis_request(analysis_request)
            reply_type = self._messages.GraphComputeReply
        encoded_at = perf_counter()
        if len(encoded) > MAX_JSON_INPUT_BYTES:
            raise EngineError("INVALID_INPUT: engine input is too large", code="INVALID_INPUT")
        if graph_id is None:
            request = self._messages.ComputeRequest(request_json=encoded)
        else:
            request = self._messages.GraphComputeRequest(
                graph_id=graph_id, analysis_request_json=encoded)
        if request.ByteSize() > MAX_SEND_MESSAGE_BYTES:
            raise EngineError("C++ RPC 请求超过消息大小限制", code="INVALID_INPUT")
        reply = await self._invoke(method, request, COMPUTE_DEADLINE_SECONDS,
                                   reply_type=reply_type, graph_id=graph_id)
        stdout = reply.response_json
        received_at = perf_counter()
        result = decode_engine_result(stdout)
        finished_at = perf_counter()
        result["diagnostics"]["invocation"] = {
            "transport": "grpc",
            "method": method,
            "encodeMs": round((encoded_at - started) * 1000, 3),
            "roundTripMs": round((received_at - encoded_at) * 1000, 3),
            "decodeMs": round((finished_at - received_at) * 1000, 3),
            "totalMs": round((finished_at - started) * 1000, 3),
            "inputBytes": len(encoded), "outputBytes": len(stdout),
        }
        if graph_id is not None:
            result["diagnostics"]["invocation"]["graphId"] = graph_id
        return result

    async def check_engine(self) -> EngineHealth:
        try:
            reply = await self._invoke("Health", self._messages.HealthRequest(),
                                       HEALTH_DEADLINE_SECONDS)
            stdout = reply.response_json
            detail = stdout.decode("utf-8").strip()
            document = json.loads(detail)
            if (not isinstance(document, dict) or document.get("status") != "ok" or
                    document.get("schemaVersion") != 2 or
                    document.get("engine") != "isochrone_engine"):
                raise ValueError("invalid health response")
            has_graph_field = "graph_snapshot" in reply.DESCRIPTOR.fields_by_name
            has_snapshot = has_graph_field and reply.HasField("graph_snapshot")
            if self._expected_graph_id is not None and not has_snapshot:
                raise EngineError("GRAPH_NOT_LOADED: C++ RPC 未报告已加载的固定图",
                                  code="GRAPH_NOT_LOADED")
            if has_snapshot:
                snapshot = reply.graph_snapshot
                if not is_graph_snapshot_id(snapshot.graph_id):
                    raise EngineError("ENGINE_PROTOCOL_ERROR: 健康响应的图版本标识无效",
                                      code="ENGINE_PROTOCOL_ERROR")
                if (self._expected_graph_id is not None and
                        snapshot.graph_id != self._expected_graph_id):
                    raise EngineError("GRAPH_VERSION_MISMATCH: Python 与 C++ RPC 的固定图版本不一致",
                                      code="GRAPH_VERSION_MISMATCH")
                document["graphSnapshot"] = {
                    "graphId": snapshot.graph_id,
                    "sourceBytes": snapshot.source_bytes,
                    "nodeCount": snapshot.node_count,
                    "edgeCount": snapshot.edge_count,
                    "pathPointCount": snapshot.path_point_count,
                }
                detail = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
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
