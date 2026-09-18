"""游戏模式配置。

三种模式的差异只有四个维度：角色池大小、出战人数、增益次数、标签是否随机分配。
把它们抽成配置对象之后，引擎与房间逻辑都不需要认识"模式"这个概念，
以后加新模式也只是多一条配置。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModeConfig:
    key: str
    name: str
    summary: str
    detail: str
    pool_size: int
    team_size: int
    bonus_per_round: int
    max_bonus_per_fighter: int = 2
    random_tags: bool = False          # 混沌模式：角色与标签脱钩
    excluded_tags: tuple[str, ...] = ()        # 本模式不会出现的标签
    excluded_characters: tuple[int, ...] = ()  # 本模式不会出现的角色编号
    tags_per_player: int = 0           # 随机标签模式下每人分到几个标签
    max_tags_per_character: int = 2    # 单个角色最多拥有几个标签

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "summary": self.summary,
            "detail": self.detail,
            "pool_size": self.pool_size,
            "team_size": self.team_size,
            "bonus_per_round": self.bonus_per_round,
            "max_bonus_per_fighter": self.max_bonus_per_fighter,
            "random_tags": self.random_tags,
            "tags_per_player": self.tags_per_player,
            "max_tags_per_character": self.max_tags_per_character,
        }


# 混沌模式里互斥的标签组合（自爆相关的三条在排除自爆后不会触发，保留作为防御）
TAG_CONFLICTS: tuple[tuple[str, str], ...] = (
    ("explosive", "necromancy"),
    ("explosive", "lifesteal"),
    ("explosive", "berserk"),
    ("berserk", "heavy_armor"),
)


# 混沌模式可用的标签：所有标签里排除自爆
CHAOS_EXCLUDED_TAGS: tuple[str, ...] = ("explosive",)
# 自爆步兵不进混沌模式的角色池（没有自爆标签的它只是一个畸形面板）
CHAOS_EXCLUDED_CHARACTERS: tuple[int, ...] = (4,)


STANDARD = ModeConfig(
    key="standard",
    name="标准模式",
    summary="6 选 3，4 次增益，角色自带固定标签。",
    detail="每局从 20 名角色里抽出双方各 6 张池，选出 3 名出战；角色携带自己固定的标签，是最好上手、也最考验布阵的模式。",
    pool_size=6,
    team_size=3,
    bonus_per_round=4,
)

CHAOS = ModeConfig(
    key="chaos",
    name="混沌模式",
    summary="6 选 3，4 次增益，标签随机重新分配。",
    detail=(
        "角色与标签脱钩：每名玩家会拿到 4 个随机标签，最多 2 个叠在同一个角色身上，"
        "并遵守互斥规则（狂暴不能与重装盔甲共存）。自爆标签与自爆步兵不参与本模式。"
    ),
    pool_size=6,
    team_size=3,
    bonus_per_round=4,
    random_tags=True,
    excluded_tags=CHAOS_EXCLUDED_TAGS,
    excluded_characters=CHAOS_EXCLUDED_CHARACTERS,
    tags_per_player=4,
)

BIG_BATTLEFIELD = ModeConfig(
    key="big_battlefield",
    name="大战场模式",
    summary="9 选 5，6 次增益，五人对五人的大规模战斗。",
    detail=(
        "角色池扩大到 9 张、出战 5 人、增益 6 次，出手顺序拉长为 A1→B1→…→A5→B5。"
        "人多了以后群伤、护盾、复活这类团队标签的价值会明显上升。"
    ),
    pool_size=9,
    team_size=5,
    bonus_per_round=6,
)

MODES: dict[str, ModeConfig] = {mode.key: mode for mode in (STANDARD, CHAOS, BIG_BATTLEFIELD)}
DEFAULT_MODE_KEY = STANDARD.key


class ModeError(ValueError):
    """模式不存在。"""


def get_mode(key: str | None) -> ModeConfig:
    """按 key 取模式配置，缺省返回标准模式。"""

    if not key:
        return MODES[DEFAULT_MODE_KEY]
    mode = MODES.get(str(key).strip().lower())
    if mode is None:
        raise ModeError(f"未知的游戏模式：{key}")
    return mode


def available_modes() -> list[ModeConfig]:
    return list(MODES.values())


def tags_conflict(first: str, second: str) -> bool:
    """两个标签是否互斥。"""

    return (first, second) in TAG_CONFLICTS or (second, first) in TAG_CONFLICTS
