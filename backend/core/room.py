"""房间与对局流程状态机。

这一层不依赖任何 Web 框架：它把「发生了什么」变成一串待发送的消息（Outgoing），
由 WebSocket 层负责投递。这样房间逻辑可以脱离网络直接测试，
也方便日后接入命令行、机器人或其它前端。

状态机：
    WAITING ──两人到齐──> PREPARING ──双方提交/超时──> BATTLING
        ^                      ^                            │
        │                      └──── 未分出胜负，开下一局 ───┤
        │                                                   v
        └────────────── CLOSED <── 掉线/退出 ──────── FINISHED（三局两胜达成）
"""

from __future__ import annotations

import random
import secrets
import time
from dataclasses import dataclass, field
from enum import Enum

from . import constants as C
from . import rules
from .characters import Character
from .models import Plan


class RoomError(Exception):
    """房间操作失败，message 直接展示给玩家。"""


class Phase(str, Enum):
    WAITING = "waiting"            # 等待对手加入
    PREPARING = "preparing"        # 选人 / 增益 / 策略阶段
    BATTLING = "battling"          # 战斗结算中（瞬态）
    FINISHED = "finished"          # 整场对局结束，可请求再来一局
    CLOSED = "closed"              # 房间已关闭


@dataclass
class Outgoing:
    """一条待投递的消息。seat 为 None 表示发给房间内所有人。"""

    seat: int | None
    payload: dict


@dataclass
class Player:
    """房间内的一名玩家。"""

    seat: int
    name: str
    token: str
    score: int = 0
    pool: list[Character] = field(default_factory=list)
    plan: Plan | None = None
    submitted: bool = False
    auto_submitted: bool = False
    rematch: bool = False
    connected: bool = True
    reconnect_deadline: float | None = None   # 掉线后允许重连的截止时间（monotonic）

    def reset_for_round(self) -> None:
        self.submitted = False
        self.auto_submitted = False
        self.plan = None

    def to_dict(self) -> dict:
        return {
            "seat": self.seat,
            "name": self.name,
            "score": self.score,
            "submitted": self.submitted,
            "connected": self.connected,
        }


class Room:
    """一场三局两胜的对局。"""

    def __init__(
        self,
        code: str,
        *,
        seed: int | None = None,
        prepare_timeout: int = C.PREPARE_TIMEOUT_SECONDS,
        reconnect_grace: int = C.RECONNECT_GRACE_SECONDS,
        mode: str = "standard",
    ) -> None:
        self.code = code
        self.mode = mode
        self.seed = seed if seed is not None else secrets.randbelow(2**31 - 1)
        self.rng = random.Random(self.seed)
        self.prepare_timeout = prepare_timeout
        self.reconnect_grace = reconnect_grace
        self.players: list[Player] = []
        self.phase = Phase.WAITING
        self.round_index = 0                     # 当前局数，从 1 开始显示
        self.deadline: float | None = None       # 准备阶段截止时间（monotonic）
        self.history: list[dict] = []            # 每局结果摘要
        self.closed_reason = ""
        self.created_at = time.monotonic()
        self.last_activity = self.created_at

    # ------------------------------------------------------------ 基础信息
    @property
    def is_full(self) -> bool:
        return len(self.players) >= 2

    @property
    def is_closed(self) -> bool:
        return self.phase is Phase.CLOSED

    def player_of(self, seat: int) -> Player:
        for player in self.players:
            if player.seat == seat:
                return player
        raise RoomError("你不在这个房间中")

    def opponent_of(self, seat: int) -> Player:
        return self.player_of(1 - seat)

    def scores(self) -> list[int]:
        """比分列表。座位还没坐满时用 0 补齐，方便等待页也能展示。"""

        return [self.player_of(seat).score if self.has_seat(seat) else 0 for seat in (0, 1)]

    def has_seat(self, seat: int) -> bool:
        return any(player.seat == seat for player in self.players)

    def player_by_token(self, token: str) -> Player | None:
        for player in self.players:
            if player.token == token:
                return player
        return None

    def room_state_payload(self) -> dict:
        return {
            "room_code": self.code,
            "phase": self.phase.value,
            "round_index": self.round_index,
            "score": self.scores(),
            "players": [p.to_dict() for p in self.players],
        }

    def touch(self) -> None:
        self.last_activity = time.monotonic()

    # ------------------------------------------------------------ 加入与退出
    def join(self, name: str) -> tuple[Player, list[Outgoing]]:
        if self.is_closed:
            raise RoomError("房间已关闭，请重新创建房间")
        if self.is_full:
            raise RoomError("房间已经满员")
        if self.phase is not Phase.WAITING:
            raise RoomError("对局已经开始，无法加入")
        player = Player(seat=len(self.players), name=name, token=secrets.token_hex(8))
        self.players.append(player)
        self.touch()

        outgoings = [
            Outgoing(
                player.seat,
                self._msg("room_joined", **self.room_state_payload(), seat=player.seat, token=player.token),
            ),
        ]
        if self.is_full:
            outgoings.append(Outgoing(0, self._msg("opponent_joined", name=player.name)))
            outgoings.extend(self.start_round())
        return player, outgoings

    def disconnect(self, seat: int, now: float | None = None) -> list[Outgoing]:
        """玩家掉线：先给一段重连宽限，超时才关闭房间。"""

        if self.is_closed:
            return []
        now = time.monotonic() if now is None else now
        player = self.player_of(seat)
        if not player.connected:
            return []
        player.connected = False
        player.reconnect_deadline = now + self.reconnect_grace
        self.touch()

        outgoings: list[Outgoing] = []
        if self.is_full:
            opponent = self.opponent_of(seat)
            outgoings.append(
                Outgoing(
                    opponent.seat,
                    self._msg(
                        "opponent_disconnected",
                        name=player.name,
                        grace_seconds=self.reconnect_grace,
                    ),
                )
            )
        return outgoings

    def reconnect(self, token: str, now: float | None = None) -> list[Outgoing]:
        """用 token 重新坐上原来的座位，并把当前房间状态同步回去。"""

        if self.is_closed:
            raise RoomError("房间已关闭，无法重连")
        player = self.player_by_token(token)
        if player is None:
            raise RoomError("重连凭据无效，请重新创建或加入房间")
        if player.connected:
            # 同一凭据重复连接：把旧连接顶掉，状态照常同步
            pass
        player.connected = True
        player.reconnect_deadline = None
        if self.deadline is not None:
            # 备战阶段的剩余时间按"掉线期间照常流逝"计算
            remaining = max(1, int(self.deadline - time.monotonic()))
        else:
            remaining = 0
        self.touch()

        outgoings = [Outgoing(player.seat, self.state_sync_payload(player, remaining_seconds=remaining))]
        if self.is_full:
            opponent = self.opponent_of(player.seat)
            outgoings.append(Outgoing(opponent.seat, self._msg("opponent_reconnected", name=player.name)))
        return outgoings

    def state_sync_payload(self, player: Player, remaining_seconds: int = 0) -> dict:
        """重连后一次性把玩家需要恢复界面的信息发过去。"""

        payload = self._msg(
            "state_sync",
            **self.room_state_payload(),
            seat=player.seat,
            token=player.token,
            plan=player.plan.to_dict() if player.plan else None,
            submitted=player.submitted,
            opponent_ready=self.is_full and self.opponent_of(player.seat).submitted,
            remaining_seconds=remaining_seconds,
        )
        if player.pool:
            payload["pool"] = rules.pool_payload(player.pool)
            payload["mode"] = self.mode
            payload["team_size"] = C.TEAM_SIZE
            payload["pool_size"] = C.POOL_SIZE
            payload["bonus_per_round"] = C.BONUS_PER_ROUND
            payload["max_bonus_per_fighter"] = C.MAX_BONUS_PER_FIGHTER
            payload["rounds_to_win"] = C.ROUNDS_TO_WIN
            payload["bonus_options"] = [
                {"kind": "atk", "label": f"攻击 +{C.BONUS_ATK}"},
                {"kind": "hp", "label": f"生命 +{C.BONUS_HP}"},
            ]
            payload["strategies"] = rules.available_strategies()
        return payload

    def close(self, reason: str) -> list[Outgoing]:
        if self.is_closed:
            return []
        self.phase = Phase.CLOSED
        self.closed_reason = reason
        self.deadline = None
        return [Outgoing(None, self._msg("room_closed", reason=reason))]

    # ------------------------------------------------------------ 局内流程
    def start_round(self) -> list[Outgoing]:
        """开始新的一局：重新抽取双方角色池并进入准备阶段。"""

        if self.is_closed:
            return []
        self.round_index += 1
        self.deadline = time.monotonic() + self.prepare_timeout
        self.phase = Phase.PREPARING
        pools = rules.generate_pools(self.rng)
        for player, pool in zip(self.players, pools, strict=True):
            player.reset_for_round()
            player.pool = pool
        self.touch()

        outgoings: list[Outgoing] = []
        for player in self.players:
            outgoings.append(
                Outgoing(
                    player.seat,
                    self._msg(
                        "round_start",
                        round_index=self.round_index,
                        score=self.scores(),
                        deadline_seconds=self.prepare_timeout,
                        pool=rules.pool_payload(player.pool),
                        mode=self.mode,
                        rounds_to_win=C.ROUNDS_TO_WIN,
                        team_size=C.TEAM_SIZE,
                        pool_size=C.POOL_SIZE,
                        bonus_per_round=C.BONUS_PER_ROUND,
                        bonus_options=[
                            {"kind": "atk", "label": f"攻击 +{C.BONUS_ATK}"},
                            {"kind": "hp", "label": f"生命 +{C.BONUS_HP}"},
                        ],
                        max_bonus_per_fighter=C.MAX_BONUS_PER_FIGHTER,
                        strategies=rules.available_strategies(),
                    ),
                )
            )
        return outgoings

    def submit(self, seat: int, plan: Plan) -> list[Outgoing]:
        """提交或修改本级方案；双方都提交后立刻结算。"""

        if self.is_closed:
            raise RoomError("房间已关闭")
        if self.phase is not Phase.PREPARING:
            raise RoomError("当前不是待提交阶段")
        player = self.player_of(seat)
        rules.validate_plan(player.pool, plan)

        player.plan = plan
        player.submitted = True
        player.auto_submitted = False
        self.touch()

        outgoings = [
            Outgoing(seat, self._msg("plan_accepted", plan=plan.to_dict())),
        ]
        other = self.opponent_of(seat) if self.is_full else None
        if other is not None:
            outgoings.append(Outgoing(other.seat, self._msg("opponent_ready")))
        if self.is_full and all(p.submitted for p in self.players):
            outgoings.extend(self._resolve_round())
        return outgoings

    def tick(self, now: float | None = None) -> list[Outgoing]:
        """准备阶段超时：未提交的玩家自动随机提交，然后结算本局。"""

        now = time.monotonic() if now is None else now
        # 掉线超时：等不到人回来就关房
        for player in self.players:
            if not player.connected and player.reconnect_deadline is not None and now >= player.reconnect_deadline:
                return self.close(f"{player.name} 掉线超过 {self.reconnect_grace} 秒未重连，房间已关闭")
        if self.phase is not Phase.PREPARING or self.deadline is None or now < self.deadline:
            return []
        outgoings: list[Outgoing] = []
        for player in self.players:
            if player.submitted:
                continue
            player.plan = rules.random_plan(player.pool, self.rng)
            player.submitted = True
            player.auto_submitted = True
            outgoings.append(
                Outgoing(
                    player.seat,
                    self._msg(
                        "plan_auto_submitted",
                        plan=player.plan.to_dict(),
                        reason="准备时间结束，系统已为你随机提交方案",
                    ),
                )
            )
            other = self.opponent_of(player.seat)
            outgoings.append(Outgoing(other.seat, self._msg("opponent_ready")))
        outgoings.extend(self._resolve_round())
        return outgoings

    def _resolve_round(self) -> list[Outgoing]:
        if self.phase is Phase.BATTLING or self.is_closed or not self.is_full:
            return []
        self.phase = Phase.BATTLING
        self.deadline = None
        self.touch()

        teams = [
            rules.build_team(player.pool, player.plan, player.seat) for player in self.players
        ]
        # 开战前的阵容快照：前端用它绘制血条动画，战斗过程会修改 Fighter 对象
        lineups = [[f.snapshot() for f in team] for team in teams]
        strategies = [player.plan.strategy for player in self.players]
        round_seed = self._round_seed()
        result = rules.simulate(teams, strategies, seed=round_seed)

        auto_submitted = [p.seat for p in self.players if p.auto_submitted]
        score_before = self.scores()
        winner_seat = result.winner_team
        if winner_seat is not None:
            self.player_of(winner_seat).score += 1
        round_summary = {
            "round_index": self.round_index,
            "winner_seat": winner_seat,
            "reason": result.reason,
            "rounds": result.rounds,
            "score": self.scores(),
            "seed": round_seed,
        }
        self.history.append(round_summary)

        report = {
            "round_index": self.round_index,
            # 战报里的比分是本局开始前的比分，本局结果由 round_result 给出
            "score": score_before,
            "auto_submitted": auto_submitted,
            "plans": [p.plan.to_dict() for p in self.players],
            "players": [{"seat": p.seat, "name": p.name, "pool": rules.pool_payload(p.pool)} for p in self.players],
            "lineups": lineups,
            "result": result.to_dict(),
        }
        outgoings = [Outgoing(None, self._msg("battle_report", **report))]

        match_over = any(p.score >= C.ROUNDS_TO_WIN for p in self.players)
        if winner_seat is None:
            # 平局（双方同归于尽）：本局重开，分数不变
            outgoings.append(
                Outgoing(
                    None,
                    self._msg(
                        "round_result",
                        round_index=self.round_index,
                        winner_seat=None,
                        score=self.scores(),
                        match_over=False,
                        replay=True,
                        reason=result.reason,
                    ),
                )
            )
            self.round_index -= 1  # start_round 会自增，保持局数不变
            outgoings.extend(self.start_round())
            return outgoings

        outgoings.append(
            Outgoing(
                None,
                self._msg(
                    "round_result",
                    round_index=self.round_index,
                    winner_seat=winner_seat,
                    score=self.scores(),
                    match_over=match_over,
                    reason=result.reason,
                    rounds=result.rounds,
                ),
            )
        )

        if match_over:
            self.phase = Phase.FINISHED
            for player in self.players:
                player.rematch = False
            outgoings.append(
                Outgoing(
                    None,
                    self._msg(
                        "game_over",
                        winner_seat=winner_seat,
                        score=self.scores(),
                        history=list(self.history),
                        players=[{"seat": p.seat, "name": p.name} for p in self.players],
                    ),
                )
            )
        else:
            outgoings.extend(self.start_round())
        return outgoings

    def _round_seed(self) -> int:
        """每局一个稳定的种子：同一房间、同一局永远重放出同一场战斗。"""

        return (self.seed * 31 + self.round_index * 7919) % (2**31 - 1)

    # ------------------------------------------------------------ 再来一局
    def request_rematch(self, seat: int) -> list[Outgoing]:
        if self.is_closed:
            raise RoomError("房间已关闭")
        if self.phase is not Phase.FINISHED:
            raise RoomError("对局尚未结束")
        player = self.player_of(seat)
        player.rematch = True
        other = self.opponent_of(seat)

        if not other.rematch:
            return [Outgoing(other.seat, self._msg("opponent_rematch"))]

        for p in self.players:
            p.score = 0
            p.rematch = False
            p.reset_for_round()
        self.history.clear()
        self.round_index = 0
        self.rng = random.Random(secrets.randbelow(2**31 - 1))
        self.phase = Phase.WAITING
        outgoings = [Outgoing(None, self._msg("rematch_started"))]
        outgoings.extend(self.start_round())
        return outgoings

    # ------------------------------------------------------------ 消息构造
    def _msg(self, kind: str, **payload) -> dict:
        payload["type"] = kind
        payload["room_code"] = self.code
        return payload
