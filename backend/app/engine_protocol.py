"""Full-input and request-only v2 JSON codecs, independent of transport."""

import json
from typing import Any

from .engine_errors import EngineError
from .walking_types import normalize_graph


_EDGE_WIRE_FIELDS = frozenset({
    "id", "from", "to", "kind", "accessMode", "pathMeters", "streetBlockId",
    "side", "sharedWayType", "widthMeters", "waitSeconds",
})


def is_graph_snapshot_id(value: object) -> bool:
    """Check ID syntax only; artifact loaders separately verify actual bytes."""
    return (isinstance(value, str) and len(value) == 71 and
            value.startswith("sha256:") and
            all(character in "0123456789abcdef" for character in value[7:]))


def compact_graph_fields(graph: dict[str, Any]) -> dict[str, Any]:
    """Share canonical wire graph fields between full requests and export.

    Keep annotations in Python and preserve meter precision. Missing nodes stay
    missing for the legacy encoder; artifact export validates its own inputs.
    Returned nested geometry is borrowed read-only, never edited here.
    """
    normalized = normalize_graph(graph)
    fields = {"edges": [
        {key: value for key, value in edge.items() if key in _EDGE_WIRE_FIELDS}
        for edge in normalized["edges"]]}
    if "nodes" in normalized:
        fields["nodes"] = normalized["nodes"]
    return fields


def encode_engine_input(payload: dict[str, Any]) -> bytes:
    wire = {**payload, **compact_graph_fields(payload)}
    return json.dumps(wire, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def encode_analysis_request(payload: dict[str, Any]) -> bytes:
    """Encode request-only input without normalizing or copying the graph."""
    if "nodes" in payload or "edges" in payload:
        raise EngineError("INVALID_INPUT: analysis request must not contain nodes or edges",
                          code="INVALID_INPUT")
    return json.dumps(payload, ensure_ascii=False, allow_nan=False,
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
