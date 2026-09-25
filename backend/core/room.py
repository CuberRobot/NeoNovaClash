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
from . import modes, rules
from .characters import Character
from .models import Plan


class RoomError(Exception):
    """房间操作失败，message 直接展示给玩家。"""


# 练习模式里电脑对手的默认昵称（前端会再补一句"电脑，不是真人"，避免被误当成真人）
BOT_NAME = "练习对手"
# 机器人的"思考"时间（秒）：看完回放后 / 出方案前，随机取值，像真人一样不会瞬发
BOT_THINK_AFTER_REPLAY = (2.0, 6.0)
BOT_THINK_BEFORE_PLAN = (4.0, 16.0)


class Phase(str, Enum):
    WAITING = "waiting"  # 等待对手加入
    PREPARING = "preparing"  # 选人 / 增益 / 策略阶段
    BATTLING = "battling"  # 战斗结算中（瞬态）
    FINISHED = "finished"  # 整场对局结束，可请求再来一局
    CLOSED = "closed"  # 房间已关闭


@dataclass
class Outgoing:
    """一条待投递的消息。seat 为 None 表示发给房间内所有人。"""

    seat: int | None
    payload: dict

    def message_type(self) -> str | None:
        """这条消息的协议类型（`type` 字段），运维日志与统计都靠它识别关键节点。

        放成方法而不是普通字段，是为了让房间规则层始终是"纯数据"，
        不引入日志或统计的概念。
        """

        kind = self.payload.get("type")
        return kind if isinstance(kind, str) else None


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
    replay_done: bool = False  # 是否已经看完上一局的回放
    candidates: list[rules.PoolCard] = field(default_factory=list)  # 补卡候选（3 选 1）
    picked: bool = False  # 本局是否已经选过补卡
    is_bot: bool = False  # 练习模式里的电脑对手
    bot_action_at: float = 0.0  # 机器人下一次动作的时间（monotonic）
    connected: bool = True
    reconnect_deadline: float | None = None  # 掉线后允许重连的截止时间（monotonic）

    def reset_for_round(self) -> None:
        self.submitted = False
        self.auto_submitted = False
        self.plan = None
        self.candidates = []
        self.picked = False

    def to_dict(self) -> dict:
        return {
            "seat": self.seat,
            "name": self.name,
            "score": self.score,
            "submitted": self.submitted,
            "connected": self.connected,
            "is_bot": self.is_bot,
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
        mode: modes.ModeConfig | str = modes.DEFAULT_MODE_KEY,
    ) -> None:
        self.code = code
        self.mode: modes.ModeConfig = mode if isinstance(mode, modes.ModeConfig) else modes.get_mode(mode)
        self.seed = seed if seed is not None else secrets.randbelow(2**31 - 1)
        self.rng = random.Random(self.seed)
        self.prepare_timeout = prepare_timeout
        self.reconnect_grace = reconnect_grace
        self.players: list[Player] = []
        self.phase = Phase.WAITING
        self.round_index = 0  # 当前局数，从 1 开始显示
        self.deadline: float | None = None  # 准备阶段截止时间（monotonic）
        self.replay_deadline: float | None = None  # 等客户端看完回放的兜底时间（monotonic）
        self.awaiting_replay = False  # 本局倒计时是否还没开始（等回放）
        # 整场三局共用同一份角色池：核心玩法是"猜对手会从这 6 张里选谁、怎么排"
        self.match_pools: list[list[Character]] | None = None
        self.deck: list[Character] = []  # 没被抽进双方池子的角色，用来发补卡候选
        self.final_payload: dict | None = None  # 整场结果，供赛后重连恢复结算页
        self.history: list[dict] = []  # 每局结果摘要
        self.closed_reason = ""
        self.close_logged = False  # 运维日志去重：一个房间的关闭只记录一次
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
            # 房间的模式一旦定下就不再变化：用房间号加入的玩家也按房主选的模式对局
            "mode": self.mode.key,
            "mode_name": self.mode.name,
            "random_tags": self.mode.random_tags,
        }

    def touch(self) -> None:
        self.last_activity = time.monotonic()

    def diagnostic_payload(self) -> dict:
        """运维诊断用的房间快照：只有连接与提交状态，不含昵称、token 或任何私有信息。"""

        now = time.monotonic()
        return {
            "room_code": self.code,
            "phase": self.phase.value,
            "round_index": self.round_index,
            "score": self.scores(),
            "mode": self.mode.key,
            "idle_seconds": round(now - self.last_activity, 1),
            "seats": [
                {
                    "seat": player.seat,
                    "connected": player.connected,
                    "submitted": player.submitted,
                    "auto_submitted": player.auto_submitted,
                    "replay_done": player.replay_done,
                }
                for player in self.players
            ],
        }

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

    def join_bot(self, name: str = BOT_NAME) -> list[Outgoing]:
        """练习模式：直接坐上一个电脑对手，不走匹配队列。"""

        if self.is_closed:
            raise RoomError("房间已关闭，请重新创建房间")
        if self.is_full:
            raise RoomError("房间已经满员")
        if self.phase is not Phase.WAITING:
            raise RoomError("对局已经开始，无法加入")
        bot = Player(seat=len(self.players), name=name, token=secrets.token_hex(8), is_bot=True)
        self.players.append(bot)
        self.touch()
        outgoings: list[Outgoing] = []
        if self.is_full:
            outgoings.append(Outgoing(0, self._msg("opponent_joined", name=bot.name)))
            outgoings.extend(self.start_round())
        return outgoings

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
        if self.awaiting_replay:
            # 倒计时还没开始（在等回放），回到界面后由客户端继续确认
            remaining = self.prepare_timeout
        elif self.deadline is not None:
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
            awaiting_replay=self.awaiting_replay,
        )
        if self.phase is Phase.PREPARING and player.candidates:
            payload["candidates"] = rules.pool_payload(player.candidates)
            payload["draft_size"] = C.CARD_DRAFT_SIZE
        # 整场已经结束的房间：把结算结果一起发回去，客户端直接重新弹出胜负面板
        if self.phase is Phase.FINISHED and self.final_payload is not None:
            payload["final"] = self.final_payload
        if player.pool:
            payload["pool"] = rules.pool_payload(player.pool)
            payload["mode"] = self.mode.key
            payload["mode_name"] = self.mode.name
            payload["mode_summary"] = self.mode.summary
            payload["random_tags"] = self.mode.random_tags
            payload["team_size"] = self.mode.team_size
            payload["pool_size"] = self.mode.pool_size
            payload["bonus_per_round"] = self.mode.bonus_per_round
            payload["max_bonus_per_fighter"] = self.mode.max_bonus_per_fighter
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
    def start_round(self, *, after_battle: bool = False) -> list[Outgoing]:
        """开始新的一局。

        - 角色池整场固定：只在第一局抽取，后面两局沿用同一份池子；
        - 上一局刚打完（after_battle）时**不立刻开始倒计时**：
          等两边客户端都看完回放（replay_done）再开始，兜底 REPLAY_GRACE_SECONDS 秒。
        """

        if self.is_closed:
            return []
        self.round_index += 1
        self.phase = Phase.PREPARING
        if self.match_pools is None:
            self.match_pools = rules.generate_pools(self.rng, self.mode)
            used = {card.id for pool in self.match_pools for card in pool}
            self.deck = [c for c in rules.deck_source(self.mode) if c.id not in used]
            self.rng.shuffle(self.deck)
        for player in self.players:
            player.reset_for_round()
            # 只在开局发一次底牌：之后每局的补卡都长在 player.pool 上，不能被覆盖回去
            if not player.pool:
                player.pool = list(self.match_pools[player.seat])
            player.replay_done = not after_battle
        # 第二局起发补卡候选：随机 3 张，选 1 张进池（7 选 3 → 8 选 3）
        self.deal_candidates()
        self.awaiting_replay = after_battle
        self._schedule_bot(after_battle)
        if after_battle:
            self.deadline = None
            self.replay_deadline = time.monotonic() + C.REPLAY_GRACE_SECONDS
        else:
            self.deadline = time.monotonic() + self.prepare_timeout
            self.replay_deadline = None
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
                        awaiting_replay=after_battle,
                        # 对手昵称（含"是不是电脑"）随每局一起下发，前端好显示"对手：xxx"
                        players=[{"seat": p.seat, "name": p.name, "is_bot": p.is_bot} for p in self.players],
                        pool=rules.pool_payload(player.pool),
                        candidates=rules.pool_payload(player.candidates),
                        draft_size=C.CARD_DRAFT_SIZE,
                        mode=self.mode.key,
                        mode_name=self.mode.name,
                        mode_summary=self.mode.summary,
                        random_tags=self.mode.random_tags,
                        rounds_to_win=C.ROUNDS_TO_WIN,
                        team_size=self.mode.team_size,
                        pool_size=self.mode.pool_size,
                        bonus_per_round=self.mode.bonus_per_round,
                        bonus_options=[
                            {"kind": "atk", "label": f"攻击 +{C.BONUS_ATK}"},
                            {"kind": "hp", "label": f"生命 +{C.BONUS_HP}"},
                        ],
                        max_bonus_per_fighter=self.mode.max_bonus_per_fighter,
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
        rules.validate_plan(player.pool, plan, self.mode)

        player.plan = plan
        player.submitted = True
        player.auto_submitted = False
        self.touch()

        outgoings: list[Outgoing] = []
        if player.candidates and not player.picked:
            # 直接提交也可以：系统随机补一张，不让玩家因为忘记选卡而少一张牌
            outgoings.extend(self._apply_pick(player, self.rng.choice(player.candidates), auto=True))
        outgoings.extend(
            [
                Outgoing(seat, self._msg("plan_accepted", plan=plan.to_dict())),
            ]
        )
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
        if self.phase is not Phase.PREPARING:
            return []
        if self.bots():  # 练习模式：电脑对手按自己的节奏行动
            outgoings = self._tick_bots(now)
            if outgoings:
                return outgoings
        if self.awaiting_replay:
            # 有人迟迟没确认"回放看完了"（关掉页面、回放一直暂停…），兜底开始倒计时
            if self.replay_deadline is not None and now >= self.replay_deadline:
                return self.begin_prepare()
            return []
        if self.deadline is None or now < self.deadline:
            return []
        outgoings: list[Outgoing] = []
        for player in self.players:
            if player.submitted:
                continue
            if player.candidates and not player.picked:
                # 超时了还没选补卡：随机补一张，再随机出方案
                outgoings.extend(self.auto_pick_candidate(player.seat))
            player.plan = rules.random_plan(player.pool, self.rng, self.mode)
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

    # ------------------------------------------------------------ 电脑对手（练习模式）
    def bots(self) -> list[Player]:
        return [player for player in self.players if player.is_bot]

    def _tick_bots(self, now: float) -> list[Outgoing]:
        """推进电脑对手：看完回放 → 挑补卡 → 出方案。人类玩家感觉就像对面在思考。"""

        outgoings: list[Outgoing] = []
        for bot in self.bots():
            if self.phase is not Phase.PREPARING:
                return outgoings
            if self.awaiting_replay:
                if not bot.replay_done and now >= bot.bot_action_at:
                    outgoings.extend(self.mark_replay_done(bot.seat))
                    bot.bot_action_at = now + self.rng.uniform(*BOT_THINK_AFTER_REPLAY)
                continue
            if bot.submitted or now < bot.bot_action_at:
                continue
            if bot.candidates and not bot.picked:
                pick = self.rng.choice(bot.candidates)
                outgoings.extend(self._apply_pick(bot, pick, auto=True))
            try:
                outgoings.extend(self.submit(bot.seat, rules.random_plan(bot.pool, self.rng, self.mode)))
            except (RoomError, rules.RuleError):  # pragma: no cover - 正常不会发生
                bot.submitted = True  # 兜底：别让机器人卡住整局
            bot.bot_action_at = float("inf")  # 本局不再动作
        return outgoings

    def _schedule_bot(self, after_battle: bool) -> None:
        """给每个机器人安排下一次动作的时间。"""

        low, high = BOT_THINK_AFTER_REPLAY if after_battle else BOT_THINK_BEFORE_PLAN
        deadline = time.monotonic() + self.rng.uniform(low, high)
        for bot in self.bots():
            bot.replay_done = not after_battle
            bot.bot_action_at = deadline

    # ------------------------------------------------------------ 补卡
    def candidates_per_player(self) -> int:
        """当前还能给每人发几张补卡候选（不够就不发，大战场就是这种情况）。"""

        if self.round_index <= 1:
            return 0
        if not self.players:
            return 0
        return C.CARD_DRAFT_SIZE if len(self.deck) >= C.CARD_DRAFT_SIZE * len(self.players) else 0

    def deal_candidates(self) -> None:
        """给双方各发 CARD_DRAFT_SIZE 张补卡候选（候选期间这几张从牌堆里扣住，不会撞车）。"""

        count = self.candidates_per_player()
        if count <= 0:
            return
        for player in self.players:
            player.candidates = []
            player.picked = False
            for _ in range(count):
                character = self.deck.pop(self.rng.randrange(len(self.deck)))
                player.candidates.append(rules.make_pool_card(character, self.rng, self.mode))

    def pick_candidate(self, seat: int, char_id: int, *, auto: bool = False) -> list[Outgoing]:
        """把候选里的一张收进角色池，剩下两张退回牌堆。"""

        if self.phase is not Phase.PREPARING:
            raise RoomError("当前不是备战阶段")
        player = self.player_of(seat)
        if player.picked or not player.candidates:
            return []
        chosen = next((card for card in player.candidates if card.id == char_id), None)
        if chosen is None:
            raise RoomError("这张牌不在你的补卡候选里")
        return self._apply_pick(player, chosen, auto=auto)

    def auto_pick_candidate(self, seat: int) -> list[Outgoing]:
        """没选就随机补一张（超时、或者直接提交方案时）。"""

        player = self.player_of(seat)
        if player.picked or not player.candidates:
            return []
        chosen = self.rng.choice(player.candidates)
        return self._apply_pick(player, chosen, auto=True)

    def _apply_pick(self, player: Player, chosen: rules.PoolCard, *, auto: bool) -> list[Outgoing]:
        player.candidates = [card for card in player.candidates if card.id != chosen.id]
        # 没选中的退回牌堆（牌堆里存的是角色本身，不是带标签的池卡），留给下一局再发
        self.deck.extend(card.character for card in player.candidates)
        player.candidates = []
        player.picked = True
        player.pool = [*player.pool, chosen]
        self.touch()
        return [
            Outgoing(
                player.seat,
                self._msg(
                    "pool_updated",
                    round_index=self.round_index,
                    pool=rules.pool_payload(player.pool),
                    card=chosen.to_dict(),
                    auto=auto,
                ),
            )
        ]

    def begin_prepare(self) -> list[Outgoing]:
        """正式开始本局的准备倒计时（回放看完或兜底时间到了）。"""

        if self.phase is not Phase.PREPARING or not self.awaiting_replay:
            return []
        self.awaiting_replay = False
        self.replay_deadline = None
        self.deadline = time.monotonic() + self.prepare_timeout
        self.touch()
        return [
            Outgoing(
                None,
                self._msg(
                    "prepare_started",
                    round_index=self.round_index,
                    deadline_seconds=self.prepare_timeout,
                ),
            )
        ]

    def mark_replay_done(self, seat: int) -> list[Outgoing]:
        """客户端确认"上一局的回放已经看完"，两边都确认后开始倒计时。"""

        if self.phase is not Phase.PREPARING or not self.awaiting_replay:
            return []
        player = self.player_of(seat)
        if player.replay_done:
            return []
        player.replay_done = True
        self.touch()
        if all(p.replay_done for p in self.players):
            return self.begin_prepare()
        return []

    def _resolve_round(self) -> list[Outgoing]:
        """双方方案齐了就开打：算出战报、更新比分、决定进入下一局还是整场结束。"""

        if self.phase is Phase.BATTLING or self.is_closed or not self.is_full:
            return []
        self.phase = Phase.BATTLING
        self.deadline = None
        self.replay_deadline = None
        self.awaiting_replay = False
        self.touch()

        teams = [rules.build_team(player.pool, player.plan, player.seat, self.mode) for player in self.players]
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
            outgoings.extend(self.start_round(after_battle=True))
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
            # 存一份整场结果：赛后再重连（刷新页面）也要能看到结算页，而不是被丢回大厅
            self.final_payload = {
                "winner_seat": winner_seat,
                "score": self.scores(),
                "history": list(self.history),
                "players": [{"seat": p.seat, "name": p.name} for p in self.players],
            }
            outgoings.append(
                Outgoing(
                    None,
                    self._msg("game_over", **self.final_payload),
                )
            )
        else:
            outgoings.extend(self.start_round(after_battle=True))
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
        if other.is_bot:  # 练习模式：电脑对手总是同意再来一局
            other.rematch = True

        if not other.rematch:
            return [Outgoing(other.seat, self._msg("opponent_rematch"))]

        for p in self.players:
            p.score = 0
            p.rematch = False
            p.reset_for_round()
            p.pool = []           # 新的一场：底牌重新发
        self.match_pools = None  # 新的一场对局：重新抽一份整场固定的角色池
        self.deck = []
        self.final_payload = None
        self.awaiting_replay = False
        self.replay_deadline = None
        self.deadline = None
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
