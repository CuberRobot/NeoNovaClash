"""对局规则层：角色池、方案校验、阵容构建与整局模拟。

这一层负责「玩家能做什么、不能做什么」，所有校验失败的提示都直接面向玩家，
文案要能让人一眼看懂该怎么改。
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass
from itertools import combinations

from . import constants as C
from . import modes
from . import tags as taglib
from .characters import Character, roster
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


@dataclass(frozen=True)
class PoolCard:
    """角色池里的一张卡：角色 + 本局实际生效的标签。

    标准模式下标签就是角色自带的标签；混沌模式下标签是随机分配的，
    因此同一名角色在不同对局里可能是完全不同的战术角色。
    """

    character: Character
    tags: tuple[str, ...] = ()

    @property
    def id(self) -> int:
        return self.character.id

    @property
    def name(self) -> str:
        return self.character.name

    # 下面几个属性只是转发角色数据，方便调用方按"角色"的方式读池子里的卡
    @property
    def role(self) -> str:
        return self.character.role

    @property
    def atk(self) -> int:
        return self.character.atk

    @property
    def hp(self) -> int:
        return self.character.hp

    @property
    def initiative(self) -> int:
        return self.character.initiative

    @property
    def domain(self) -> str:
        return self.character.domain

    @property
    def tag(self) -> str:
        """兼容单标签读取：取第一个标签，没有则返回 none。"""

        return self.tags[0] if self.tags else NONE_TAG

    @property
    def place_first(self) -> bool:
        return any(taglib.get_spec(tag).place_first for tag in self.tags)

    def has(self, tag: str) -> bool:
        return tag in self.tags

    def to_dict(self) -> dict:
        character = self.character
        specs = [taglib.get_spec(tag) for tag in self.tags]
        names = [spec.name for spec in specs]
        return {
            "id": character.id,
            "name": character.name,
            "role": character.role,
            "atk": character.atk,
            "hp": character.hp,
            "initiative": character.initiative,
            "domain": character.domain,
            "lore": character.lore,
            # 本局实际生效的标签（可能有 0 个、1 个或多个）
            "tags": list(self.tags),
            "tag_names": names,
            "tag_summaries": [spec.summary for spec in specs],
            # 兼容单标签字段：前端老代码只读这两个也能正常显示
            "tag": self.tags[0] if self.tags else NONE_TAG,
            "tag_name": " / ".join(names) if names else taglib.get_spec(NONE_TAG).name,
            "tag_summary": " ".join(spec.summary for spec in specs),
            "place_first": self.place_first,
        }


# ---------------------------------------------------------------- 角色池
def _roster_for(mode: modes.ModeConfig) -> list[Character]:
    return [c for c in roster() if c.id not in mode.excluded_characters]


def _make_cards(characters: list[Character], mode: modes.ModeConfig, rng: random.Random) -> list[PoolCard]:
    """把抽到的角色变成卡牌：标准模式沿用角色自带标签，混沌模式随机分配标签。"""

    if not mode.random_tags:
        return [
            PoolCard(character=c, tags=(c.tag,) if c.tag != NONE_TAG else ())
            for c in characters
        ]
    cards = [PoolCard(character=c, tags=()) for c in characters]
    _assign_chaos_tags(cards, mode, rng)
    return cards


def _assign_chaos_tags(cards: list[PoolCard], mode: modes.ModeConfig, rng: random.Random) -> None:
    """混沌模式的标签分配：每人 tags_per_player 个标签，单角色最多 2 个，且不能互斥。

    分配时优先给"手上标签最少"的角色，让标签尽量分散而不是堆在同一个人身上
    （旧项目文档里的示例就是 4 个角色各 1 个标签）。
    """

    available = [tag for tag in taglib.TAGS if tag != NONE_TAG and tag not in mode.excluded_tags]
    working: dict[int, list[str]] = {card.character.id: [] for card in cards}

    for _ in range(mode.tags_per_player):
        options: list[tuple[PoolCard, str]] = []
        for card in cards:
            current = working[card.character.id]
            if len(current) >= mode.max_tags_per_character:
                continue
            for tag in available:
                if tag in current:
                    continue
                if any(modes.tags_conflict(tag, existing) for existing in current):
                    continue
                options.append((card, tag))
        if not options:
            break
        fewest = min(len(working[card.character.id]) for card, _ in options)
        options = [option for option in options if len(working[option[0].character.id]) == fewest]
        card, tag = rng.choice(options)
        working[card.character.id].append(tag)

    for index, card in enumerate(cards):
        cards[index] = PoolCard(character=card.character, tags=tuple(working[card.character.id]))


def generate_pool(rng: random.Random, mode: modes.ModeConfig | None = None) -> list[PoolCard]:
    """为一名玩家抽取本局角色池（从当前启用的角色中随机 6 名，不重复）。"""

    mode = mode or modes.get_mode(None)
    characters = rng.sample(_roster_for(mode), mode.pool_size)
    return _make_cards(characters, mode, rng)


def generate_pools(
    rng: random.Random, mode: modes.ModeConfig | None = None
) -> tuple[list[PoolCard], list[PoolCard]]:
    """本局双方的 6 张角色池。

    抽取机制：
    1. 从卡池里**均匀**抽 POOL_SIZE*2 == 12 张（不重复，保证每名角色的出场概率一致）；
    2. 在 12 张的所有对半切分（462 种）里，挑一个最公平的切法：
       - 硬性条件：每个池子至少有 MIN_TAGGED_PER_POOL 名带标签的角色、
         同一标签最多 MAX_SAME_TAG_PER_POOL 名；
       - 软性条件：两个池子的先手值总和、总生命、总攻击最接近。
    3. 因此双方不会拿到同一名角色，也不会出现「一边四个坦克、一边四个脆皮」的碾压池。

    均匀抽样 + 枚举切分是刻意分开的：如果只用「反复重抽直到公平」，
    像自爆步兵这种数值离群的角色会因为拖低公平分数而被系统性筛掉，
    导致它的实际出场率远低于其他角色。
    """

    mode = mode or modes.get_mode(None)
    pool_size = mode.pool_size
    drawn = rng.sample(_roster_for(mode), pool_size * 2)

    best: tuple[int, list[Character], list[Character]] | None = None
    fallback: tuple[int, list[Character], list[Character]] | None = None
    indexes = range(len(drawn))
    for combo in combinations(indexes, pool_size):
        chosen_indexes = set(combo)
        first = [drawn[i] for i in combo]
        second = [drawn[i] for i in indexes if i not in chosen_indexes]
        gap = _pool_gap_score(first, second)
        if fallback is None or gap < fallback[0]:
            fallback = (gap, first, second)
        if not mode.random_tags and not (_pool_has_build_space(first) and _pool_has_build_space(second)):
            continue
        if best is None or gap < best[0]:
            best = (gap, first, second)
        # 已经足够公平就不必继续枚举，省下计算
        if _pools_are_fair(first, second):
            break

    chosen = best or fallback
    if chosen is None:  # pragma: no cover - 卡池小于 2*POOL_SIZE 时才会发生
        first_chars, second_chars = drawn[:pool_size], drawn[pool_size:]
    else:
        _gap, first_chars, second_chars = chosen
    first_chars, second_chars = _sorted_chars(first_chars), _sorted_chars(second_chars)
    return (
        _make_cards(first_chars, mode, rng),
        _make_cards(second_chars, mode, rng),
    )


def _sorted_chars(pool: list[Character]) -> list[Character]:
    """按编号排序，让前端卡片顺序稳定、方便对照。"""

    return sorted(pool, key=lambda character: character.id)


def _pool_has_build_space(pool: list[Character]) -> bool:
    tagged = [c for c in pool if c.tag != NONE_TAG]
    if len(tagged) < C.MIN_TAGGED_PER_POOL:
        return False
    counts = Counter(c.tag for c in tagged)
    return all(count <= C.MAX_SAME_TAG_PER_POOL for count in counts.values())


def _pool_gap_score(first: list[Character], second: list[Character]) -> int:
    """池子强弱差距的粗略度量，数值越小越公平。"""

    def total(pool: list[Character]) -> tuple[int, int, int]:
        return (
            sum(c.initiative for c in pool),
            sum(c.hp for c in pool),
            sum(c.atk for c in pool),
        )

    initiative_a, hp_a, atk_a = total(first)
    initiative_b, hp_b, atk_b = total(second)
    return (
        abs(initiative_a - initiative_b) * 10
        + abs(hp_a - hp_b) * 2
        + abs(atk_a - atk_b) * 4
    )


def _pools_are_fair(first: list[Character], second: list[Character]) -> bool:
    def total(pool: list[Character], pick) -> int:
        return sum(pick(c) for c in pool)

    def within(ratio: tuple[int, int], value_a: int, value_b: int) -> bool:
        base = max(value_a, value_b, 1)
        return abs(value_a - value_b) * ratio[1] <= base * ratio[0]

    initiative_a = total(first, lambda c: c.initiative)
    initiative_b = total(second, lambda c: c.initiative)
    hp_a, hp_b = total(first, lambda c: c.hp), total(second, lambda c: c.hp)
    atk_a, atk_b = total(first, lambda c: c.atk), total(second, lambda c: c.atk)

    return (
        abs(initiative_a - initiative_b) <= C.INITIATIVE_TOLERANCE
        and within(C.HP_TOLERANCE_RATIO, hp_a, hp_b)
        and within(C.ATK_TOLERANCE_RATIO, atk_a, atk_b)
    )


def pool_payload(pool: list[PoolCard]) -> list[dict]:
    """角色池的对外结构，前端据此渲染卡牌。"""

    return [card.to_dict() for card in pool]


def deck_source(mode: modes.ModeConfig | None = None) -> list[Character]:
    """本模式可用的全部角色。开局抽完双方池子后，剩下的就是补卡的候选牌堆。

    标准模式 20 张里用掉 12 张 → 剩 8 张，正好够两局各发 3 张候选；
    大战场 9 选 5 用掉 18 张只剩 2 张，不够发牌，因此那个模式不开补卡。
    """

    mode = mode or modes.get_mode(None)
    return list(_roster_for(mode))


def make_pool_card(
    character: Character, rng: random.Random, mode: modes.ModeConfig | None = None
) -> PoolCard:
    """把一名角色做成池子里的卡（混沌模式下会按模式规则随机分配标签）。"""

    mode = mode or modes.get_mode(None)
    return _make_cards([character], mode, rng)[0]


def deck_candidates(deck: list[Character], rng: random.Random, count: int) -> list[Character]:
    """从候选牌堆里发出 count 张补卡候选（不修改传入列表，由调用方决定去留）。"""

    if count <= 0 or not deck:
        return []
    take = min(count, len(deck))
    return rng.sample(deck, take)


# ---------------------------------------------------------------- 方案校验
def validate_plan(pool: list[PoolCard], plan: Plan, mode: modes.ModeConfig | None = None) -> None:
    """校验一份出战方案，不合法时抛出 RuleError（消息可直接展示给玩家）。"""

    mode = mode or modes.get_mode(None)
    pool_ids = {c.id for c in pool}
    by_id = {card.id: card for card in pool}

    if len(plan.selection) != mode.team_size:
        raise RuleError(
            f"需要选择 {mode.team_size} 名角色出战，当前选择了 {len(plan.selection)} 名",
            "selection",
        )

    if len(set(plan.selection)) != len(plan.selection):
        raise RuleError("同一名角色不能重复上场", "selection")

    for char_id in plan.selection:
        if char_id not in pool_ids:
            raise RuleError("选择的角色不在本局角色池中", "selection")

    for slot, char_id in enumerate(plan.selection):
        card = by_id[char_id]
        if card.place_first and slot != 0:
            raise RuleError(f"{card.name} 只能放在第一个出击位", "selection")

    if len(plan.bonuses) != mode.bonus_per_round:
        raise RuleError(
            f"需要分配 {mode.bonus_per_round} 次增益，当前分配了 {len(plan.bonuses)} 次",
            "bonuses",
        )

    per_slot: Counter[int] = Counter()
    for bonus in plan.bonuses:
        if bonus.slot < 1 or bonus.slot > mode.team_size:
            raise RuleError("增益只能分配给已出战的出击位", "bonuses")
        if bonus.kind not in (BONUS_ATK, BONUS_HP):
            raise RuleError("增益类型只能是攻击 +2 或生命 +4", "bonuses")
        per_slot[bonus.slot] += 1
    for slot, count in per_slot.items():
        if count > mode.max_bonus_per_fighter:
            raise RuleError(
                f"每个出击位最多获得 {mode.max_bonus_per_fighter} 次增益，第 {slot} 位当前有 {count} 次",
                "bonuses",
            )

    if plan.strategy.kind not in STRATEGY_LABELS:
        raise RuleError("攻击策略不在可选范围内", "strategy")
    if plan.strategy.kind == STRATEGY_TAG_PRIORITY:
        tag = plan.strategy.tag
        if not tag or tag == NONE_TAG or tag not in taglib.TAGS:
            raise RuleError("选择「优先攻击指定标签」时，需要指定一个有效的标签", "strategy")


# ---------------------------------------------------------------- 阵容构建
def build_team(pool: list[PoolCard], plan: Plan, team: int, mode: modes.ModeConfig | None = None) -> list[Fighter]:
    """把角色池 + 方案变成战场上的战士列表（顺序即出击顺序）。"""

    by_id = {card.id: card for card in pool}
    fighters: list[Fighter] = []
    bonus_atk: Counter[int] = Counter()
    bonus_hp: Counter[int] = Counter()
    for bonus in plan.bonuses:
        if bonus.kind == BONUS_ATK:
            bonus_atk[bonus.slot] += C.BONUS_ATK
        else:
            bonus_hp[bonus.slot] += C.BONUS_HP

    for index, char_id in enumerate(plan.selection):
        card = by_id[char_id]
        character = card.character
        slot = index + 1
        atk = character.atk + bonus_atk.get(slot, 0)
        max_hp = character.hp + bonus_hp.get(slot, 0)
        tags = [tag for tag in card.tags if tag != NONE_TAG]
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


def random_plan(pool: list[PoolCard], rng: random.Random, mode: modes.ModeConfig | None = None) -> Plan:
    """随机生成一份合法方案，用于准备超时自动提交与平衡性对拍。"""

    mode = mode or modes.get_mode(None)
    first_slot_chars = [card for card in pool if card.place_first]
    others = [card for card in pool if not card.place_first]
    team_size = mode.team_size

    if first_slot_chars and rng.random() < 0.5:
        selection = [rng.choice(first_slot_chars).id]
        selection.extend(card.id for card in rng.sample(others, team_size - 1))
    else:
        picked = rng.sample(pool, team_size)
        picked.sort(key=lambda card: (not card.place_first, card.id))
        selection = [card.id for card in picked]

    bonuses: list[Bonus] = []
    slots = [slot for slot in range(1, team_size + 1) for _ in range(mode.max_bonus_per_fighter)]
    rng.shuffle(slots)
    for slot in slots[: mode.bonus_per_round]:
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
