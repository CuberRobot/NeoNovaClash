"""出手顺序的守卫测试。

规则：**位次优先，同位次由先手方先动**。
双方都按第 1 位 → 第 2 位 → 第 3 位的顺序出手，每一对位次里先手方先打，
所以先手方是 A 时顺序是 A1,B1,A2,B2,A3,B3，先手方是 B 时就是 B1,A1,B2,A2,B3,A3。
"""

from __future__ import annotations

from tests.helpers import make_battle, make_fighter

BALANCE_E = 14        # 均衡战士E：4/33/3，够耐打，能撑满一个回合
CRYSTAL_GUARD = 17    # 晶壁守卫：4/31/3
ARCHER = 9            # 风行射手：8/20/6，用于构造"先手值更高"的一方


def build_battle(seed: int = 20260101):
    team_a = [make_fighter(BALANCE_E, 0, slot) for slot in range(3)]
    team_b = [make_fighter(CRYSTAL_GUARD, 1, slot) for slot in range(3)]
    return make_battle(team_a, team_b, seed=seed)


def first_round_actions(result) -> list[str]:
    """取第一回合里所有"轮到某个人"的事件（攻击与跳过），按发生顺序返回位置标记。"""

    markers: list[str] = []
    in_first_round = False
    for event in result.events:
        if event.kind == "round_start":
            if in_first_round:
                break
            in_first_round = True
            continue
        if not in_first_round:
            continue
        if event.kind in ("attack", "clash", "skip"):
            markers.append(event.text.split(" ")[0])
    return markers


def test_action_order_is_slot_first_then_first_mover():
    result = build_battle().run()
    first = "A" if result.first_team == 0 else "B"
    second = "B" if first == "A" else "A"

    assert first_round_actions(result) == [
        f"{first}1",
        f"{second}1",
        f"{first}2",
        f"{second}2",
        f"{first}3",
        f"{second}3",
    ]


def test_lower_total_initiative_moves_first():
    # A 队先手值总和更低（3×3=9 对 3×6=18）→ A 先手；顺序应该是 A1,B1,A2,B2,A3,B3
    team_a = [make_fighter(BALANCE_E, 0, slot) for slot in range(3)]
    team_b = [make_fighter(ARCHER, 1, slot) for slot in range(3)]
    result = make_battle(team_a, team_b, seed=7).run()

    assert result.total_initiative[0] < result.total_initiative[1]
    assert result.first_team == 0
    assert first_round_actions(result) == ["A1", "B1", "A2", "B2", "A3", "B3"]
