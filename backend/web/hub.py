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
from starlette.websockets import WebSocketState

from ..config import Settings
from ..core import constants as C
from ..core import modes
from ..core.room import Outgoing, Phase, Room, RoomError

logger = logging.getLogger("neonovaclash.hub")

ROOM_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 去掉易混淆的 I/O/0/1


def is_socket_alive(socket: WebSocket) -> bool:
    """这条 WebSocket 是否还处于已连接状态。

    浏览器被直接关掉、电脑睡眠、网络断开时，服务端的处理协程可能还阻塞在 receive 上，
    拿不到断开事件；这时 client_state 仍然会是 CONNECTED（要等 uvicorn 的心跳超时才会翻），
    所以它只能当作"第一道过滤"，后面还有队列 TTL 兜底。
    """

    state = getattr(socket, "client_state", None)
    if state is None:  # 拿不到状态就不拦，交给 TTL
        return True
    return state is WebSocketState.CONNECTED


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

    # ------------------------------------------------------------ 运维日志
    # 关键事件一律打成 `key=value` 的单行结构化日志，方便直接 grep：
    #   昨天有多少人开房：  grep room_created   app.log | wc -l
    #   昨天打完几局：      grep match_over     app.log | wc -l
    #   人是从哪一步走的：  grep room_closed    app.log
    # 日志只带昵称与房间号，不带 token 或任何私有对局数据。
    def _room_summary(self, room: Room) -> str:
        room_code = getattr(room, "code", "?")
        return f"room={room_code} mode={room.mode.key}"

    def _player_summary(self, room: Room) -> str:
        try:
            return " vs ".join(f"{p.name}{'(电脑)' if p.is_bot else ''}" for p in room.players)
        except Exception:  # pragma: no cover - 日志不该反过来影响主流程
            return "?"

    def log_room_created(self, room: Room, *, source: str) -> None:
        """公开的房间创建日志入口（建房 / 练习模式由 ws 层调用，匹配由 log_matched 调用）。"""

        self._log_room_created(room, source=source)

    def _log_room_created(self, room: Room, *, source: str) -> None:
        """房间一建出来就记一笔（这时还没有玩家，所以只带来源与模式）。

        玩家昵称在 `battle_start` / `match_over` / `room_closed` 里带上，
        因为创建和"第一人坐下"是两件事，硬凑在一行只会得到空的名字。
        """

        logger.info(
            "room_created %s source=%s prepare_timeout=%ss",
            self._room_summary(room),
            source,
            room.prepare_timeout,
        )

    def _log_outgoing_events(self, room: Room, outgoings: list[Outgoing]) -> None:
        """把房间产出的关键消息翻译成运维日志（战斗开始/结束、整场结束、房间关闭）。"""

        for outgoing in outgoings:
            kind = outgoing.message_type()
            if kind == "battle_report":
                result = outgoing.payload.get("result") or {}
                logger.info(
                    "battle_start %s round=%s seed=%s players=%s",
                    self._room_summary(room),
                    outgoing.payload.get("round_index"),
                    result.get("seed"),
                    self._player_summary(room),
                )
            elif kind == "game_over":
                score = outgoing.payload.get("score") or []
                history = outgoing.payload.get("history") or []
                logger.info(
                    "match_over %s winner=%s score=%s rounds=%s players=%s",
                    self._room_summary(room),
                    outgoing.payload.get("winner_seat"),
                    " ".join(str(s) for s in score),
                    len(history),
                    self._player_summary(room),
                )
            # room_closed 统一由 _log_room_removed 记录（那条路径覆盖所有关房方式）

    def _log_room_removed(self, room: Room) -> None:
        """房间离开注册表时记录一次（`leave` / 掉线超时 / 空闲回收都会走到这里）。"""

        if room.close_logged:
            return
        room.close_logged = True
        logger.info(
            "room_closed %s reason=%s rounds_played=%s",
            self._room_summary(room),
            room.closed_reason or "未说明",
            len(room.history),
        )

    async def dispatch(self, room: Room, outgoings: list[Outgoing]) -> None:
        """把房间产生的消息投递给对应玩家。"""

        if not outgoings:
            return
        self._log_outgoing_events(room, outgoings)
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
        self._log_room_removed(room)

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
    def log_matchmaking_waiting(self, name: str, mode_key: str, queue_size: int) -> None:
        logger.info(
            "matchmaking_waiting player=%s mode=%s queue_size=%s",
            name,
            mode_key,
            queue_size,
        )

    def log_matched(self, room: Room, first: str, second: str, wait_seconds: float) -> None:
        self._log_room_created(room, source="matchmaking")
        logger.info(
            "matchmaking_matched %s pair=%s vs %s waited=%ss",
            self._room_summary(room),
            first,
            second,
            round(wait_seconds, 1),
        )

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

    def queue_diagnostics(self, now: float | None = None) -> list[dict]:
        """匹配队列快照（诊断用）：只有模式和已等待时长，不含昵称与 token。"""

        now = time.monotonic() if now is None else now
        return [
            {"mode": item.mode_key, "waiting_seconds": round(now - item.since, 1)}
            for item in self.queue
        ]

    def take_opponent(
        self, mode_key: str = modes.DEFAULT_MODE_KEY, exclude: Session | None = None
    ) -> QueuedPlayer | None:
        """取出仍在等待、且模式相同的玩家（不同模式之间不会互相匹配）。

        这里必须确认对方**连接还活着**：浏览器被直接关掉、电脑睡眠、网络断开时，
        服务端可能还没收到断开事件，队列里就会留下一个"幽灵"。
        如果把它当成对手，玩家会瞬间匹配成功、然后对着一个永远不动的人打完整场。

        `exclude` 用来排除"自己"：即使调用方忘了先把自己出队，
        也绝不允许同一条连接占两个座位自己跟自己打。
        """

        while self.queue:
            index = next((i for i, item in enumerate(self.queue) if item.mode_key == mode_key), None)
            if index is None:
                return None
            queued = self.queue.pop(index)
            if exclude is not None and queued.session is exclude:
                continue
            if queued.session.in_room:
                continue
            if not is_socket_alive(queued.websocket):
                logger.info("匹配队列里丢掉一条已断开的连接（%s）", queued.name)
                continue
            return queued
        return None

    def reap_stale_queue(self, now: float | None = None) -> list[QueuedPlayer]:
        """清掉排队太久或连接已死的队列项，避免它们继续被配对给真人。"""

        now = time.monotonic() if now is None else now
        dropped: list[QueuedPlayer] = []
        keep: list[QueuedPlayer] = []
        for item in self.queue:
            expired = now - item.since > C.MATCHMAKING_QUEUE_TTL_SECONDS
            if expired or not is_socket_alive(item.websocket):
                dropped.append(item)
            else:
                keep.append(item)
        self.queue = keep
        return dropped

    # ------------------------------------------------------------ 后台任务
    async def _notify_queue_dropped(self, queued: QueuedPlayer) -> None:
        """队列项被清掉时尽量告诉本人（连不上就算了）。"""

        if not is_socket_alive(queued.websocket):
            logger.info("清理匹配队列中的失效连接：%s", queued.name)
            return
        try:
            await queued.websocket.send_json(
                {"type": "matchmaking_cancelled", "reason": "排队超时，已自动取消匹配"}
            )
        except Exception:  # pragma: no cover - 连接刚好断掉
            logger.debug("通知匹配超时失败", exc_info=True)
        logger.info("匹配排队超时，已移除：%s", queued.name)

    async def ticker(self, interval: float = 1.0) -> None:
        """每秒推进一次：超时自动提交 + 回收空闲房间。"""

        while True:
            await asyncio.sleep(interval)
            now = time.monotonic()
            for dropped in self.reap_stale_queue(now):
                # 还在排队但连接已死 / 排太久的，直接清掉，别再配对给真人
                await self._notify_queue_dropped(dropped)
            for room in list(self.rooms.values()):
                try:
                    outgoings = room.tick(now)
                    if outgoings:
                        if any(o.message_type() == "battle_report" for o in outgoings):
                            self.stats.battles += 1
                        await self.dispatch(room, outgoings)
                    # 房间被 tick 关掉（比如掉线超过宽限期）时没走 dispatch，这里补一次日志
                    if room.is_closed:
                        self._log_room_removed(room)
                    if room.is_closed or self._should_reap(room, now):
                        if not room.is_closed:
                            await self.dispatch(
                                room, room.close("房间长时间无活动，已自动关闭")
                            )
                        self.drop_room(room)
                        self._log_room_removed(room)
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
