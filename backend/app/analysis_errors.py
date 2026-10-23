"""Machine-readable task failures; never infer codes from display text."""

import sqlite3

from .baidu.errors import BaiduApiError
from .engine import EngineError


def analysis_error_code(exc: Exception) -> str:
    if isinstance(exc, EngineError):
        return exc.code
    if isinstance(exc, BaiduApiError):
        return "UPSTREAM_ERROR"
    if isinstance(exc, (OSError, sqlite3.Error)):
        return "DATA_ACCESS_ERROR"
    # Key/Type/Value errors can be internal transformations, not user mistakes.
    return "ANALYSIS_ERROR"
