"""FastAPI 应用：HTTP 接口 + WebSocket 端点 + 前端静态资源。"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import time
from functools import partial
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .. import __version__, protocol
from ..config import Settings
from .hub import GameHub
from .websocket import handle_connection

logger = logging.getLogger("neonovaclash.app")


def frontend_fingerprint(frontend_dir: Path) -> str:
    """前端资源的内容指纹：文件一改指纹就变，浏览器与 CDN 就不会继续用旧 JS/CSS。

    比"发布前记得手动改版本号"可靠得多 —— 忘记改版本号是这类问题的常见来源。
    """

    digest = hashlib.sha256()
    if not frontend_dir.exists():
        return "dev"
    for path in sorted(frontend_dir.rglob("*")):
        if path.is_file() and path.suffix in {".js", ".css", ".html"}:
            digest.update(path.name.encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()[:10]


_FINGERPRINT_TTL_SECONDS = 2.0
_fingerprint_cache: dict[str, tuple[float, str]] = {}


def cached_frontend_fingerprint(frontend_dir: Path) -> str:
    """带 2 秒缓存的指纹。

    开发时改完前端直接刷新就能拿到新资源，不用重启服务；
    生产环境每个请求最多多一次目录遍历，代价可以忽略。
    """

    key = str(frontend_dir)
    now = time.monotonic()
    hit = _fingerprint_cache.get(key)
    if hit is not None and now - hit[0] < _FINGERPRINT_TTL_SECONDS:
        return hit[1]
    value = frontend_fingerprint(frontend_dir)
    _fingerprint_cache[key] = (now, value)
    return value


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    hub = GameHub(settings)
    # 不再在启动时算死：前端一改，两秒内刷新就能拿到新资源（见 cached_frontend_fingerprint）
    asset_version = partial(cached_frontend_fingerprint, settings.frontend_dir)

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

    @app.middleware("http")
    async def frontend_revalidate(request, call_next):
        """前端资源一律回源校验：否则发版后浏览器（以及 Cloudflare 边缘）会继续用旧的 JS/CSS。"""

        response = await call_next(request)
        # 基础安全响应头：这是个纯前端 + API 的服务，不需要被 iframe 嵌套
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """兜底异常处理：不要让玩家看到一堆堆栈，同时把细节写进日志。"""

        logger.exception("处理 %s 时出现未捕获异常", request.url.path)
        return JSONResponse(
            {"error": "服务器内部错误", "path": request.url.path, "version": __version__},
            status_code=500,
        )

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
            # 诊断用：匹配队列长度 + 每个房间的座位状态（只看连接/提交，不含昵称与 token）
            "queue": hub.queue_size(),
            "room_list": [room.diagnostic_payload() for room in hub.rooms.values()],
            "totals": {
                "rooms_created": hub.stats.created,
                "rooms_closed": hub.stats.closed,
                "battles_played": hub.stats.battles,
            },
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

    def render_page(filename: str) -> Response:
        """返回一个独立页面，并把 {{ASSET_VERSION}} 换成当前前端指纹。

        介绍页、规则页与游戏主页面共用同一套版本注入，
        改前端后连独立页面也不会吃到缓存里的旧 CSS/JS。
        """

        page = frontend_dir / filename
        if not page.exists():
            return JSONResponse({"message": "页面缺失", "version": __version__}, status_code=404)
        html = page.read_text(encoding="utf-8").replace("{{ASSET_VERSION}}", asset_version())
        return HTMLResponse(html)

    @app.get("/", include_in_schema=False)
    async def index() -> Response:
        return render_page("index.html")

    @app.get("/about", include_in_schema=False)
    async def about_page() -> Response:
        """项目介绍页：讲清楚这是什么、怎么玩、怎么做的。"""

        return render_page("about.html")

    @app.get("/rules", include_in_schema=False)
    async def rules_page() -> Response:
        """规则介绍页：数据全部来自 /api/rules，保证与版本同步。"""

        return render_page("rules.html")

    if frontend_dir.exists():
        app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

    return app


app = create_app()
