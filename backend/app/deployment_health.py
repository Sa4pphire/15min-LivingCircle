"""Container readiness: check the engine and the default region through HTTP."""

import json
import os
import sys
from urllib.request import ProxyHandler, build_opener


def check_service(base_url: str) -> None:
    opener = build_opener(ProxyHandler({}))

    def read(path: str) -> dict:
        with opener.open(f"{base_url}{path}", timeout=4) as response:
            result = json.load(response)
        if not isinstance(result, dict):
            raise ValueError(f"Invalid readiness response: {path}")
        return result

    health = read("/api/v1/health")
    engine = health.get("engine")
    if (health.get("status") != "ok" or not isinstance(engine, dict) or
            engine.get("status") != "ok"):
        raise ValueError("Calculation engine is not ready")
    region = read("/api/v1/region")
    if not region.get("id"):
        raise ValueError("Default region is not ready")


def main() -> int:
    try:
        port = int(os.getenv("APP_PORT", "8000"))
        check_service(f"http://127.0.0.1:{port}")
    except (OSError, ValueError, TypeError) as exc:
        print(f"Readiness check failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
