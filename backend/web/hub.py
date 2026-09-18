"""房间注册表与连接管理。

职责边界：房间规则在 backend/core/room.py，这里只负责「房间号的分配/回收」
以及「把房间产生的消息投递到对应的 WebSocket」。
"""

from __future__ import annotations

import asyncio
import logging
import random
import secrets
import time
from dataclasses import dataclass

from fastapi import WebSocket

from ..config import Settings
from ..core import constants as C
from ..core import modes
from ..core.room import Outgoing, Phase, Room, RoomError

logger = logging.getLogger("neonovaclash.hub")

ROOM_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 去掉易混淆的 I/O/0/1


@dataclass
class Session:
    """一条 WebSocket 连接当前所在的房间与座位。"""

    room_code: str | None = None
    seat: int | None = None
    name: str | None = None

    @property
    def in_room(self) -> bool:
        return self.room_code is not None and self.seat is not None

    def leave(self) -> None:
        self.room_code = None
        self.seat = None


@dataclass
class RoomStats:
    created: int = 0
    closed: int = 0
    battles: int = 0
    matched: int = 0


@dataclass
class QueuedPlayer:
    """在随机匹配队列里等待的玩家。"""

    session: Session
    name: str
    since: float
    websocket: WebSocket
    mode_key: str = modes.DEFAULT_MODE_KEY


class GameHub:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.rooms: dict[str, Room] = {}
        self.connections: dict[str, dict[int, WebSocket]] = {}
        self.stats = RoomStats()
        self.queue: list[QueuedPlayer] = []
        self.started_at = time.monotonic()
        self._rng = random.Random()

    # ------------------------------------------------------------ 房间生命周期
    def create_room(self, mode: modes.ModeConfig | str = modes.DEFAULT_MODE_KEY) -> Room:
        if len(self.rooms) >= self.settings.max_rooms:
            raise RoomError("服务器房间已满，请稍后再试")
        code = self._new_code()
        room = Room(
            code,
            prepare_timeout=self.settings.prepare_timeout,
            reconnect_grace=self.settings.reconnect_grace,
            mode=mode,
        )
        self.rooms[code] = room
        self.connections[code] = {}
        self.stats.created += 1
        return room

    def _new_code(self) -> str:
        for _ in range(200):
            code = "".join(secrets.choice(ROOM_CODE_ALPHABET) for _ in range(C.ROOM_CODE_LENGTH))
            if code not in self.rooms:
                return code
        raise RoomError("房间号分配失败，请重试")

    def get_room(self, code: str) -> Room:
        room = self.rooms.get(code.upper())
        if room is None or room.is_closed:
            raise RoomError("房间不存在或已关闭，请确认房间号")
        return room

    def bind(self, room_code: str, seat: int, websocket: WebSocket) -> None:
        self.connections.setdefault(room_code, {})[seat] = websocket

    def unbind(self, room_code: str, seat: int) -> None:
        self.connections.get(room_code, {}).pop(seat, None)

    def drop_room(self, room: Room) -> None:
        self.rooms.pop(room.code, None)
        self.connections.pop(room.code, None)
        self.stats.closed += 1

    async def dispatch(self, room: Room, outgoings: list[Outgoing]) -> None:
        """把房间产生的消息投递给对应玩家。"""

        if not outgoings:
            return
        sockets = self.connections.get(room.code, {})
        for outgoing in outgoings:
            if outgoing.seat is None:
                targets = list(sockets.values())
            else:
                socket = sockets.get(outgoing.seat)
                targets = [socket] if socket is not None else []
            for socket in targets:
                try:
                    await socket.send_json(outgoing.payload)
                except Exception:  # pragma: no cover - 连接已断开时忽略
                    logger.debug("向房间 %s 投递消息失败", room.code, exc_info=True)

    async def leave(self, session: Session, reason: str) -> None:
        """玩家主动退出：立即关闭房间并通知对手。"""

        if not session.in_room:
            return
        room = self.rooms.get(session.room_code)
        seat = session.seat
        session.leave()
        if room is None or room.is_closed:
            return
        outgoings = room.close(f"{room.player_of(seat).name} 已离开，房间关闭")
        await self.dispatch(room, outgoings)
        self.drop_room(room)

    async def handle_disconnect(self, session: Session, websocket: WebSocket | None = None) -> None:
        """连接断开：先给重连宽限，房间保持存活；匹配队列里的玩家直接移除。

        注意 websocket 参数：如果这个座位已经绑定了更新的连接（玩家重连之后旧连接才断开），
        就不要把这个已经回来的玩家误标成掉线。
        """

        self.dequeue(session)
        if not session.in_room:
            return
        room = self.rooms.get(session.room_code)
        seat = session.seat
        session.leave()
        if room is None or room.is_closed:
            return
        current = self.connections.get(room.code, {}).get(seat)
        if websocket is not None and current is not None and current is not websocket:
            return
        self.unbind(room.code, seat)
        outgoings = room.disconnect(seat)
        await self.dispatch(room, outgoings)

    # ------------------------------------------------------------ 随机匹配
    def enqueue(
        self,
        session: Session,
        name: str,
        websocket: WebSocket,
        mode_key: str = modes.DEFAULT_MODE_KEY,
    ) -> int:
        """把玩家放进匹配队列，返回该模式下排队人数（含自己）。"""

        self.dequeue(session)
        same_mode = [item for item in self.queue if item.mode_key == mode_key]
        if len(same_mode) >= C.MATCHMAKING_QUEUE_LIMIT:
            raise RoomError("匹配队列已满，请稍后再试")
        self.queue.append(
            QueuedPlayer(
                session=session,
                name=name,
                since=time.monotonic(),
                websocket=websocket,
                mode_key=mode_key,
            )
        )
        return len(same_mode) + 1

    def dequeue(self, session: Session) -> bool:
        before = len(self.queue)
        self.queue = [item for item in self.queue if item.session is not session]
        return len(self.queue) != before

    def queue_size(self) -> int:
        return len(self.queue)

    def take_opponent(self, mode_key: str = modes.DEFAULT_MODE_KEY) -> QueuedPlayer | None:
        """取出仍在等待、且模式相同的玩家（不同模式之间不会互相匹配）。"""

        while self.queue:
            index = next((i for i, item in enumerate(self.queue) if item.mode_key == mode_key), None)
            if index is None:
                return None
            queued = self.queue.pop(index)
            if queued.session.in_room:
                continue
            return queued
        return None

    # ------------------------------------------------------------ 后台任务
    async def ticker(self, interval: float = 1.0) -> None:
        """每秒推进一次：超时自动提交 + 回收空闲房间。"""

        while True:
            await asyncio.sleep(interval)
            now = time.monotonic()
            for room in list(self.rooms.values()):
                try:
                    outgoings = room.tick(now)
                    if outgoings:
                        if any(o.payload.get("type") == "battle_report" for o in outgoings):
                            self.stats.battles += 1
                        await self.dispatch(room, outgoings)
                    if room.is_closed or self._should_reap(room, now):
                        await self.dispatch(room, room.close("房间长时间无活动，已自动关闭"))
                        self.drop_room(room)
                except Exception:  # pragma: no cover - 单个房间异常不应影响整体
                    logger.exception("房间 %s 推进失败", room.code)

    def _should_reap(self, room: Room, now: float) -> bool:
        if room.phase is Phase.WAITING and not room.players:
            return now - room.created_at > 300
        if room.phase in (Phase.WAITING, Phase.FINISHED):
            return now - room.last_activity > self.settings.room_ttl
        return False

    # ------------------------------------------------------------ 统计
    def active_players(self) -> int:
        return sum(len(room.players) for room in self.rooms.values() if not room.is_closed)

    def uptime(self) -> float:
        return time.monotonic() - self.started_at
