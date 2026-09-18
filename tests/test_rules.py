"""规则层测试：角色池、方案校验与阵容构建。"""

from __future__ import annotations

import random
from collections import Counter

import pytest

from backend.core import constants as C
from backend.core import rules
from backend.core import tags as taglib
from backend.core.models import BONUS_ATK, BONUS_HP, Bonus, Plan, Strategy


def test_generate_pool_has_expected_size_without_duplicates():
    rng = random.Random(1)
    pool = rules.generate_pool(rng)

    assert len(pool) == C.POOL_SIZE
    assert len({c.id for c in pool}) == C.POOL_SIZE


def test_valid_plan_passes_validation():
    rng = random.Random(2)
    pool = rules.generate_pool(rng)
    plan = rules.random_plan(pool, rng)

    rules.validate_plan(pool, plan)


def test_plan_requires_exact_team_size():
    pool = rules.generate_pool(random.Random(3))
    plan = Plan(selection=(pool[0].id, pool[1].id), bonuses=(), strategy=Strategy())

    with pytest.raises(rules.RuleError) as excinfo:
        rules.validate_plan(pool, plan)

    assert "3 名角色" in str(excinfo.value)
    assert excinfo.value.field == "selection"


def test_plan_rejects_duplicate_characters():
    pool = rules.generate_pool(random.Random(4))
    plan = Plan(
        selection=(pool[0].id, pool[0].id, pool[1].id),
        bonuses=(),
        strategy=Strategy(),
    )

    with pytest.raises(rules.RuleError, match="重复"):
        rules.validate_plan(pool, plan)


def test_explosive_must_be_placed_first():
    explosive = next(c for c in rules.roster() if c.tag == "explosive")
    others = [c for c in rules.roster() if c.tag != "explosive"][:2]
    pool = [explosive, *others]
    plan = Plan(
        selection=(others[0].id, explosive.id, others[1].id),
        bonuses=(),
        strategy=Strategy(),
    )

    with pytest.raises(rules.RuleError, match="只能放在第一个出击位"):
        rules.validate_plan(pool, plan)


def test_bonus_count_must_match():
    pool = rules.generate_pool(random.Random(5))
    plan = Plan(
        selection=tuple(c.id for c in pool[:3]),
        bonuses=(Bonus(slot=1, kind=BONUS_ATK),),
        strategy=Strategy(),
    )

    with pytest.raises(rules.RuleError, match="增益"):
        rules.validate_plan(pool, plan)


def test_bonus_per_fighter_is_limited():
    pool = rules.generate_pool(random.Random(6))
    plan = Plan(
        selection=tuple(c.id for c in pool[:3]),
        bonuses=tuple(
            Bonus(slot=1, kind=BONUS_ATK) for _ in range(C.MAX_BONUS_PER_FIGHTER + 1)
        )
        + (Bonus(slot=2, kind=BONUS_HP),),
        strategy=Strategy(),
    )

    with pytest.raises(rules.RuleError, match="每个出击位最多"):
        rules.validate_plan(pool, plan)


def test_tag_strategy_requires_tag():
    pool = rules.generate_pool(random.Random(7))
    plan = Plan(
        selection=tuple(c.id for c in pool[:3]),
        bonuses=(
            Bonus(slot=1, kind=BONUS_ATK),
            Bonus(slot=1, kind=BONUS_ATK),
            Bonus(slot=2, kind=BONUS_HP),
            Bonus(slot=3, kind=BONUS_HP),
        ),
        strategy=Strategy(kind="tag_priority", tag=None),
    )

    with pytest.raises(rules.RuleError, match="标签"):
        rules.validate_plan(pool, plan)


def test_random_plan_always_valid_across_many_runs():
    rng = random.Random(2026)
    for _ in range(300):
        pool = rules.generate_pool(rng)
        plan = rules.random_plan(pool, rng)
        rules.validate_plan(pool, plan)


def test_build_team_applies_bonuses_in_order():
    pool = rules.generate_pool(random.Random(8))
    selection = tuple(c.id for c in pool[:3])
    plan = Plan(
        selection=selection,
        bonuses=(
            Bonus(slot=1, kind=BONUS_ATK),
            Bonus(slot=1, kind=BONUS_ATK),
            Bonus(slot=2, kind=BONUS_HP),
            Bonus(slot=2, kind=BONUS_HP),
        ),
        strategy=Strategy(),
    )
    rules.validate_plan(pool, plan)

    team = rules.build_team(pool, plan, 0)

    assert [f.char_id for f in team] == list(selection)
    assert team[0].atk == pool[0].atk + 2 * C.BONUS_ATK
    assert team[1].max_hp == pool[1].hp + 2 * C.BONUS_HP
    assert team[0].hp == team[0].max_hp
    assert [f.slot for f in team] == [0, 1, 2]


def test_simulate_runs_a_full_round_and_returns_result():
    rng = random.Random(11)
    pool_a, pool_b = rules.generate_pool(rng), rules.generate_pool(rng)
    plan_a, plan_b = rules.random_plan(pool_a, rng), rules.random_plan(pool_b, rng)
    teams = [rules.build_team(pool_a, plan_a, 0), rules.build_team(pool_b, plan_b, 1)]

    result = rules.simulate(teams, [plan_a.strategy, plan_b.strategy], seed=123)

    assert result.winner_team in (0, 1)
    assert result.rounds >= 1
    assert len(result.events) > 5


def test_roster_is_complete_and_every_tag_is_implemented():
    characters = rules.roster()

    assert len(characters) == 20
    assert len({c.id for c in characters}) == 20
    for character in characters:
        assert character.tag in taglib.TAGS, character.name
        if character.tag != "none":
            assert taglib.get_runtime(character.tag) is not None, character.name


def test_roster_keeps_every_character_distinct():
    """防止平衡调整把角色越调越像：面板组合、同标签角色的曲线都要有区分度。"""

    characters = rules.roster()

    # 1) 不允许出现完全相同的（攻击, 生命, 先手值）三元组
    triples = [(c.atk, c.hp, c.initiative) for c in characters]
    assert len(set(triples)) == len(triples)

    # 2) 不允许出现完全相同的（攻击, 生命）面板
    panels = [(c.atk, c.hp) for c in characters]
    assert len(set(panels)) == len(panels)

    # 3) 同一标签下的角色必须有不同的面板曲线（同一个标签也要有强弱差别）
    by_tag: dict[str, list[tuple[int, int]]] = {}
    for character in characters:
        if character.tag == "none":
            continue
        by_tag.setdefault(character.tag, []).append((character.atk, character.hp))
    for tag, tag_panels in by_tag.items():
        assert len(set(tag_panels)) == len(tag_panels), tag

    # 4) 无标签角色要构成一条「攻击递减、生命递增」的连续曲线，而不是五个一模一样的白板
    plain = sorted((c for c in characters if c.tag == "none"), key=lambda c: -c.atk)
    assert [c.atk for c in plain] == sorted({c.atk for c in plain}, reverse=True)
    assert [c.hp for c in plain] == sorted({c.hp for c in plain})


def test_pool_only_contains_characters_from_active_roster():
    rng = random.Random(77)
    active_ids = {c.id for c in rules.roster()}

    for _ in range(50):
        pool = rules.generate_pool(rng)
        assert {c.id for c in pool} <= active_ids


# ---------------------------------------------------------------- 抽取机制


def test_generate_pools_are_disjoint_and_well_formed():
    rng = random.Random(5)
    for _ in range(300):
        first, second = rules.generate_pools(rng)

        assert len(first) == C.POOL_SIZE and len(second) == C.POOL_SIZE
        ids_first = {c.id for c in first}
        ids_second = {c.id for c in second}
        assert not (ids_first & ids_second), "双方不应该拿到同一名角色"

        for pool in (first, second):
            tagged = [c for c in pool if c.tag != "none"]
            assert len(tagged) >= C.MIN_TAGGED_PER_POOL
            counts = Counter(c.tag for c in tagged)
            assert max(counts.values()) <= C.MAX_SAME_TAG_PER_POOL


def test_generate_pools_are_reproducible():
    first_a, second_a = rules.generate_pools(random.Random(99))
    first_b, second_b = rules.generate_pools(random.Random(99))

    assert [c.id for c in first_a] == [c.id for c in first_b]
    assert [c.id for c in second_a] == [c.id for c in second_b]


def test_generate_pools_keep_the_two_sides_close_in_strength():
    rng = random.Random(11)
    initiative_gaps = []
    hp_gaps = []
    for _ in range(300):
        first, second = rules.generate_pools(rng)
        initiative_gaps.append(abs(sum(c.initiative for c in first) - sum(c.initiative for c in second)))
        hp_gaps.append(abs(sum(c.hp for c in first) - sum(c.hp for c in second)))

    assert sum(initiative_gaps) / len(initiative_gaps) < 3
    assert max(initiative_gaps) <= 10
    assert sum(hp_gaps) / len(hp_gaps) < 12


def test_every_character_is_drawn_at_a_similar_rate():
    """防止「公平性筛选」把某类离群角色系统性筛掉（曾经出现过自爆步兵只有 17.8% 的情况）。"""

    rng = random.Random(2024)
    rounds = 400
    counter: Counter[int] = Counter()
    for _ in range(rounds):
        for pool in rules.generate_pools(rng):
            for character in pool:
                counter[character.id] += 1

    expected = rounds * C.POOL_SIZE * 2 / len(rules.roster())
    for character in rules.roster():
        drawn = counter[character.id]
        assert abs(drawn - expected) / expected < 0.25, f"{character.name} 的抽取比例偏离过大：{drawn}"
