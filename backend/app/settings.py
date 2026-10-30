from dataclasses import dataclass
from pathlib import Path
import os
from dotenv import dotenv_values, load_dotenv

APP_ROOT = Path(__file__).resolve().parents[1]
# Source checkout: <repo>/backend/app; container: /app/app.
REPO_ROOT = APP_ROOT.parent if APP_ROOT.name == "backend" else APP_ROOT
load_dotenv(REPO_ROOT / ".env", override=False)
load_dotenv(APP_ROOT / ".env", override=False)


def _server_ak() -> str:
    if "BAIDU_SERVER_AK" in os.environ:
        return os.environ["BAIDU_SERVER_AK"].strip()
    # Read only the server key. Do not load browser variables into the server
    # environment or expose this value in health responses/logs/cache entries.
    for path in (REPO_ROOT / ".env", APP_ROOT / ".env"):
        if path.is_file():
            values = dotenv_values(path, interpolate=False)
            value = values.get("BAIDU_SERVER_AK")
            if value:
                return value.strip()
    return ""


def _repo_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


REGION_ID = os.getenv("REGION_ID", "shanghai-new-jiangwan")
if not REGION_ID or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in REGION_ID):
    raise ValueError("REGION_ID must be a region package identifier")
REGION_ROOT = REPO_ROOT / "data/regions" / REGION_ID
SYNTHETIC_NETWORK_PATH = _repo_path(os.getenv(
    "SYNTHETIC_NETWORK_PATH", str(REGION_ROOT / "network.json")
))


@dataclass(frozen=True)
class Settings:
    region_id: str = REGION_ID
    region_storage_backend: str = os.getenv("REGION_STORAGE_BACKEND", "file")
    app_env: str = os.getenv("APP_ENV", "development")
    local_container_editor: bool = os.getenv(
        "LOCAL_CONTAINER_EDITOR", "false").strip().lower() in ("true", "1")
    cpp_engine_path: Path = Path(
        os.getenv("CPP_ENGINE_PATH", "cpp-engine/build/isochrone_engine")
    )
    cpp_engine_transport: str = os.getenv("CPP_ENGINE_TRANSPORT", "subprocess")
    cpp_engine_rpc_port: int = int(os.getenv("CPP_ENGINE_RPC_PORT", "50051"))
    cpp_engine_rpc_capacity: int = int(os.getenv("CPP_ENGINE_RPC_CAPACITY", "2"))
    analysis_cache_dir: Path = Path(
        os.getenv("ANALYSIS_CACHE_DIR", str(REPO_ROOT / "data/cache"))
    )
    walking_network_path: Path = Path(
        os.getenv("WALKING_NETWORK_PATH", "data/networks/shanghai-new-jiangwan.json")
    )
    synthetic_network_path: Path = SYNTHETIC_NETWORK_PATH
    local_experiment_network_path: Path = _repo_path(
        os.getenv("LOCAL_EXPERIMENT_NETWORK_PATH", str(SYNTHETIC_NETWORK_PATH))
    )
    baidu_server_ak: str = _server_ak()
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
    baidu_max_qps: float = float(os.getenv("BAIDU_MAX_QPS", "1"))
    baidu_max_retries: int = int(
    os.getenv("BAIDU_MAX_RETRIES", "2")
    )
    cache_ttl_hours: int = int(
        os.getenv("CACHE_TTL_HOURS", "24")
    )
    poi_cache_path: Path = Path(os.getenv(
        "POI_CACHE_PATH", str(REPO_ROOT / "data/cache/baidu-pois.sqlite3")))
    poi_cache_ttl_hours: int = int(os.getenv("POI_CACHE_TTL_HOURS", "168"))
    poi_cache_stale_hours: int = int(os.getenv("POI_CACHE_STALE_HOURS", "720"))
    poi_empty_cache_ttl_hours: int = int(os.getenv("POI_EMPTY_CACHE_TTL_HOURS", "24"))
    poi_max_pages: int = int(os.getenv("POI_MAX_PAGES", "8"))
    poi_budget_seconds: float = float(os.getenv("POI_BUDGET_SECONDS", "12"))
    poi_tile_meters: int = int(os.getenv("POI_TILE_METERS", "1000"))
    poi_max_tiles: int = int(os.getenv("POI_MAX_TILES", "64"))
    poi_max_requests: int = int(os.getenv("POI_MAX_REQUESTS", "96"))
    poi_snap_meters: float = float(os.getenv("POI_SNAP_METERS", "3"))
    poi_map_asset_path: Path = _repo_path(os.getenv(
        "POI_MAP_ASSET_PATH", str(REGION_ROOT / "alignment.bd09.json")))

    def __post_init__(self) -> None:
        if self.region_storage_backend not in ("file", "mysql"):
            raise ValueError("REGION_STORAGE_BACKEND must be file or mysql")
        if self.cpp_engine_transport not in ("subprocess", "grpc"):
            raise ValueError("CPP_ENGINE_TRANSPORT must be subprocess or grpc")
        if type(self.cpp_engine_rpc_port) is not int or not 1 <= self.cpp_engine_rpc_port <= 65535:
            raise ValueError("CPP_ENGINE_RPC_PORT must be an integer from 1 to 65535")
        if (type(self.cpp_engine_rpc_capacity) is not int or
                not 1 <= self.cpp_engine_rpc_capacity <= 2_147_483_647):
            raise ValueError("CPP_ENGINE_RPC_CAPACITY must be an integer from 1 to 2147483647")

    @property
    def network_editor_enabled(self) -> bool:
        # Container opt-in only affects local request checks. It cannot enable
        # authoring endpoints when the application runs in production.
        # Database releases are immutable; author with a separate file-mode app.
        return self.app_env == "development" and self.region_storage_backend == "file"


settings = Settings()
