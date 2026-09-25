"""启动入口：本地开发与容器部署都从这里开始。

用法：
    python run.py                  # 读取环境变量并使用默认端口 8000
    uvicorn backend.web.app:app     # 等价方式，便于接入进程管理器
"""

from __future__ import annotations

import logging

import uvicorn

from backend import __version__
from backend.config import Settings, configure_logging
from backend.web.app import create_app

app = create_app()


def main() -> None:
    settings = Settings.from_env()
    # 先把应用日志配好（格式统一、级别来自 NC_LOG_LEVEL），再交给 uvicorn：
    # uvicorn 只配置自己的 logger，不配这一棵树，配晚了启动日志就丢了。
    configure_logging(settings.log_level)
    logging.getLogger("neonovaclash").info(
        "NeoNovaClash v%s 启动中：%s:%s（备战 %s 秒，房间上限 %s）",
        __version__,
        settings.host,
        settings.port,
        settings.prepare_timeout,
        settings.max_rooms,
    )
    uvicorn.run(app, host=settings.host, port=settings.port, log_level=settings.log_level)


if __name__ == "__main__":
    main()
