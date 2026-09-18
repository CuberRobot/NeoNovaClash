"""对局规则层：角色池、方案校验、阵容构建与整局模拟。

这一层负责「玩家能做什么、不能做什么」，所有校验失败的提示都直接面向玩家，
文案要能让人一眼看懂该怎么改。
"""

from __future__ import annotations

import random
from collections import Counter

from . import constants as C
from . import tags as taglib
from .characters import Character, get_character, roster
from .engine import run_battle
from .models import (
    BONUS_ATK,
    BONUS_HP,
    NONE_TAG,
    STRATEGY_HIGHEST_ATK,
    STRATEGY_LABELS,
    STRATEGY_LOWEST_HP,
    STRATEGY_TAG_PRIORITY,
    BattleResult,
    Bonus,
    Fighter,
    Plan,
    Strategy,
)


class RuleError(ValueError):
    """方案不符合规则。field 用于前端把错误定位到具体控件。"""

    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


# ---------------------------------------------------------------- 角色池
def generate_pool(rng: random.Random) -> list[Character]:
    """为一名玩家抽取本局角色池（从当前启用的角色中随机 6 名，不重复）。"""

    return rng.sample(list(roster()), C.POOL_SIZE)


def pool_payload(pool: list[Character]) -> list[dict]:
    """角色池的对外结构，前端据此渲染卡牌。"""

    return [
        {
            "id": c.id,
            "name": c.name,
            "role": c.role,
            "atk": c.atk,
            "hp": c.hp,
            "initiative": c.initiative,
            "tag": c.tag,
            "tag_name": taglib.get_spec(c.tag).name,
            "tag_summary": taglib.get_spec(c.tag).summary,
            "domain": c.domain,
            "lore": c.lore,
            "place_first": taglib.get_spec(c.tag).place_first,
        }
        for c in pool
    ]


# ---------------------------------------------------------------- 方案校验
def validate_plan(pool: list[Character], plan: Plan) -> None:
    """校验一份出战方案，不合法时抛出 RuleError（消息可直接展示给玩家）。"""

    pool_ids = {c.id for c in pool}

    if len(plan.selection) != C.TEAM_SIZE:
        raise RuleError(f"需要选择 {C.TEAM_SIZE} 名角色出战，当前选择了 {len(plan.selection)} 名", "selection")

    if len(set(plan.selection)) != len(plan.selection):
        raise RuleError("同一名角色不能重复上场", "selection")

    for char_id in plan.selection:
        if char_id not in pool_ids:
            raise RuleError("选择的角色不在本局角色池中", "selection")

    for slot, char_id in enumerate(plan.selection):
        character = get_character(char_id)
        spec = taglib.get_spec(character.tag)
        if spec.place_first and slot != 0:
            raise RuleError(f"{character.name} 只能放在第一个出击位", "selection")

    if len(plan.bonuses) != C.BONUS_PER_ROUND:
        raise RuleError(
            f"需要分配 {C.BONUS_PER_ROUND} 次增益，当前分配了 {len(plan.bonuses)} 次",
            "bonuses",
        )

    per_slot: Counter[int] = Counter()
    for bonus in plan.bonuses:
        if bonus.slot < 1 or bonus.slot > C.TEAM_SIZE:
            raise RuleError("增益只能分配给已出战的出击位", "bonuses")
        if bonus.kind not in (BONUS_ATK, BONUS_HP):
            raise RuleError("增益类型只能是攻击 +2 或生命 +4", "bonuses")
        per_slot[bonus.slot] += 1
    for slot, count in per_slot.items():
        if count > C.MAX_BONUS_PER_FIGHTER:
            raise RuleError(
                f"每个出击位最多获得 {C.MAX_BONUS_PER_FIGHTER} 次增益，第 {slot} 位当前有 {count} 次",
                "bonuses",
            )

    if plan.strategy.kind not in STRATEGY_LABELS:
        raise RuleError("攻击策略不在可选范围内", "strategy")
    if plan.strategy.kind == STRATEGY_TAG_PRIORITY:
        tag = plan.strategy.tag
        if not tag or tag == NONE_TAG or tag not in taglib.TAGS:
            raise RuleError("选择「优先攻击指定标签」时，需要指定一个有效的标签", "strategy")


# ---------------------------------------------------------------- 阵容构建
def build_team(pool: list[Character], plan: Plan, team: int) -> list[Fighter]:
    """把角色池 + 方案变成战场上的战士列表（顺序即出击顺序）。"""

    fighters: list[Fighter] = []
    bonus_atk: Counter[int] = Counter()
    bonus_hp: Counter[int] = Counter()
    for bonus in plan.bonuses:
        if bonus.kind == BONUS_ATK:
            bonus_atk[bonus.slot] += C.BONUS_ATK
        else:
            bonus_hp[bonus.slot] += C.BONUS_HP

    for index, char_id in enumerate(plan.selection):
        character = get_character(char_id)
        slot = index + 1
        atk = character.atk + bonus_atk.get(slot, 0)
        max_hp = character.hp + bonus_hp.get(slot, 0)
        tags = [character.tag] if character.tag != NONE_TAG else []
        fighters.append(
            Fighter(
                uid=f"{'AB'[team]}{slot}-{character.id}",
                char_id=character.id,
                name=character.name,
                team=team,
                slot=index,
                base_atk=character.atk,
                base_hp=character.hp,
                atk=atk,
                max_hp=max_hp,
                hp=max_hp,
                initiative=character.initiative,
                tags=list(tags),
                role=character.role,
                domain=character.domain,
                bonus_atk=bonus_atk.get(slot, 0),
                bonus_hp=bonus_hp.get(slot, 0),
                uses_left=taglib.initial_uses(tags),
            )
        )
    return fighters


def random_plan(pool: list[Character], rng: random.Random) -> Plan:
    """随机生成一份合法方案，用于准备超时自动提交与平衡性对拍。"""

    first_slot_chars = [c for c in pool if taglib.get_spec(c.tag).place_first]
    others = [c for c in pool if c not in first_slot_chars]

    selection: list[int] = []
    if first_slot_chars and rng.random() < 0.5:
        selection.append(rng.choice(first_slot_chars).id)
        selection.extend(c.id for c in rng.sample(others, C.TEAM_SIZE - 1))
    else:
        selection.extend(c.id for c in rng.sample(pool, C.TEAM_SIZE))
        selection.sort(key=lambda cid: (not taglib.get_spec(get_character(cid).tag).place_first,))

    bonuses: list[Bonus] = []
    slots = [slot for slot in range(1, C.TEAM_SIZE + 1) for _ in range(C.MAX_BONUS_PER_FIGHTER)]
    rng.shuffle(slots)
    for slot in slots[: C.BONUS_PER_ROUND]:
        bonuses.append(Bonus(slot=slot, kind=rng.choice([BONUS_ATK, BONUS_HP])))

    strategy = Strategy(kind=rng.choice([STRATEGY_LOWEST_HP, STRATEGY_HIGHEST_ATK]))
    return Plan(selection=tuple(selection), bonuses=tuple(bonuses), strategy=strategy)


# ---------------------------------------------------------------- 对局模拟
def simulate(
    teams: list[list[Fighter]],
    strategies: list[Strategy],
    seed: int,
) -> BattleResult:
    """执行一场战斗（薄封装，便于测试与工具层直接调用）。"""

    return run_battle(teams, strategies, seed)


def available_strategies() -> list[dict]:
    """前端可选的攻击策略。"""

    return [
        {"kind": STRATEGY_LOWEST_HP, "label": STRATEGY_LABELS[STRATEGY_LOWEST_HP], "need_tag": False},
        {"kind": STRATEGY_HIGHEST_ATK, "label": STRATEGY_LABELS[STRATEGY_HIGHEST_ATK], "need_tag": False},
        {"kind": STRATEGY_TAG_PRIORITY, "label": STRATEGY_LABELS[STRATEGY_TAG_PRIORITY], "need_tag": True},
    ]
