"""三种游戏模式的规则测试：标准 / 混沌 / 大战场。"""

from __future__ import annotations

import random

import pytest

from backend.core import modes, rules
from backend.core import tags as taglib
from backend.core.models import BONUS_ATK, Bonus, Plan, Strategy

ALL_MODES = list(modes.available_modes())


def test_mode_configs_are_self_consistent():
    for mode in ALL_MODES:
        assert mode.pool_size >= mode.team_size
        assert mode.bonus_per_round <= mode.team_size * mode.max_bonus_per_fighter
        assert mode.pool_size * 2 <= len(rules.roster())
        if mode.random_tags:
            assert mode.tags_per_player > 0
            assert mode.excluded_tags


def test_unknown_mode_is_rejected():
    with pytest.raises(modes.ModeError):
        modes.get_mode("nightmare")


@pytest.mark.parametrize("mode_key", [mode.key for mode in ALL_MODES])
def test_every_mode_can_run_a_full_battle(mode_key: str):
    mode = modes.get_mode(mode_key)
    rng = random.Random(hash(mode_key) % 1000)

    pools = rules.generate_pools(rng, mode)
    plans = [rules.random_plan(pool, rng, mode) for pool in pools]
    for pool, plan in zip(pools, plans, strict=True):
        rules.validate_plan(pool, plan, mode)

    teams = [rules.build_team(pools[seat], plans[seat], seat, mode) for seat in (0, 1)]
    assert all(len(team) == mode.team_size for team in teams)
    result = rules.simulate(teams, [plan.strategy for plan in plans], seed=2026)
    assert result.winner_team in (0, 1)
    assert result.events


def test_chaos_mode_assigns_four_random_tags_per_pool():
    mode = modes.get_mode("chaos")
    rng = random.Random(11)
    allowed = {tag for tag in taglib.TAGS if tag != "none" and tag not in mode.excluded_tags}

    for _ in range(80):
        pools = rules.generate_pools(rng, mode)
        for pool in pools:
            tags = [tag for card in pool for tag in card.tags]
            assert len(tags) == mode.tags_per_player
            assert set(tags) <= allowed
            for card in pool:
                assert len(card.tags) <= mode.max_tags_per_character
                for first in card.tags:
                    for second in card.tags:
                        if first != second:
                            assert not modes.tags_conflict(first, second)


def test_chaos_mode_excludes_explosive_content():
    mode = modes.get_mode("chaos")
    rng = random.Random(5)

    for _ in range(50):
        for pool in rules.generate_pools(rng, mode):
            assert all(card.id not in mode.excluded_characters for card in pool)
            assert all("explosive" not in card.tags for card in pool)


def test_chaos_tags_can_differ_from_the_character_default():
    """混沌模式的核心是角色与标签脱钩：同一个角色在不同局可能拿到别的标签。"""

    mode = modes.get_mode("chaos")
    rng = random.Random(3)
    mismatches = 0
    total = 0
    for _ in range(40):
        for pool in rules.generate_pools(rng, mode):
            for card in pool:
                total += 1
                if card.tags != ((card.character.tag,) if card.character.tag != "none" else ()):
                    mismatches += 1

    assert mismatches > total * 0.3


def test_big_battlefield_uses_larger_pools_and_six_bonuses():
    mode = modes.get_mode("big_battlefield")
    rng = random.Random(7)
    pools = rules.generate_pools(rng, mode)

    assert all(len(pool) == 9 for pool in pools)
    pool = pools[0]
    plan = rules.random_plan(pool, rng, mode)
    assert len(plan.selection) == 5
    assert len(plan.bonuses) == 6
    rules.validate_plan(pool, plan, mode)

    # 3 人方案在大战场模式下不合法
    small_plan = Plan(
        selection=tuple(card.id for card in pool[:3]),
        bonuses=tuple(Bonus(slot=i % 5 + 1, kind=BONUS_ATK) for i in range(6)),
        strategy=Strategy(),
    )
    with pytest.raises(rules.RuleError, match="5 名角色"):
        rules.validate_plan(pool, small_plan, mode)


def test_plan_limits_follow_the_mode():
    chaos = modes.get_mode("chaos")
    rng = random.Random(13)
    pool = rules.generate_pools(rng, chaos)[0]

    # 4 次增益在混沌模式合法，5 次不合法
    ok = Plan(
        selection=tuple(card.id for card in pool[:3]),
        bonuses=tuple(Bonus(slot=(i % 3) + 1, kind=BONUS_ATK) for i in range(4)),
        strategy=Strategy(),
    )
    rules.validate_plan(pool, ok, chaos)
    too_many = Plan(selection=ok.selection, bonuses=ok.bonuses + (Bonus(slot=1, kind=BONUS_ATK),), strategy=Strategy())
    with pytest.raises(rules.RuleError, match="4 次增益"):
        rules.validate_plan(pool, too_many, chaos)


def test_pool_payload_exposes_multiple_tags():
    mode = modes.get_mode("chaos")
    rng = random.Random(21)
    payload = rules.pool_payload(rules.generate_pools(rng, mode)[0])

    assert len(payload) == mode.pool_size
    for card in payload:
        assert isinstance(card["tags"], list)
        assert len(card["tag_names"]) == len(card["tags"])
        assert "place_first" in card
