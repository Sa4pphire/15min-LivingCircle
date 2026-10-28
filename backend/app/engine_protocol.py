"""The unchanged v2 JSON codec, independent of its transport."""

import json
from typing import Any

from .engine_errors import EngineError
from .walking_types import normalize_graph


def encode_engine_input(payload: dict[str, Any]) -> bytes:
    # Keep annotations in Python; compact only the v2 wire representation.
    # Meter coordinates retain their original precision.
    payload = normalize_graph(payload)
    edge_fields = {"id", "from", "to", "kind", "accessMode", "pathMeters", "streetBlockId",
                   "side", "sharedWayType", "widthMeters", "waitSeconds"}
    wire = {**payload, "edges": [
        {key: value for key, value in edge.items() if key in edge_fields}
        for edge in payload.get("edges", [])]}
    return json.dumps(wire, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def decode_engine_result(stdout: bytes, *, returncode: int | None = 0,
                         stderr: bytes = b"") -> dict[str, Any]:
    try:
        response = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        detail = stderr.decode("utf-8", errors="replace").strip()[:300]
        if returncode != 0:
            raise EngineError(f"C++ 引擎异常退出（{returncode}）：{detail}",
                              code="ENGINE_EXIT_ERROR") from exc
        raise EngineError(f"C++ 引擎返回了无效 JSON：{detail}",
                          code="ENGINE_PROTOCOL_ERROR") from exc
    if not isinstance(response, dict) or response.get("schemaVersion") != 2:
        raise EngineError("C++ 引擎响应版本不匹配", code="ENGINE_PROTOCOL_ERROR")
    # The engine deliberately exits nonzero for valid business rejections.
    # Preserve their structured code before classifying an unexplained exit.
    if response.get("success") is False:
        error = response.get("error")
        if (not isinstance(error, dict) or not isinstance(error.get("code"), str)
                or not error["code"] or not isinstance(error.get("message"), str)):
            raise EngineError("C++ 引擎错误对象无效", code="ENGINE_PROTOCOL_ERROR")
        raise EngineError(f"{error['code']}: {error['message']}", code=error["code"])
    if response.get("success") is not True:
        raise EngineError("C++ 引擎缺少有效的成功标记", code="ENGINE_PROTOCOL_ERROR")
    if returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()[:300]
        raise EngineError(f"C++ 引擎异常退出（{returncode}）：{detail}",
                          code="ENGINE_EXIT_ERROR")
    if not isinstance(response.get("result"), dict):
        raise EngineError("C++ 引擎缺少结果对象", code="ENGINE_PROTOCOL_ERROR")
    diagnostics = response["result"].setdefault("diagnostics", {})
    if not isinstance(diagnostics, dict):
        raise EngineError("C++ 引擎诊断对象无效", code="ENGINE_PROTOCOL_ERROR")
    return response["result"]
