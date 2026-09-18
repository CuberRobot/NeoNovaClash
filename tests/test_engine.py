"""战斗引擎的标签效果测试：每个标签都对应一份可以直接对拍的期望。"""

from __future__ import annotations

from backend.core import constants as C
from backend.core.models import (
    STRATEGY_HIGHEST_ATK,
    STRATEGY_TAG_PRIORITY,
    Strategy,
)
from tests.helpers import damage_events, event_kinds, make_battle, make_fighter

# 角色编号速查（与 backend/core/characters.py 一致）
BALANCE_A = 1
BALANCE_B = 2
EXPLOSIVE = 4
CURSE = 5
NECROMANCER = 6
HEAVY_ARMOR = 7
SHIELD = 8
ARCHER = 9
BERSERKER = 10
POISONER = 11
ARTILLERY = 12


def test_battle_is_reproducible_with_same_seed():
    def build_battle():
        # 战斗会直接修改 Fighter 对象，所以每场都要重建阵容
        return make_battle(
            [make_fighter(BALANCE_A, 0, 0), make_fighter(ARCHER, 0, 1)],
            [make_fighter(BERSERKER, 1, 0), make_fighter(HEAVY_ARMOR, 1, 1)],
            seed=99,
        )

    first = build_battle().run()
    second = build_battle().run()

    assert [e.text for e in first.events] == [e.text for e in second.events]
    assert first.winner_team == second.winner_team
    assert first.rounds == second.rounds


def test_initiative_uses_total_and_lower_goes_first():
    # 先手值：均衡战士A=5、狂战士=5 → 平手；铁甲卫士=6 让 B 队总和更高
    team_a = [make_fighter(BALANCE_A, 0, 0)]
    team_b = [make_fighter(HEAVY_ARMOR, 1, 0)]
    battle = make_battle(team_a, team_b)
    battle._battle_start()
    assert battle.order == [0, 1]


def test_berserk_multiplies_damage_below_threshold():
    berserker = make_fighter(BERSERKER, 0, 0, hp=C.BERSERK_THRESHOLD)
    target = make_fighter(BALANCE_A, 1, 0, hp=99)
    battle = make_battle([berserker], [target])
    battle.run()

    expected = berserker.atk * C.BERSERK_SCALE[0] // C.BERSERK_SCALE[1]
    assert expected in [d["amount"] for d in damage_events(battle, berserker.uid)]


def test_berserk_not_triggered_above_threshold():
    berserker = make_fighter(BERSERKER, 0, 0, hp=C.BERSERK_THRESHOLD + 1)
    target = make_fighter(BALANCE_A, 1, 0, hp=99)
    battle = make_battle([berserker], [target])
    battle.run()

    assert berserker.atk in [d["amount"] for d in damage_events(battle, berserker.uid)]


def test_heavy_armor_reduces_big_hits_only():
    attacker = make_fighter(BALANCE_A, 0, 0, atk=10)
    tank = make_fighter(HEAVY_ARMOR, 1, 0)
    battle = make_battle([attacker], [tank])
    battle.run()

    expected = 10 * C.HEAVY_ARMOR_SCALE[0] // C.HEAVY_ARMOR_SCALE[1]
    assert expected in [d["amount"] for d in damage_events(battle, attacker.uid)]


def test_heavy_armor_ignores_small_hits():
    attacker = make_fighter(BALANCE_A, 0, 0, atk=C.HEAVY_ARMOR_THRESHOLD - 1)
    tank = make_fighter(HEAVY_ARMOR, 1, 0)
    battle = make_battle([attacker], [tank])
    battle.run()

    assert (C.HEAVY_ARMOR_THRESHOLD - 1) in [d["amount"] for d in damage_events(battle, attacker.uid)]


def test_explosive_kills_fragile_target_and_self():
    bomber = make_fighter(EXPLOSIVE, 0, 0)
    fragile = make_fighter(BALANCE_A, 1, 0, hp=C.EXPLOSIVE_KILL_MAX_HP)
    battle = make_battle([bomber], [fragile])
    battle.run()

    assert not fragile.alive
    assert not bomber.alive
    assert any(e.data.get("lethal") for e in battle.events if e.kind == "damage")


def test_explosive_deals_normal_damage_to_sturdy_target():
    bomber = make_fighter(EXPLOSIVE, 0, 0)
    sturdy = make_fighter(HEAVY_ARMOR, 1, 0)  # 最大生命 30 > 26
    battle = make_battle([bomber], [sturdy])
    battle.run()

    expected = bomber.atk * C.HEAVY_ARMOR_SCALE[0] // C.HEAVY_ARMOR_SCALE[1]
    assert [d["amount"] for d in damage_events(battle, bomber.uid)] == [expected]
    assert not bomber.alive
    assert sturdy.alive


def test_explosive_vs_explosive_mutual_destruction():
    bomber_a = make_fighter(EXPLOSIVE, 0, 0)
    bomber_b = make_fighter(EXPLOSIVE, 1, 0)
    battle = make_battle([bomber_a], [bomber_b])
    battle.run()

    assert not bomber_a.alive and not bomber_b.alive
    assert "clash" in event_kinds(battle)


def test_explosive_damage_ignores_shield():
    bomber = make_fighter(EXPLOSIVE, 0, 0)
    fragile = make_fighter(BALANCE_A, 1, 0)
    shield_bearer = make_fighter(SHIELD, 1, 1)
    battle = make_battle([bomber], [fragile, shield_bearer])
    battle.run()

    assert not fragile.alive
    assert shield_bearer.hp == shield_bearer.max_hp
    assert "shield" not in event_kinds(battle)


def test_necromancy_revives_ally_once_per_charge():
    ally = make_fighter(BALANCE_A, 0, 0)
    necromancer = make_fighter(NECROMANCER, 0, 1)
    battle = make_battle([ally, necromancer], [make_fighter(BALANCE_B, 1, 0)])
    battle._battle_start()

    battle.kill(ally, killer=None, reason="测试")

    expected_hp = ally.max_hp * C.NECROMANCY_SCALE[0] // C.NECROMANCY_SCALE[1]
    assert ally.alive and ally.hp == expected_hp
    assert necromancer.uses_left["necromancy"] == C.NECROMANCY_CHARGES - 1


def test_necromancy_cannot_revive_explosive():
    bomber = make_fighter(EXPLOSIVE, 0, 0)
    necromancer = make_fighter(NECROMANCER, 0, 1)
    battle = make_battle([bomber, necromancer], [make_fighter(BALANCE_B, 1, 0)])
    battle._battle_start()

    battle.kill(bomber, killer=None, reason="测试")

    assert not bomber.alive
    assert necromancer.uses_left["necromancy"] == C.NECROMANCY_CHARGES


def test_necromancy_stops_after_death():
    ally = make_fighter(BALANCE_A, 0, 0)
    necromancer = make_fighter(NECROMANCER, 0, 1)
    battle = make_battle([ally, necromancer], [make_fighter(BALANCE_B, 1, 0)])
    battle._battle_start()

    battle.kill(necromancer, killer=None, reason="测试")
    battle.kill(ally, killer=None, reason="测试")

    assert not ally.alive


def test_curse_strips_tags_but_skips_curse_and_explosive():
    wizard = make_fighter(CURSE, 0, 0)
    battle = make_battle(
        [wizard, make_fighter(BALANCE_A, 0, 1)],
        [make_fighter(BERSERKER, 1, 0), make_fighter(EXPLOSIVE, 1, 1)],
    )
    battle._battle_start()

    cursed = [e for e in battle.events if e.kind == "curse"]
    assert len(cursed) == 1
    assert cursed[0].data["lost_tags"] == ["berserk"]
    assert battle.find("t1s1-4").has("explosive")


def test_shield_shares_half_damage_and_consumes_charges():
    attacker = make_fighter(BALANCE_A, 1, 0, atk=10)
    fragile = make_fighter(BALANCE_B, 0, 0)
    bearer = make_fighter(SHIELD, 0, 1)
    battle = make_battle([fragile, bearer], [attacker])
    battle._battle_start()

    battle.resolve_attack(attacker, fragile)

    assert fragile.max_hp - fragile.hp == 5
    assert bearer.max_hp - bearer.hp == 5
    assert bearer.uses_left["shield"] == C.SHIELD_CHARGES - 1


def test_shield_runs_out_of_charges():
    attacker = make_fighter(BALANCE_A, 1, 0, atk=10)
    fragile = make_fighter(BALANCE_B, 0, 0, hp=200)
    bearer = make_fighter(SHIELD, 0, 1, hp=200)
    battle = make_battle([fragile, bearer], [attacker])
    battle._battle_start()

    for _ in range(C.SHIELD_CHARGES + 2):
        fragile.hp = fragile.max_hp
        bearer.hp = bearer.max_hp
        battle.resolve_attack(attacker, fragile)

    assert bearer.uses_left["shield"] == 0
    assert len([e for e in battle.events if e.kind == "shield"]) == C.SHIELD_CHARGES
    assert fragile.max_hp - fragile.hp == 10  # 护盾用尽后全额承伤


def test_pierce_follows_up_on_fragile_target():
    archer = make_fighter(ARCHER, 0, 0)
    fragile = make_fighter(BALANCE_A, 1, 0, hp=C.FRAGILE_MAX_HP)
    other = make_fighter(BALANCE_B, 1, 1, hp=99)
    battle = make_battle([archer], [fragile, other])
    battle._battle_start()

    battle.resolve_attack(archer, fragile)

    expected = archer.atk * C.PIERCE_SCALE[0] // C.PIERCE_SCALE[1]
    follow_ups = [d for d in damage_events(battle, archer.uid) if d["follow_up"]]
    assert [d["amount"] for d in follow_ups] == [expected]
    assert "pierce" in event_kinds(battle)


def test_pierce_does_not_trigger_on_sturdy_target():
    archer = make_fighter(ARCHER, 0, 0)
    sturdy = make_fighter(HEAVY_ARMOR, 1, 0, hp=99)  # 最大生命 30 > 24
    other = make_fighter(BALANCE_B, 1, 1, hp=99)
    battle = make_battle([archer], [sturdy, other])
    battle._battle_start()

    battle.resolve_attack(archer, sturdy)

    assert "pierce" not in event_kinds(battle)


def test_poison_stacks_are_capped_and_tick_every_round():
    poisoner = make_fighter(POISONER, 0, 0)
    victim = make_fighter(BALANCE_B, 1, 0, hp=200)
    battle = make_battle([poisoner], [victim])
    battle._battle_start()

    for _ in range(C.POISON_MAX_STACKS + 2):
        battle.apply_poison(poisoner, victim)

    assert len(victim.poison) == C.POISON_MAX_STACKS
    battle.round = 1
    battle._resolve_poison_phase()
    assert victim.max_hp - victim.hp == C.POISON_DAMAGE * C.POISON_MAX_STACKS
    assert len(victim.poison) == C.POISON_MAX_STACKS  # 只减持续轮数，不减层数


def test_poison_expires_after_duration():
    poisoner = make_fighter(POISONER, 0, 0)
    victim = make_fighter(BALANCE_B, 1, 0, hp=200)
    battle = make_battle([poisoner], [victim])
    battle._battle_start()

    battle.apply_poison(poisoner, victim)
    for _ in range(C.POISON_DURATION):
        battle._resolve_poison_phase()

    assert victim.poison == []
    assert victim.max_hp - victim.hp == C.POISON_DAMAGE * C.POISON_DURATION


def test_aoe_hits_every_living_enemy():
    artillery = make_fighter(ARTILLERY, 0, 0)
    enemies = [make_fighter(BALANCE_A, 1, 0), make_fighter(BALANCE_B, 1, 1), make_fighter(BALANCE_B, 1, 2)]
    battle = make_battle([artillery, make_fighter(BALANCE_A, 0, 1)], enemies)
    battle._battle_start()

    battle._take_action(artillery)

    hits = damage_events(battle, artillery.uid)
    assert len(hits) == 3
    assert {h["target"] for h in hits} == {f.uid for f in enemies}


def test_target_strategy_highest_atk():
    attacker = make_fighter(BALANCE_A, 0, 0)
    weak = make_fighter(BALANCE_A, 1, 0, atk=1)
    strong = make_fighter(BALANCE_B, 1, 1, atk=20)
    battle = make_battle([attacker], [weak, strong], strategy_a=Strategy(kind=STRATEGY_HIGHEST_ATK))
    battle._battle_start()

    assert battle.choose_target(attacker) is strong


def test_target_strategy_tag_priority():
    attacker = make_fighter(BALANCE_A, 0, 0)
    plain = make_fighter(BALANCE_A, 1, 0, hp=1)
    tagged = make_fighter(BERSERKER, 1, 1, hp=99)
    strategy = Strategy(kind=STRATEGY_TAG_PRIORITY, tag="berserk")
    battle = make_battle([attacker], [plain, tagged], strategy_a=strategy)
    battle._battle_start()

    assert battle.choose_target(attacker) is tagged


def test_battle_ends_by_timeout_when_teams_never_die(monkeypatch):
    monkeypatch.setattr(C, "MAX_ROUNDS_PER_DUEL", 2)
    tank_a = make_fighter(HEAVY_ARMOR, 0, 0, atk=0, hp=100)
    tank_b = make_fighter(HEAVY_ARMOR, 1, 0, atk=0, hp=50)
    battle = make_battle([tank_a], [tank_b])

    result = battle.run()

    assert result.winner_team == 0
    assert "最大回合数" in result.reason


def test_full_battle_produces_readable_report():
    battle = make_battle(
        [make_fighter(ARTILLERY, 0, 0), make_fighter(HEAVY_ARMOR, 0, 1), make_fighter(NECROMANCER, 0, 2)],
        [make_fighter(ARCHER, 1, 0), make_fighter(BERSERKER, 1, 1), make_fighter(SHIELD, 1, 2)],
    )
    result = battle.run()

    kinds = set(event_kinds(battle))
    assert {"battle_start", "round_start", "attack", "damage", "death", "battle_end"} <= kinds
    assert result.winner_team in (0, 1)
    assert all(event.text for event in result.events)
    assert len(result.teams) == 2 and len(result.teams[0]) == 3
