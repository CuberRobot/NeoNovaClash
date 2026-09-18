"""运行配置：全部来自环境变量，便于本地、容器与线上使用同一份代码。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FRONTEND_DIR = PROJECT_ROOT / "frontend"


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """服务运行参数。"""

    host: str = "0.0.0.0"
    port: int = 8000
    prepare_timeout: int = 60
    room_ttl: int = 1800
    max_rooms: int = 500
    log_level: str = "info"
    frontend_dir: Path = DEFAULT_FRONTEND_DIR

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            host=os.getenv("NC_HOST", "0.0.0.0"),
            port=_env_int("NC_PORT", 8000),
            prepare_timeout=_env_int("NC_PREPARE_TIMEOUT", 60),
            room_ttl=_env_int("NC_ROOM_TTL", 1800),
            max_rooms=_env_int("NC_MAX_ROOMS", 500),
            log_level=os.getenv("NC_LOG_LEVEL", "info").lower(),
        )
