"""运行配置：全部来自环境变量，便于本地、容器与线上使用同一份代码。"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FRONTEND_DIR = PROJECT_ROOT / "frontend"

# 应用自己的日志命名空间：configure_logging 只碰这一棵子树以及根 logger 的兜底输出。
LOGGER_NAME = "neonovaclash"
# 日志格式：时间 + 级别 + 模块 + 消息。结构化事件日志的字段直接拼在消息里（key=value），
# 这样 `grep`、`awk`、`journalctl` 都能直接用，不需要额外的 JSON 解析。
LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


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
    prepare_timeout: int = 90
    reconnect_grace: int = 90
    room_ttl: int = 1800
    max_rooms: int = 500
    log_level: str = "info"
    frontend_dir: Path = DEFAULT_FRONTEND_DIR

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            host=os.getenv("NC_HOST", "0.0.0.0"),
            port=_env_int("NC_PORT", 8000),
            prepare_timeout=_env_int("NC_PREPARE_TIMEOUT", 90),
            reconnect_grace=_env_int("NC_RECONNECT_GRACE", 90),
            room_ttl=_env_int("NC_ROOM_TTL", 1800),
            max_rooms=_env_int("NC_MAX_ROOMS", 500),
            log_level=os.getenv("NC_LOG_LEVEL", "info").lower(),
        )


def configure_logging(log_level: str = "info", *, force: bool = False) -> None:
    """配置应用日志输出。

    为什么需要它：uvicorn 只配置自己的 logger，应用里的 `logging.getLogger("neonovaclash.*")`
    没有 handler，日志会走 logging 的 `lastResort` 直接打到 stderr，格式不可控，而且
    部署时如果把 stderr 丢掉（systemd 没配 StandardOutput 就是这样）就真的什么都不剩。

    这里给 neonovaclash 子树和根 logger 各挂一个 handler（缺了才挂，不覆盖已有配置），
    并关掉子树向根的冒泡，避免在 uvicorn / systemd 下被打印两遍；uvicorn 自己的 logger
    有独立 handler，不受影响。

    `force=True` 用于测试：pytest 的 logging 插件会把根 logger 抬到 WARNING，
    导致 INFO 级别的应用日志被"有效级别"过滤掉，此时需要把级别重新压回 INFO。
    """

    level = getattr(logging, log_level.upper(), logging.INFO)
    formatter = logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT)
    for name in (LOGGER_NAME, None):  # None = 根 logger，兜住第三方库的 warning/error
        target = logging.getLogger(name)
        if force or target.level != level:
            target.setLevel(level)
        if not target.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(formatter)
            target.addHandler(handler)
    # 应用日志不再向根 logger 冒泡：它有自己的 handler，冒泡只会重复打印
    logging.getLogger(LOGGER_NAME).propagate = False
