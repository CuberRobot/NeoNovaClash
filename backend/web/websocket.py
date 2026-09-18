"""WebSocket 端点：把协议消息翻译成房间操作，再把结果投递回去。"""

from __future__ import annotations

import logging
import time

from fastapi import WebSocket, WebSocketDisconnect

from .. import protocol
from ..core.room import RoomError
from ..core.rules import RuleError
from .hub import GameHub, Session

logger = logging.getLogger("neonovaclash.ws")

# 每个连接的消息频率限制：正常玩家一秒最多几条，超过就是在刷接口
RATE_WINDOW_SECONDS = 1.0
RATE_MAX_MESSAGES = 25
# 连续超限多少次就断开连接
RATE_STRIKE_LIMIT = 5


class RateLimiter:
    """滑动窗口限流：防止单个连接高频刷消息把服务打满。"""

    def __init__(self, window: float = RATE_WINDOW_SECONDS, limit: int = RATE_MAX_MESSAGES) -> None:
        self.window = window
        self.limit = limit
        self.timestamps: list[float] = []
        self.strikes = 0

    def allow(self) -> bool:
        now = time.monotonic()
        self.timestamps = [t for t in self.timestamps if now - t < self.window]
        if len(self.timestamps) >= self.limit:
            self.strikes += 1
            return False
        self.strikes = 0
        self.timestamps.append(now)
        return True


async def handle_connection(websocket: WebSocket, hub: GameHub) -> None:
    await websocket.accept()
    session = Session()
    limiter = RateLimiter()
    await websocket.send_json(protocol.hello())
    try:
        while True:
            raw = await websocket.receive_text()
            if not limiter.allow():
                await websocket.send_json(protocol.error("操作过于频繁，请稍后再试"))
                if limiter.strikes >= RATE_STRIKE_LIMIT:
                    await websocket.close(code=1008, reason="rate limited")
                    break
                continue
            await _handle_raw_message(websocket, hub, session, raw)
    except WebSocketDisconnect:
        logger.debug("连接断开 seat=%s room=%s", session.seat, session.room_code)
    except Exception:  # pragma: no cover - 兜底，避免整条连接静默失败
        logger.exception("处理连接时发生异常")
    finally:
        await hub.leave(session, reason="连接断开")


async def _handle_raw_message(websocket: WebSocket, hub: GameHub, session: Session, raw: str) -> None:
    try:
        message = protocol.parse_message(raw)
    except protocol.ProtocolError as exc:
        await websocket.send_json(protocol.error(str(exc), field=exc.field))
        return

    handler = {
        "create_room": _create_room,
        "join_room": _join_room,
        "submit_plan": _submit_plan,
        "rematch": _rematch,
        "leave_room": _leave_room,
        "ping": _ping,
    }[message.type]
    try:
        await handler(websocket, hub, session, message)
    except Exception:  # pragma: no cover - 兜底，避免一条异常消息让玩家端静默卡住
        logger.exception("处理 %s 消息失败", message.type)
        await websocket.send_json(protocol.error("服务器处理这条指令时出错，请重试或刷新页面"))


async def _create_room(websocket: WebSocket, hub: GameHub, session: Session, message) -> None:
    if session.in_room:
        await hub.leave(session, reason="重新创建房间")
    try:
        room = hub.create_room()
        player, outgoings = room.join(message.name)
        hub.bind(room.code, player.seat, websocket)
        session.room_code, session.seat, session.name = room.code, player.seat, player.name
        await hub.dispatch(room, outgoings)
    except RoomError as exc:
        await websocket.send_json(protocol.error(str(exc), fatal=True))


async def _join_room(websocket: WebSocket, hub: GameHub, session: Session, message) -> None:
    if session.in_room:
        await hub.leave(session, reason="加入新房间")
    try:
        room = hub.get_room(message.room_code)
        player, outgoings = room.join(message.name)
        hub.bind(room.code, player.seat, websocket)
        session.room_code, session.seat, session.name = room.code, player.seat, player.name
        await hub.dispatch(room, outgoings)
    except RoomError as exc:
        await websocket.send_json(protocol.error(str(exc), fatal=True))


async def _submit_plan(websocket: WebSocket, hub: GameHub, session: Session, message) -> None:
    room = _current_room(hub, session)
    if room is None:
        await websocket.send_json(protocol.error("请先创建或加入房间"))
        return
    plan = protocol.plan_from_message(message)
    try:
        outgoings = room.submit(session.seat, plan)
    except RuleError as exc:
        await websocket.send_json(protocol.error(str(exc), field=exc.field))
        return
    except RoomError as exc:
        await websocket.send_json(protocol.error(str(exc)))
        return
    await hub.dispatch(room, outgoings)


async def _rematch(websocket: WebSocket, hub: GameHub, session: Session, message) -> None:
    room = _current_room(hub, session)
    if room is None:
        await websocket.send_json(protocol.error("请先创建或加入房间"))
        return
    try:
        outgoings = room.request_rematch(session.seat)
    except RoomError as exc:
        await websocket.send_json(protocol.error(str(exc)))
        return
    await hub.dispatch(room, outgoings)


async def _leave_room(websocket: WebSocket, hub: GameHub, session: Session, message) -> None:
    if not session.in_room:
        await websocket.send_json(protocol.error("你当前不在任何房间中"))
        return
    await hub.leave(session, reason="主动退出")
    await websocket.send_json({"type": "left_room"})


async def _ping(websocket: WebSocket, hub: GameHub, session: Session, message) -> None:
    await websocket.send_json(protocol.pong())


def _current_room(hub: GameHub, session: Session):
    if not session.in_room:
        return None
    room = hub.rooms.get(session.room_code)
    if room is None or room.is_closed:
        session.leave()
        return None
    return room
