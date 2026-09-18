"""启动入口：本地开发与容器部署都从这里开始。

用法：
    python run.py                  # 读取环境变量并使用默认端口 8000
    uvicorn backend.web.app:app     # 等价方式，便于接入进程管理器
"""

from __future__ import annotations

import uvicorn

from backend.config import Settings
from backend.web.app import create_app

app = create_app()


def main() -> None:
    settings = Settings.from_env()
    print(f"星核竞技场已启动 → http://127.0.0.1:{settings.port}")
    uvicorn.run(app, host=settings.host, port=settings.port, log_level=settings.log_level)


if __name__ == "__main__":
    main()
