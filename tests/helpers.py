"""测试用的构造工具：直接拼装战士与小型战斗，避免被完整规则挡住。"""

from __future__ import annotations

from backend.core import tags as taglib
from backend.core.characters import get_character
from backend.core.engine import Battle
from backend.core.models import STRATEGY_LOWEST_HP, Fighter, Strategy


def make_fighter(
    char_id: int,
    team: int = 0,
    slot: int = 0,
    *,
    atk: int | None = None,
    hp: int | None = None,
    tags: list[str] | None = None,
) -> Fighter:
    """按角色编号造一个战士，可覆盖攻击 / 生命 / 标签，便于定向测试标签效果。"""

    character = get_character(char_id)
    tag_list = [character.tag] if character.tag != "none" else []
    if tags is not None:
        tag_list = list(tags)
    final_atk = character.atk if atk is None else atk
    final_hp = character.hp if hp is None else hp
    return Fighter(
        uid=f"t{team}s{slot}-{char_id}",
        char_id=character.id,
        name=character.name,
        team=team,
        slot=slot,
        base_atk=character.atk,
        base_hp=character.hp,
        atk=final_atk,
        max_hp=final_hp,
        hp=final_hp,
        initiative=character.initiative,
        tags=tag_list,
        role=character.role,
        domain=character.domain,
        uses_left=taglib.initial_uses(tag_list),
    )


def make_battle(
    team_a: list[Fighter],
    team_b: list[Fighter],
    *,
    seed: int = 20260101,
    strategy_a: Strategy | None = None,
    strategy_b: Strategy | None = None,
) -> Battle:
    strategies = [
        strategy_a or Strategy(kind=STRATEGY_LOWEST_HP),
        strategy_b or Strategy(kind=STRATEGY_LOWEST_HP),
    ]
    return Battle([team_a, team_b], strategies, seed)


def damage_events(battle: Battle, attacker_uid: str | None = None) -> list[dict]:
    """取出伤害事件，可按攻击者过滤。"""

    result = []
    for event in battle.events:
        if event.kind != "damage":
            continue
        if attacker_uid is not None and event.data.get("attacker") != attacker_uid:
            continue
        result.append(event.data)
    return result


def event_kinds(battle: Battle) -> list[str]:
    return [event.kind for event in battle.events]
