"""平衡性对拍工具：批量跑对局，统计角色出场率与胜率，给数值调整提供依据。

用法：
    python -m tools.balance                        # 默认 2000 场
    python -m tools.balance --battles 5000 --seed 7
    python -m tools.balance --draw independent     # 对比旧的各自独立抽池机制

输出：
    - 总览：胜率分布、先手方胜率、平局率、回合数
    - 角色表：抽到率 / 出战率 / 出战胜率（判断强弱的依据）
    - 标签表：出战次数与胜率
    - 提示：明显偏强（>55%）与偏弱（<45%）的角色
"""

from __future__ import annotations

import argparse
import random
from collections import defaultdict

from backend.core import rules
from backend.core import tags as taglib
from backend.core.characters import Character, roster

# 视作「平衡」的胜率区间，超出就会被列进提示
BALANCED_LOW = 0.45
BALANCED_HIGH = 0.55


class Stats:
    def __init__(self) -> None:
        self.battles = 0
        self.draws = 0
        self.team_wins = [0, 0]
        self.first_team_wins = 0
        self.first_team_battles = 0
        self.rounds_total = 0
        self.rounds_max = 0
        self.pooled: dict[int, int] = defaultdict(int)
        self.fielded: dict[int, int] = defaultdict(int)
        self.wins: dict[int, int] = defaultdict(int)
        self.tag_fielded: dict[str, int] = defaultdict(int)
        self.tag_wins: dict[str, int] = defaultdict(int)

    def record(self, pools, teams, result) -> None:
        self.battles += 1
        self.rounds_total += result.rounds
        self.rounds_max = max(self.rounds_max, result.rounds)
        self.first_team_battles += 1
        if result.winner_team is None:
            self.draws += 1
        else:
            self.team_wins[result.winner_team] += 1
            if result.winner_team == result.first_team:
                self.first_team_wins += 1
        for pool in pools:
            for character in pool:
                self.pooled[character.id] += 1
        for team_index, team in enumerate(teams):
            won = result.winner_team == team_index
            for fighter in team:
                self.fielded[fighter.char_id] += 1
                for tag in fighter.tags or ["none"]:
                    self.tag_fielded[tag] += 1
                    if won:
                        self.tag_wins[tag] += 1
                if won:
                    self.wins[fighter.char_id] += 1


def draw_pools(rng: random.Random, independent: bool) -> list[list[Character]]:
    if independent:
        return [rules.generate_pool(rng), rules.generate_pool(rng)]
    return list(rules.generate_pools(rng))


def run(battles: int, seed: int | None, independent: bool) -> Stats:
    rng = random.Random(seed)
    stats = Stats()
    for _ in range(battles):
        pools = draw_pools(rng, independent)
        plans = [rules.random_plan(pool, rng) for pool in pools]
        teams = [rules.build_team(pools[seat], plans[seat], seat) for seat in (0, 1)]
        result = rules.simulate(teams, [plan.strategy for plan in plans], seed=rng.randrange(2**31))
        stats.record(pools, teams, result)
    return stats


def rate(wins: int, total: int) -> float:
    return (wins / total) if total else 0.0


def report(stats: Stats, independent: bool) -> None:
    print(f"抽池机制：{'各自独立抽 6 张（旧）' if independent else '20 张共用抽 12 张后对半切（新）'}")
    print(f"对局场次：{stats.battles}")
    print(
        f"A 队胜 {stats.team_wins[0]} / B 队胜 {stats.team_wins[1]} / 平局 {stats.draws}"
        f"  先手方胜率 {rate(stats.first_team_wins, stats.first_team_battles):.1%}"
    )
    print(f"平均回合数 {stats.rounds_total / max(stats.battles, 1):.1f}，最长 {stats.rounds_max}")
    print()

    print(f"{'#':>3} {'角色':<10} {'标签':<8} {'抽到率':>7} {'出战率':>7} {'出战胜率':>9}")
    rows = []
    for character in roster():
        fielded = stats.fielded.get(character.id, 0)
        rows.append(
            (
                character,
                rate(stats.pooled.get(character.id, 0), stats.battles * 2),
                rate(fielded, stats.battles * 2),
                rate(stats.wins.get(character.id, 0), fielded),
                fielded,
            )
        )
    for character, pool_rate, pick_rate, win_rate, fielded in sorted(rows, key=lambda r: -r[3]):
        tag = taglib.get_spec(character.tag).name
        flag = ""
        if fielded >= 50:
            if win_rate > BALANCED_HIGH:
                flag = "  ← 偏强"
            elif win_rate < BALANCED_LOW:
                flag = "  ← 偏弱"
        print(
            f"{character.id:>3} {character.name:<10} {tag:<8} {pool_rate:>6.1%} {pick_rate:>6.1%} "
            f"{win_rate:>8.1%}{flag}"
        )
    print()

    print(f"{'标签':<10} {'出战次数':>8} {'胜率':>8}")
    for tag, total in sorted(stats.tag_fielded.items(), key=lambda kv: -kv[1]):
        name = "无标签" if tag == "none" else taglib.get_spec(tag).name
        print(f"{name:<10} {total:>8} {rate(stats.tag_wins.get(tag, 0), total):>8.1%}")
    print()

    strong = [r for r in rows if r[4] >= 50 and r[3] > BALANCED_HIGH]
    weak = [r for r in rows if r[4] >= 50 and r[3] < BALANCED_LOW]
    if strong:
        print("偏强：" + "、".join(f"{c.name}({w:.0%})" for c, _, _, w, _ in sorted(strong, key=lambda r: -r[3])))
    if weak:
        print("偏弱：" + "、".join(f"{c.name}({w:.0%})" for c, _, _, w, _ in sorted(weak, key=lambda r: r[3])))
    if not strong and not weak:
        print(f"全部角色都落在 {BALANCED_LOW:.0%} ~ {BALANCED_HIGH:.0%} 的目标区间内。")


def main() -> None:
    parser = argparse.ArgumentParser(description="NeoNovaClash 平衡性对拍")
    parser.add_argument("--battles", type=int, default=2000, help="对局场次，默认 2000")
    parser.add_argument("--seed", type=int, default=20260919, help="随机种子")
    parser.add_argument(
        "--draw",
        choices=["split", "independent"],
        default="split",
        help="抽池机制：split=共用抽池后对半切（默认），independent=各自独立抽池",
    )
    args = parser.parse_args()

    stats = run(args.battles, args.seed, independent=args.draw == "independent")
    report(stats, independent=args.draw == "independent")


if __name__ == "__main__":
    main()
