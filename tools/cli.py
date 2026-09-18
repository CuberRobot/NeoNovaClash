"""命令行热座对战：不打开浏览器也能验证规则与战斗表现。

用法：
    python -m tools.cli               # 交互式，两名玩家轮流输入
    python -m tools.cli --auto 20     # 随机方案自动跑 20 场，用于平衡性抽样
"""

from __future__ import annotations

import argparse
import random

from backend.core import constants as C
from backend.core import rules
from backend.core.characters import Character
from backend.core.models import BONUS_ATK, BONUS_HP, Bonus, Fighter, Plan, Strategy


def show_pool(seat: int, pool: list[Character]) -> None:
    print(f"\n=== 玩家 {seat + 1} 的角色池 ===")
    for index, character in enumerate(pool, start=1):
        print(
            f"  {index}. {character.name:<8} ATK {character.atk:>2}  HP {character.hp:>2}  "
            f"先手 {character.initiative}  标签 {character.tag}"
        )


def read_plan(seat: int, pool: list[Character]) -> Plan:
    """交互式读取一份方案，输入不合法时提示重来。"""

    while True:
        show_pool(seat, pool)
        try:
            raw = input("按出击顺序输入 3 个角色序号（如 2 5 1）：").strip()
            indexes = [int(part) for part in raw.split()]
            selection = tuple(pool[i - 1].id for i in indexes)

            bonuses: list[Bonus] = []
            print(f"需要分配 {C.BONUS_PER_ROUND} 次增益，格式：出击位 类型（1=攻击+2 / 2=生命+4），每行一次")
            for _ in range(C.BONUS_PER_ROUND):
                raw_bonus = input("  增益> ").strip().split()
                slot = int(raw_bonus[0])
                kind = BONUS_ATK if raw_bonus[1] == "1" else BONUS_HP
                bonuses.append(Bonus(slot=slot, kind=kind))

            strategy = Strategy(kind="lowest_hp")
            plan = Plan(selection=selection, bonuses=tuple(bonuses), strategy=strategy)
            rules.validate_plan(pool, plan)
            return plan
        except (ValueError, IndexError, rules.RuleError) as exc:
            print(f"输入有误：{exc}，请重新输入\n")


def describe_team(team: list[Fighter]) -> str:
    parts = []
    for fighter in team:
        tags = f"（{'、'.join(fighter.tags)}）" if fighter.tags else ""
        parts.append(f"{fighter.position} {fighter.name} {fighter.atk}/{fighter.max_hp}{tags}")
    return " | ".join(parts)


def run_interactive() -> None:
    rng = random.Random()
    pools = [rules.generate_pool(rng), rules.generate_pool(rng)]
    plans = [read_plan(0, pools[0]), read_plan(1, pools[1])]
    play(pools, plans, rng.randrange(2**31))


def play(pools: list[list[Character]], plans: list[Plan], seed: int) -> int | None:
    teams = [rules.build_team(pools[seat], plans[seat], seat) for seat in (0, 1)]
    for seat in (0, 1):
        print(f"\n玩家 {seat + 1} 出战：{describe_team(teams[seat])}")
    print(f"\n攻击策略：A队 {plans[0].strategy.describe()} / B队 {plans[1].strategy.describe()}")

    result = rules.simulate(teams, [plans[0].strategy, plans[1].strategy], seed=seed)
    print(f"\n种子 {seed}\n")
    for event in result.events:
        print(f"[{event.round:>2}] {event.text}")
    print(f"\n结果：{'平局' if result.winner_team is None else f'玩家 {result.winner_team + 1} 获胜'}"
          f"（{result.reason}，共 {result.rounds} 回合）")
    return result.winner_team


def run_auto(count: int, seed: int | None) -> None:
    rng = random.Random(seed)
    score = [0, 0, 0]
    for index in range(count):
        pools = [rules.generate_pool(rng), rules.generate_pool(rng)]
        plans = [rules.random_plan(pools[0], rng), rules.random_plan(pools[1], rng)]
        result = rules.simulate(
            [rules.build_team(pools[seat], plans[seat], seat) for seat in (0, 1)],
            [plans[0].strategy, plans[1].strategy],
            seed=rng.randrange(2**31),
        )
        score[0 if result.winner_team is None else result.winner_team + 1] += 1
        winner = "平局" if result.winner_team is None else f"{'AB'[result.winner_team]} 队胜"
        print(f"第 {index + 1:>3} 场：{winner}（{result.reason}，{result.rounds} 回合）")
    total = sum(score)
    print(f"\n共 {total} 场 —— 平局 {score[0]} / A 胜 {score[1]} / B 胜 {score[2]}")
    if total:
        print(f"先手方胜率参考：{score[1] / total:.1%}（A 队）")


def main() -> None:
    parser = argparse.ArgumentParser(description="NeoNovaClash 命令行对战工具")
    parser.add_argument("--auto", type=int, metavar="N", help="随机自动对拍 N 场")
    parser.add_argument("--seed", type=int, default=None, help="随机种子，便于复现")
    args = parser.parse_args()

    if args.auto:
        run_auto(args.auto, args.seed)
    else:
        run_interactive()


if __name__ == "__main__":
    main()
