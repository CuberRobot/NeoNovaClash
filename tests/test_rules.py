"""规则层测试：角色池、方案校验与阵容构建。"""

from __future__ import annotations

import random

import pytest

from backend.core import constants as C
from backend.core import rules
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
