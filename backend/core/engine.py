"""战斗引擎。

设计目标：
- 纯计算：给定（双方阵容 + 策略 + seed）必然得到同一份战报，方便复现、测试与平衡性分析。
- 无副作用：不读文件、不连网络、不依赖 Web 层，可以被命令行、测试与服务端同时复用。
- 可读的结算顺序：所有标签效果挂在固定阶段，见「结算顺序」。

结算顺序（与 docs/规则设定.md 保持一致）：
1. 开战：诅咒巫师剥夺标签 → 判定先手
2. 每轮开始：中毒统一结算
3. 按出击位依次行动：A1 → B1 → A2 → B2 → A3 → B3（先手方先动）
4. 单次攻击内部：攻击方修正（狂暴）→ 自爆判定 → 护盾分担 → 受击方减伤（重装盔甲）
   → 扣血 → 命中后效果（中毒 / 穿透）→ 自爆反噬
5. 任一阶段出现阵亡立即处理复活，并在任一方全灭时立刻结束战斗

注意：战斗会直接修改传入的 Fighter 对象（扣血、施加中毒等）。
需要重放同一场战斗时请重新构建阵容，rules.build_team() 每次都会返回新的对象。
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from . import constants as C
from . import tags as taglib
from .models import (
    STRATEGY_HIGHEST_ATK,
    STRATEGY_TAG_PRIORITY,
    BattleEvent,
    BattleResult,
    Fighter,
    Strategy,
)


@dataclass(frozen=True)
class HitContext:
    """一次伤害结算的上下文，用来区分主攻击、追击、护盾转移与自爆伤害。"""

    follow_up: bool = False      # 来自箭矢穿透的追击伤害
    explosive: bool = False      # 来自自爆步兵的伤害
    transferred: bool = False    # 由护盾部署者承担后落在其身上的伤害


# 默认上下文做成单例，避免在函数默认参数里反复构造对象
DEFAULT_HIT_CONTEXT = HitContext()


class Battle:
    """一场战斗的运行时状态机。"""

    def __init__(self, teams: list[list[Fighter]], strategies: list[Strategy], seed: int) -> None:
        if len(teams) != 2:
            raise ValueError("当前只支持两支队伍对战")
        self.teams = teams
        self.strategies = strategies
        self.seed = seed
        self.rng = random.Random(seed)
        self.events: list[BattleEvent] = []
        self.round = 0
        self.finished = False
        self.winner: int | None = None
        self.reason = ""
        self.total_initiative = [sum(f.initiative for f in team) for team in teams]
        self.order = self._decide_order()
        self._seq = 0

    # ------------------------------------------------------------ 事件输出
    def log(self, kind: str, text: str, **data) -> None:
        """记录一条战斗事件。超过上限后只保留计数，避免异常情况撑爆内存。"""

        self._seq += 1
        if len(self.events) >= C.MAX_BATTLE_EVENTS:
            return
        self.events.append(BattleEvent(seq=self._seq, round=self.round, kind=kind, text=text, data=data))

    # ------------------------------------------------------------ 查询工具
    def alive(self, team: int) -> list[Fighter]:
        return [f for f in self.teams[team] if f.alive]

    def is_wiped(self, team: int) -> bool:
        return not self.alive(team)

    def opponent_team(self, team: int) -> int:
        return 1 - team

    def strategy_of(self, team: int) -> Strategy:
        return self.strategies[team]

    def find(self, uid: str) -> Fighter:
        for team in self.teams:
            for fighter in team:
                if fighter.uid == uid:
                    return fighter
        raise KeyError(uid)

    # ------------------------------------------------------------ 开战阶段
    def _decide_order(self) -> list[int]:
        if self.total_initiative[0] < self.total_initiative[1]:
            return [0, 1]
        if self.total_initiative[1] < self.total_initiative[0]:
            return [1, 0]
        if self.rng.random() < 0.5:
            return [0, 1]
        return [1, 0]

    def _battle_start(self) -> None:
        first = self.order[0]
        if self.total_initiative[0] == self.total_initiative[1]:
            reason = "双方先手值相同，随机决定先手"
        else:
            reason = "先手值总和较低的一方先手"
        self.log(
            "battle_start",
            f"开战：第 {'AB'[first]} 队先手（{reason}，先手值 {self.total_initiative[0]} : {self.total_initiative[1]}）",
            seed=self.seed,
            first_team=first,
            total_initiative=list(self.total_initiative),
            teams=[[f.snapshot() for f in team] for team in self.teams],
        )
        # 诅咒巫师在开战瞬间剥夺标签
        for team in (0, 1):
            for fighter in self.teams[team]:
                for runtime in taglib.runtime_for(fighter):
                    if runtime.on_battle_start is not None:
                        runtime.on_battle_start(self, fighter)

    def resolve_curse(self, owner: Fighter) -> None:
        """标签剥夺：随机剥夺敌方一名仍有标签的角色（不含诅咒巫师与自爆步兵）。"""

        enemies = self.alive(self.opponent_team(owner.team))
        pool = []
        for enemy in enemies:
            if not enemy.has_tags:
                continue
            if all(taglib.get_spec(tag).curseable for tag in enemy.tags):
                pool.append(enemy)
        if not pool:
            self.log("curse", f"{owner.position} {owner.name} 未找到可剥夺标签的目标", owner=owner.uid)
            return
        target = self.rng.choice(pool)
        lost = list(target.tags)
        target.lost_tags.extend(lost)
        target.tags.clear()
        names = "、".join(taglib.tag_names(lost))
        self.log(
            "curse",
            f"{owner.position} {owner.name} 剥夺了 {target.position} {target.name} 的标签（{names}）",
            owner=owner.uid,
            target=target.uid,
            lost_tags=lost,
        )

    # ------------------------------------------------------------ 目标选择
    def choose_target(self, attacker: Fighter) -> Fighter | None:
        enemies = self.alive(self.opponent_team(attacker.team))
        if not enemies:
            return None
        strategy = self.strategy_of(attacker.team)
        if strategy.kind == STRATEGY_HIGHEST_ATK:
            return min(enemies, key=lambda f: (-f.atk, f.hp, f.slot))
        if strategy.kind == STRATEGY_TAG_PRIORITY and strategy.tag:
            preferred = [f for f in enemies if f.has(strategy.tag)] or enemies
            return min(preferred, key=lambda f: (f.hp, f.slot))
        return min(enemies, key=lambda f: (f.hp, f.slot))

    # ------------------------------------------------------------ 行动
    def _take_action(self, attacker: Fighter) -> None:
        runtimes = taglib.runtime_for(attacker)
        if any(r.targets_all for r in runtimes):
            targets = self.alive(self.opponent_team(attacker.team))
            if not targets:
                return
            self.log(
                "attack",
                f"{attacker.position} {attacker.name} 发动群体攻击，覆盖 {len(targets)} 个目标",
                attacker=attacker.uid,
                targets=[t.uid for t in targets],
                aoe=True,
            )
            for target in list(targets):
                if not attacker.alive:
                    break
                self.resolve_attack(attacker, target)
            return

        target = self.choose_target(attacker)
        if target is None:
            return
        self.resolve_attack(attacker, target)

    def resolve_attack(
        self,
        attacker: Fighter,
        target: Fighter,
        *,
        damage: int | None = None,
        ctx: HitContext = DEFAULT_HIT_CONTEXT,
    ) -> int:
        """结算一次攻击，返回目标实际损失的生命。"""

        if not attacker.alive or not target.alive:
            return 0

        runtimes = taglib.runtime_for(attacker)
        explosive = any(r.self_destruct_after_attack for r in runtimes)

        if not ctx.follow_up:
            self.log(
                "attack",
                f"{attacker.position} {attacker.name} 攻击 {target.position} {target.name}",
                attacker=attacker.uid,
                target=target.uid,
            )

        # 双方自爆步兵互相攻击 → 同归于尽
        if explosive and target.has("explosive"):
            self.log(
                "clash",
                f"{attacker.position} {attacker.name} 与 {target.position} {target.name} 同归于尽",
                attacker=attacker.uid,
                target=target.uid,
            )
            self.kill(target, killer=attacker, reason="自爆对撞")
            self.kill(attacker, killer=target, reason="自爆对撞")
            return target.max_hp

        base = attacker.atk if damage is None else damage
        hit_ctx = HitContext(
            follow_up=ctx.follow_up,
            explosive=explosive or ctx.explosive,
            transferred=ctx.transferred,
        )

        # 攻击方修正（狂暴等）
        for runtime in runtimes:
            if runtime.modify_outgoing is not None:
                base = runtime.modify_outgoing(self, attacker, target, base, hit_ctx)

        # 自爆：目标足够脆弱时直接击杀
        lethal = False
        for runtime in runtimes:
            if runtime.explosive_kill_max_hp is not None and target.max_hp <= runtime.explosive_kill_max_hp:
                base = target.hp
                lethal = True
                self.log(
                    "tag",
                    f"自爆判定：{target.position} {target.name} 最大生命 {target.max_hp} "
                    f"≤ {runtime.explosive_kill_max_hp}，直接被击杀",
                    attacker=attacker.uid,
                    target=target.uid,
                )
                break

        ignore_shield = any(r.damage_ignores_shield for r in runtimes)
        damage_dealt = self._deal_damage(
            attacker,
            target,
            base,
            ctx=hit_ctx,
            follow_up=ctx.follow_up,
            lethal=lethal,
            ignore_shield=ignore_shield,
        )

        # 命中后效果：中毒、穿透
        for runtime in runtimes:
            if runtime.on_hit is not None:
                runtime.on_hit(self, attacker, target, damage_dealt, hit_ctx)

        # 自爆反噬：出手后自身死亡
        if explosive and attacker.alive:
            self.kill(attacker, killer=target, reason="自爆反噬")
        return damage_dealt

    def _deal_damage(
        self,
        attacker: Fighter,
        target: Fighter,
        damage: int,
        *,
        ctx: HitContext,
        follow_up: bool,
        lethal: bool,
        ignore_shield: bool,
    ) -> int:
        """护盾分担 → 承伤修正 → 扣血，返回目标实际损失的生命。"""

        if damage <= 0 or not target.alive:
            return 0

        # 自爆直接击杀：无视护盾与减伤，直接把目标血量打到 0
        if lethal:
            damage = target.hp

        # 护盾分担
        total_loss = 0
        if not lethal and not ignore_shield and not ctx.transferred:
            bearer = self._shield_bearer(target)
            if bearer is not None:
                shared = taglib.scale_ratio(damage, C.SHIELD_SCALE)
                bearer.uses_left["shield"] = max(0, bearer.uses_left.get("shield", 0) - 1)
                self.log(
                    "shield",
                    f"{bearer.position} {bearer.name} 为 {target.position} {target.name} 分担了 {shared} 点伤害",
                    bearer=bearer.uid,
                    target=target.uid,
                    amount=shared,
                    charges_left=bearer.uses_left["shield"],
                )
                total_loss += self._deal_damage(
                    attacker,
                    bearer,
                    shared,
                    ctx=HitContext(follow_up=follow_up, explosive=ctx.explosive, transferred=True),
                    follow_up=follow_up,
                    lethal=False,
                    ignore_shield=True,
                )
                damage -= shared
                if damage <= 0 or not target.alive:
                    return total_loss

        # 受击方修正（重装盔甲；追击伤害同样会被减伤）
        if not lethal:
            for runtime in taglib.runtime_for(target):
                if runtime.modify_incoming is not None:
                    damage = runtime.modify_incoming(self, target, attacker, damage, ctx)

        damage = max(0, damage)
        loss = min(damage, target.hp)
        total_loss += loss
        target.hp -= damage
        if target.hp <= 0:
            target.hp = 0
        if lethal:
            self.log(
                "damage",
                f"{target.position} {target.name} 被直接击杀",
                target=target.uid,
                attacker=attacker.uid,
                amount=max(loss, target.max_hp),
                hp_after=0,
                lethal=True,
                follow_up=follow_up,
            )
        else:
            suffix = "（追击）" if follow_up else ""
            self.log(
                "damage",
                f"{target.position} {target.name}{suffix} 受到 {damage} 点伤害，剩余 {max(target.hp, 0)}/{target.max_hp}",
                target=target.uid,
                attacker=attacker.uid,
                amount=damage,
                hp_after=max(target.hp, 0),
                lethal=False,
                follow_up=follow_up,
            )
        if not target.alive or target.hp <= 0:
            self.kill(target, killer=attacker, reason="战斗中被击杀")
        return total_loss

    def _shield_bearer(self, target: Fighter) -> Fighter | None:
        """找到能为 target 分担伤害的护盾部署者（不能为自己分担）。"""

        for ally in self.alive(target.team):
            if ally.uid == target.uid:
                continue
            if not ally.has("shield"):
                continue
            if ally.uses_left.get("shield", 0) > 0:
                return ally
        return None

    # ------------------------------------------------------------ 中毒
    def apply_poison(self, attacker: Fighter, target: Fighter) -> None:
        if not target.alive:
            return
        if len(target.poison) < C.POISON_MAX_STACKS:
            target.poison.append(C.POISON_DURATION)
        else:
            # 已满层：刷新最接近到期的一层，不再增加层数
            index = min(range(len(target.poison)), key=lambda i: target.poison[i])
            target.poison[index] = C.POISON_DURATION
        self.log(
            "poison",
            f"{target.position} {target.name} 中毒层数 → {len(target.poison)}",
            attacker=attacker.uid,
            target=target.uid,
            stacks=len(target.poison),
        )

    def _resolve_poison_phase(self) -> None:
        for team in (0, 1):
            for fighter in self.teams[team]:
                if not fighter.alive or not fighter.poison:
                    continue
                damage = C.POISON_DAMAGE * len(fighter.poison)
                fighter.hp = max(0, fighter.hp - damage)
                self.log(
                    "poison_tick",
                    f"{fighter.position} {fighter.name} 受到中毒伤害 {damage} 点，剩余 {fighter.hp}/{fighter.max_hp}",
                    target=fighter.uid,
                    amount=damage,
                    hp_after=fighter.hp,
                    stacks=len(fighter.poison),
                )
                fighter.poison = [rounds - 1 for rounds in fighter.poison if rounds - 1 > 0]
                if fighter.hp <= 0:
                    self.kill(fighter, killer=None, reason="中毒")

    # ------------------------------------------------------------ 穿透
    def resolve_pierce(self, attacker: Fighter, target: Fighter, dealt: int, ctx: HitContext) -> None:
        """箭矢穿透：主目标足够脆弱时，对另一名敌人追加半伤攻击。"""

        if ctx.follow_up or ctx.explosive:
            return
        if target.max_hp > C.FRAGILE_MAX_HP:
            return
        others = [f for f in self.alive(self.opponent_team(attacker.team)) if f.uid != target.uid]
        if not others:
            return
        extra = self.rng.choice(others)
        damage = taglib.scale_ratio(attacker.atk, C.PIERCE_SCALE)
        self.log(
            "pierce",
            f"{attacker.position} {attacker.name} 的箭矢穿透 {target.position} {target.name}，追击 {extra.position} {extra.name}",
            attacker=attacker.uid,
            pierced=target.uid,
            target=extra.uid,
            amount=damage,
        )
        self.resolve_attack(attacker, extra, damage=damage, ctx=HitContext(follow_up=True, explosive=False))

    # ------------------------------------------------------------ 生死
    def heal(self, fighter: Fighter, amount: int, source: str) -> None:
        if not fighter.alive or amount <= 0:
            return
        before = fighter.hp
        fighter.hp = min(fighter.max_hp, fighter.hp + amount)
        self.log(
            "heal",
            f"{fighter.position} {fighter.name} 恢复 {fighter.hp - before} 点生命，剩余 {fighter.hp}/{fighter.max_hp}",
            target=fighter.uid,
            amount=fighter.hp - before,
            hp_after=fighter.hp,
            source=source,
        )

    def kill(self, fighter: Fighter, killer: Fighter | None, reason: str) -> None:
        """处理阵亡：先记录死亡，再尝试死灵法师复活。"""

        if not fighter.alive:
            return
        fighter.alive = False
        fighter.hp = 0
        fighter.poison.clear()
        self.log(
            "death",
            f"{fighter.position} {fighter.name} 阵亡（{reason}）",
            target=fighter.uid,
            killer=killer.uid if killer else None,
            reason=reason,
        )
        self._try_revive(fighter)

    def _try_revive(self, fallen: Fighter) -> None:
        # 自爆步兵等标签明确标注为「不可复活」时直接跳过
        if any(not taglib.get_spec(tag).revivable for tag in fallen.tags):
            return
        for ally in self.alive(fallen.team):
            if ally.uid == fallen.uid or not ally.has("necromancy"):
                continue
            if ally.uses_left.get("necromancy", 0) <= 0:
                continue
            ally.uses_left["necromancy"] -= 1
            fallen.alive = True
            fallen.hp = max(1, taglib.scale_ratio(fallen.max_hp, C.NECROMANCY_SCALE))
            self.log(
                "revive",
                f"{ally.position} {ally.name} 复活了 {fallen.position} {fallen.name}，"
                f"生命 {fallen.hp}/{fallen.max_hp}（剩余次数 {ally.uses_left['necromancy']}）",
                healer=ally.uid,
                target=fallen.uid,
                hp_after=fallen.hp,
                charges_left=ally.uses_left["necromancy"],
            )
            return

    # ------------------------------------------------------------ 主循环
    def _check_finish(self) -> bool:
        wiped = (self.is_wiped(0), self.is_wiped(1))
        if not any(wiped):
            return False
        self.finished = True
        if all(wiped):
            self.winner = None
            self.reason = "双方同归于尽"
        else:
            self.winner = 1 if wiped[0] else 0
            self.reason = "一方全灭"
        return True

    def _resolve_timeout(self) -> None:
        self.finished = True
        hp_left = [sum(f.hp for f in team) for team in self.teams]
        if hp_left[0] == hp_left[1]:
            self.winner = self.rng.choice([0, 1])
            self.reason = f"达到最大回合数，双方剩余生命相同（{hp_left[0]}），随机判定"
        else:
            self.winner = 0 if hp_left[0] > hp_left[1] else 1
            self.reason = f"达到最大回合数，按剩余生命判定（{hp_left[0]} : {hp_left[1]}）"

    def run(self) -> BattleResult:
        self._battle_start()
        self._check_finish()

        while not self.finished and self.round < C.MAX_ROUNDS_PER_DUEL:
            self.round += 1
            self.log("round_start", f"第 {self.round} 回合开始", round=self.round)
            self._resolve_poison_phase()
            if self._check_finish():
                break
            for slot in range(len(self.teams[0])):
                for team in self.order:
                    fighter = self.teams[team][slot]
                    if not fighter.alive:
                        self.log("skip", f"{fighter.position} {fighter.name} 已阵亡，无法行动", target=fighter.uid)
                        continue
                    self._take_action(fighter)
                    if self._check_finish():
                        break
                if self.finished:
                    break

        if not self.finished:
            self._resolve_timeout()

        self.log(
            "battle_end",
            f"战斗结束：{self.summary()}",
            winner_team=self.winner,
            reason=self.reason,
            rounds=self.round,
        )
        return self._result()

    def summary(self) -> str:
        if self.winner is None:
            return "平局"
        return f"第 {'AB'[self.winner]} 队获胜（{self.reason}，共 {self.round} 回合）"

    def _result(self) -> BattleResult:
        return BattleResult(
            winner_team=self.winner,
            reason=self.reason,
            rounds=self.round,
            seed=self.seed,
            first_team=self.order[0],
            events=self.events,
            teams=[[f.snapshot() for f in team] for team in self.teams],
            total_initiative=list(self.total_initiative),
            survivors=[len(self.alive(0)), len(self.alive(1))],
        )


def run_battle(teams: list[list[Fighter]], strategies: list[Strategy], seed: int) -> BattleResult:
    """对外的唯一入口：执行一场战斗并返回结果。"""

    return Battle(teams, strategies, seed).run()
