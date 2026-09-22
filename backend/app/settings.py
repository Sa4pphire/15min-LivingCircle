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
    baidu_server_ak: str = os.getenv("BAIDU_SERVER_AK", "")


settings = Settings()
