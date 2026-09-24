from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(frozen=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "development")
    static_dir: Path = Path(os.getenv("STATIC_DIR", "static"))
    cpp_engine_path: Path = Path(
        os.getenv("CPP_ENGINE_PATH", "cpp-engine/build/isochrone_engine")
    )
    analysis_cache_dir: Path = Path(
        os.getenv("ANALYSIS_CACHE_DIR", "data/cache")
    )
    walking_network_path: Path = Path(
        os.getenv("WALKING_NETWORK_PATH", "data/networks/shanghai-new-jiangwan.json")
    )
    baidu_server_ak: str = os.getenv("BAIDU_SERVER_AK", "")
    baidu_api_base_url: str = os.getenv(
    "BAIDU_API_BASE_URL",
    "https://api.map.baidu.com",
    )
    baidu_timeout_seconds: float = float(
    os.getenv("BAIDU_TIMEOUT_SECONDS", "10")
    )
    baidu_max_concurrency: int = int(
    os.getenv("BAIDU_MAX_CONCURRENCY", "2")
    )
    baidu_max_retries: int = int(
    os.getenv("BAIDU_MAX_RETRIES", "2")
    )
    cache_ttl_hours: int = int(
    os.getenv("CACHE_TTL_HOURS", "24")
    )


settings = Settings()
