"""对局中的运行时模型：出战方案、战士状态、战斗事件与战斗结果。

这里的对象只描述数据，战斗流程放在 engine.py，规则校验放在 rules.py。
"""

from __future__ import annotations

from dataclasses import dataclass, field

NONE_TAG = "none"

# 攻击优先策略取值
STRATEGY_LOWEST_HP = "lowest_hp"
STRATEGY_HIGHEST_ATK = "highest_atk"
STRATEGY_TAG_PRIORITY = "tag_priority"

STRATEGY_LABELS = {
    STRATEGY_LOWEST_HP: "优先攻击当前血量最低的敌人",
    STRATEGY_HIGHEST_ATK: "优先攻击攻击力最高的敌人",
    STRATEGY_TAG_PRIORITY: "优先攻击指定标签的敌人",
}

# 增益类型
BONUS_ATK = "atk"
BONUS_HP = "hp"
BONUS_LABELS = {BONUS_ATK: "攻击 +2", BONUS_HP: "生命 +4"}


@dataclass(frozen=True)
class Strategy:
    """战前设定的攻击优先策略。"""

    kind: str = STRATEGY_LOWEST_HP
    tag: str | None = None

    def describe(self) -> str:
        if self.kind == STRATEGY_TAG_PRIORITY and self.tag:
            return f"优先攻击带「{self.tag}」标签的敌人"
        return STRATEGY_LABELS.get(self.kind, "优先攻击当前血量最低的敌人")

    def to_dict(self) -> dict:
        return {"kind": self.kind, "tag": self.tag}


@dataclass(frozen=True)
class Bonus:
    """一次增益：作用于第 slot 个出击位（从 1 开始）。"""

    slot: int
    kind: str

    def to_dict(self) -> dict:
        return {"slot": self.slot, "kind": self.kind}


@dataclass(frozen=True)
class Plan:
    """一局中一名玩家提交的完整方案。"""

    selection: tuple[int, ...]          # 角色编号，顺序即出击顺序
    bonuses: tuple[Bonus, ...]
    strategy: Strategy = field(default_factory=Strategy)

    def to_dict(self) -> dict:
        return {
            "selection": list(self.selection),
            "bonuses": [b.to_dict() for b in self.bonuses],
            "strategy": self.strategy.to_dict(),
        }


@dataclass
class Fighter:
    """战场上的一名战士（角色在对局中的实例）。"""

    uid: str
    char_id: int
    name: str
    team: int
    slot: int                 # 出击位，从 0 开始
    base_atk: int
    base_hp: int
    atk: int
    max_hp: int
    hp: int
    initiative: int
    tags: list[str] = field(default_factory=list)
    role: str = ""
    domain: str = ""
    bonus_atk: int = 0
    bonus_hp: int = 0
    alive: bool = True
    poison: list[int] = field(default_factory=list)          # 每层中毒剩余轮数
    uses_left: dict[str, int] = field(default_factory=dict)  # 标签的剩余使用次数
    lost_tags: list[str] = field(default_factory=list)       # 被剥夺的标签，用于战报展示

    @property
    def has_tags(self) -> bool:
        return bool(self.tags)

    def has(self, tag: str) -> bool:
        return tag in self.tags

    @property
    def position(self) -> str:
        """用于展示的出击位，如 A1 / B3。"""

        return f"{'AB'[self.team]}{self.slot + 1}"

    def snapshot(self) -> dict:
        return {
            "uid": self.uid,
            "char_id": self.char_id,
            "name": self.name,
            "team": self.team,
            "slot": self.slot,
            "position": self.position,
            "role": self.role,
            "domain": self.domain,
            "atk": self.atk,
            "hp": self.hp,
            "max_hp": self.max_hp,
            "initiative": self.initiative,
            "tags": list(self.tags),
            "lost_tags": list(self.lost_tags),
            "alive": self.alive,
            "poison_stacks": len(self.poison),
        }


@dataclass
class BattleEvent:
    """一条战斗事件，前端按 seq 顺序播放。"""

    seq: int
    round: int
    kind: str
    text: str
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "seq": self.seq,
            "round": self.round,
            "kind": self.kind,
            "text": self.text,
            "data": self.data,
        }


@dataclass
class BattleResult:
    """单局战斗的完整结果。"""

    winner_team: int | None          # 0 / 1；None 表示未能判定
    reason: str
    rounds: int
    seed: int
    first_team: int
    events: list[BattleEvent]
    teams: list[list[dict]]          # 双方出战阵容快照
    total_initiative: list[int]
    survivors: list[int]             # 双方存活人数

    def to_dict(self, include_events: bool = True) -> dict:
        payload = {
            "winner_team": self.winner_team,
            "reason": self.reason,
            "rounds": self.rounds,
            "seed": self.seed,
            "first_team": self.first_team,
            "teams": self.teams,
            "total_initiative": self.total_initiative,
            "survivors": self.survivors,
            "event_count": len(self.events),
        }
        if include_events:
            payload["events"] = [e.to_dict() for e in self.events]
        return payload
