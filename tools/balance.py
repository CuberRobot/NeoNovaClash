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

from backend.core import constants as C
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


class MatchStats:
    """整场（三局两胜）视角的统计：角色池固定、可选补卡之后，先手优势会不会累积。"""

    def __init__(self) -> None:
        self.matches = 0
        self.match_wins = [0, 0]
        self.sweeps = [0, 0]
        self.rounds_total = 0
        self.first_rounds = 0
        self.first_round_wins = 0
        self.first_counts = [0, 0]        # 各座位在整场里先手的局数分布
        self.all_first_matches = [0, 0]   # 整场每一局都先手的场次
        self.all_first_wins = 0
        # 池子先手值差（A 的最低 3 张之和 - B 的）→ 整场胜负，用来判断"固定池会不会一路压到底"
        self.init_buckets: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        self.rounds = Stats()             # 复用逐局的角色 / 标签统计


def run_matches(matches: int, seed: int | None, growth: bool) -> MatchStats:
    """模拟整场对局：角色池整场固定；growth=True 时第二局起每局补一张（7 选 3 → 8 选 3）。"""

    rng = random.Random(seed)
    stats = MatchStats()
    for _ in range(matches):
        pools = list(rules.generate_pools(rng))
        used = {card.id for pool in pools for card in pool}
        deck = [character for character in roster() if character.id not in used]
        pool_gap = min_pool_initiative(pools[0]) - min_pool_initiative(pools[1])
        scores = [0, 0]
        first_counts = [0, 0]
        played = 0

        while max(scores) < C.ROUNDS_TO_WIN and played < 12:
            played += 1
            plans = [rules.random_plan(pool, rng) for pool in pools]
            teams = [rules.build_team(pools[seat], plans[seat], seat) for seat in (0, 1)]
            result = rules.simulate(teams, [plan.strategy for plan in plans], seed=rng.randrange(2**31))
            stats.rounds.record(pools, teams, result)
            first_counts[result.first_team] += 1
            stats.first_rounds += 1
            if result.winner_team == result.first_team:
                stats.first_round_wins += 1
            if result.winner_team is None:
                pools = _draft(pools, deck, rng, growth)
                continue
            scores[result.winner_team] += 1
            pools = _draft(pools, deck, rng, growth)

        winner = 0 if scores[0] > scores[1] else 1
        stats.matches += 1
        bucket = (
            "A 池子能更低先手（-6 以下）"
            if pool_gap <= -6
            else "A 略低（-5 ~ -1）"
            if pool_gap < 0
            else "基本持平（0）"
            if pool_gap == 0
            else "B 略低（+1 ~ +5）"
            if pool_gap <= 5
            else "B 池子能更低先手（+6 以上）"
        )
        stats.init_buckets[bucket][0] += 1
        stats.init_buckets[bucket][1] += 1 if winner == 0 else 0
        stats.rounds_total += played
        stats.match_wins[winner] += 1
        if scores[winner] == C.ROUNDS_TO_WIN and scores[1 - winner] == 0:
            stats.sweeps[winner] += 1
        for seat in (0, 1):
            if first_counts[seat] == played:
                stats.all_first_matches[seat] += 1
                if winner == seat:
                    stats.all_first_wins += 1
    return stats


def _draft(pools, deck, rng, growth: bool):
    """第二局起给双方各发 3 张候选、各补 1 张进池（与房间层同一套规则）。"""

    if not growth or len(deck) < 3 * len(pools):
        return pools
    grown = list(pools)
    for seat in range(len(pools)):
        candidates = rng.sample(deck, 3)
        for character in candidates:
            deck.remove(character)
        pick = rng.choice(candidates)
        grown[seat] = [*grown[seat], rules.make_pool_card(pick, rng)]
        deck.extend(character for character in candidates if character is not pick)
    return grown


def min_pool_initiative(pool) -> int:
    """池子里最低的 3 张先手值之和：这名玩家"最容易抢到先手"的程度。"""

    values = sorted(card.initiative for card in pool)
    return sum(values[:3])


def report_matches(stats: MatchStats, growth: bool) -> None:
    draft = "＋每局补 1 张（7 选 3 → 8 选 3）" if growth else "，不补卡"
    print(f"对局形态：整场三局两胜，角色池整场固定{draft}")
    print(f"整场场次：{stats.matches}（平均 {stats.rounds_total / max(stats.matches, 1):.2f} 局分出胜负）")
    print(
        f"A 队拿下整场 {rate(stats.match_wins[0], stats.matches):.1%} / "
        f"B 队 {rate(stats.match_wins[1], stats.matches):.1%}"
        f"  2:0 直落比例 {rate(stats.sweeps[0] + stats.sweeps[1], stats.matches):.1%}"
    )
    print(
        f"逐局先手方胜率 {rate(stats.first_round_wins, stats.first_rounds):.1%}"
        f"  ← 对照：旧版每局重抽池子时约 55%"
    )
    same_first = stats.all_first_matches[0] + stats.all_first_matches[1]
    print(
        f"整场每局都先手的场次 {same_first / max(stats.matches, 1):.1%}"
        f"（其中先手方拿下整场 {rate(stats.all_first_wins, same_first):.1%}）"
    )
    print()
    print(f"{'池子先手值（最低 3 张之和）':<26} {'场次':>6} {'A 拿下整场':>10}")
    for bucket in (
        "A 池子能更低先手（-6 以下）",
        "A 略低（-5 ~ -1）",
        "基本持平（0）",
        "B 略低（+1 ~ +5）",
        "B 池子能更低先手（+6 以上）",
    ):
        total, wins = stats.init_buckets[bucket]
        print(f"{bucket:<26} {total:>6} {rate(wins, total):>9.1%}")
    print()
    report(stats.rounds, independent=False)


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
    parser.add_argument("--matches", type=int, default=0, help="模拟整场对局的场次（三局两胜）")
    parser.add_argument("--no-growth", action="store_true", help="整场模拟里关闭补卡，用于对照")
    args = parser.parse_args()

    if args.matches:
        stats = run_matches(args.matches, args.seed, growth=not args.no_growth)
        report_matches(stats, growth=not args.no_growth)
        return

    stats = run(args.battles, args.seed, independent=args.draw == "independent")
    report(stats, independent=args.draw == "independent")


if __name__ == "__main__":
    main()
