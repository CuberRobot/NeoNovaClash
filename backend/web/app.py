"""FastAPI 应用：HTTP 接口 + WebSocket 端点 + 前端静态资源。"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__, protocol
from ..config import Settings
from .hub import GameHub
from .websocket import handle_connection

logger = logging.getLogger("neonovaclash.app")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    hub = GameHub(settings)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(hub.ticker())
        logger.info("NeoNovaClash 已启动：准备时长 %s 秒", settings.prepare_timeout)
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(
        title="NeoNovaClash",
        version=__version__,
        description="星陨竞技场：双人战前部署、自动演算的回合制对战服务",
        lifespan=lifespan,
    )
    app.state.hub = hub
    app.state.settings = settings

    # ------------------------------------------------------------ HTTP 接口
    @app.get("/api/health", tags=["system"])
    async def health() -> dict:
        return {
            "status": "ok",
            "version": __version__,
            **protocol.stats_payload(
                rooms=len(hub.rooms),
                players=hub.active_players(),
                uptime=hub.uptime(),
            ),
        }

    @app.get("/api/version", tags=["system"])
    async def version() -> dict:
        return protocol.version_payload()

    @app.get("/api/rules", tags=["game"])
    async def rules() -> dict:
        return protocol.rules_payload()

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await handle_connection(websocket, hub)

    # ------------------------------------------------------------ 前端资源
    frontend_dir: Path = settings.frontend_dir

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        index_file = frontend_dir / "index.html"
        if not index_file.exists():
            return JSONResponse({"message": "前端资源缺失", "version": __version__}, status_code=500)
        return FileResponse(index_file)

    if frontend_dir.exists():
        app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

    return app


app = create_app()
