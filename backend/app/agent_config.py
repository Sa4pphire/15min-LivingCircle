"""Optional server-only model configuration; bad settings disable only the agent."""

from dataclasses import dataclass, field
import math
import os
from urllib.parse import urlsplit


@dataclass(frozen=True)
class AgentConfig:
    enabled: bool
    base_url: str
    api_key: str = field(repr=False)
    model: str = ""
    timeout_seconds: float = 90
    model_timeout_seconds: float = 25
    max_concurrency: int = 2
    max_model_calls: int = 4
    valid: bool = True

    @property
    def configured(self) -> bool:
        try:
            url = urlsplit(self.base_url)
            port = url.port  # Reject malformed/out-of-range ports before HTTP.
            return bool(self.valid and self.api_key and self.model
                        and url.scheme in ("https", "http") and url.hostname
                        and (port is None or 1 <= port <= 65535)
                        and not url.username and not url.password
                        and not url.query and not url.fragment
                        and not url.path.rstrip("/").endswith("/responses"))
        except ValueError:
            return False

    @classmethod
    def from_env(cls) -> "AgentConfig":
        valid = True

        def number(name, default, lower, upper, *, integer=False):
            nonlocal valid
            try:
                value = int(os.getenv(name, str(default))) if integer else float(os.getenv(name, str(default)))
                if not math.isfinite(value) or not lower <= value <= upper:
                    raise ValueError
                return value
            except (ValueError, OverflowError):
                valid = False
                return default

        timeout = number("AGENT_TIMEOUT_SECONDS", 90, 1, 300)
        model_timeout = number("AGENT_MODEL_TIMEOUT_SECONDS", 25, 1, 120)
        concurrency = number("AGENT_MAX_CONCURRENCY", 2, 1, 16, integer=True)
        calls = number("AGENT_MAX_MODEL_CALLS", 4, 2, 8, integer=True)
        enabled = os.getenv("AGENT_ENABLED", "false").strip().lower()
        if enabled not in ("true", "false", "1", "0"):
            valid = False
        return cls(enabled in ("true", "1"), os.getenv("AGENT_API_BASE_URL", "").strip().rstrip("/"),
                   os.getenv("AGENT_API_KEY", "").strip(), os.getenv("AGENT_MODEL", "").strip(),
                   timeout, model_timeout, concurrency, calls, valid)
