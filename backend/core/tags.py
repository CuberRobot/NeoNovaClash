"""标签定义与效果实现。

设计约定：
- TagSpec 描述标签的静态规则（文案、次数、限制），供校验与文档使用。
- TagRuntime 描述标签在战斗流程中的钩子，引擎在固定位置调用它们。
- 新增标签 = 在 TAGS 里加一条说明 + 在 RUNTIME 里注册一个钩子集合，
  不需要改动引擎主流程；只有需要新结算阶段时才动 engine.py。

钩子调用顺序见 docs/规则设定.md 「结算顺序」一节。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from . import constants as C
from .models import NONE_TAG, Fighter

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型检查，避免循环导入
    from .engine import Battle, HitContext


@dataclass(frozen=True)
class TagSpec:
    """标签的静态说明与限制。"""

    key: str
    name: str
    summary: str          # 一句话效果
    detail: str           # 完整结算说明
    uses: int = 0         # 初始使用次数（0 表示不限次数）
    place_first: bool = False   # 只能放在队伍首位
    revivable: bool = True      # 是否可被死灵法师复活
    curseable: bool = True      # 是否可被诅咒巫师剥夺

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "summary": self.summary,
            "detail": self.detail,
            "uses": self.uses,
            "place_first": self.place_first,
            "revivable": self.revivable,
            "curseable": self.curseable,
        }


TAGS: dict[str, TagSpec] = {
    NONE_TAG: TagSpec(
        key=NONE_TAG,
        name="无标签",
        summary="没有任何特殊能力，依靠面板数值作战。",
        detail="无标签角色不参与任何标签结算，可以作为稳定的输出或前排基石。",
    ),
    "explosive": TagSpec(
        key="explosive",
        name="自爆",
        summary=f"出手后自身必死；目标最大生命 ≤{C.EXPLOSIVE_KILL_MAX_HP} 时直接击杀。",
        detail=(
            f"攻击后自身立即死亡。若目标最大生命 ≤{C.EXPLOSIVE_KILL_MAX_HP} 则直接击杀，"
            "否则按自身攻击力结算普通伤害。自爆伤害不参与护盾分摊、不触发反弹；"
            "自爆步兵不可被复活，且只能放在队伍首位。若双方的自爆步兵互相攻击，则同归于尽。"
        ),
        place_first=True,
        revivable=False,
        curseable=False,
    ),
    "curse": TagSpec(
        key="curse",
        name="标签剥夺",
        summary="战斗开始时剥夺敌方一名角色的全部标签。",
        detail=(
            "战斗开始时触发一次：从敌方仍持有标签的角色中随机选择一名，移除其全部标签。"
            "不会选择敌方诅咒巫师与自爆步兵作为目标。"
        ),
        curseable=False,
    ),
    "necromancy": TagSpec(
        key="necromancy",
        name="复活",
        summary=(
            f"队友阵亡时以 {C.NECROMANCY_SCALE[0] * 100 // C.NECROMANCY_SCALE[1]}% 最大生命复活，"
            f"共 {C.NECROMANCY_CHARGES} 次。"
        ),
        detail=(
            "只要死灵法师存活且还有次数，队友阵亡时立刻以最大生命 40%（向下取整）复活。"
            "自爆步兵不可被复活；死灵法师死亡后复活能力立即失效，也无法复活自己。"
        ),
        uses=C.NECROMANCY_CHARGES,
    ),
    "heavy_armor": TagSpec(
        key="heavy_armor",
        name="重装盔甲",
        summary=f"单次受到 ≥{C.HEAVY_ARMOR_THRESHOLD} 点伤害时，该次伤害按 60% 结算。",
        detail=(
            f"单次受到的伤害 ≥{C.HEAVY_ARMOR_THRESHOLD} 时触发，结算后的伤害为原本的 60%（向下取整）。"
            "中毒等持续伤害不触发重装盔甲。"
        ),
    ),
    "shield": TagSpec(
        key="shield",
        name="护盾",
        summary=f"为队友分担 50% 伤害，共 {C.SHIELD_CHARGES} 次。",
        detail=(
            "队友受到伤害时，护盾部署者为其承担 50% 伤害并将其计入自身承伤流程"
            "（自身重装盔甲仍然生效，但不会被再次分担）。"
            "自爆伤害不参与护盾分摊；护盾部署者不能为自己分担伤害。"
        ),
        uses=C.SHIELD_CHARGES,
    ),
    "pierce": TagSpec(
        key="pierce",
        name="箭矢穿透",
        summary=f"命中最大生命 ≤{C.FRAGILE_MAX_HP} 的脆弱目标后，对另一名敌人追加半伤。",
        detail=(
            f"若本次攻击的主目标最大生命 ≤{C.FRAGILE_MAX_HP}（脆皮判定），"
            "则对该目标之外的随机一名存活敌人追加一次 50% 伤害的攻击。"
            "追击伤害会正常被护盾分担、被重装盔甲减伤，但不会再次触发穿透、中毒、吸血与反弹。"
        ),
    ),
    "berserk": TagSpec(
        key="berserk",
        name="狂暴",
        summary=f"自身生命 ≤{C.BERSERK_THRESHOLD} 时，造成的伤害 ×1.4。",
        detail=f"出手瞬间若自身剩余生命 ≤{C.BERSERK_THRESHOLD}，本次伤害提升至 140%（向下取整）。",
    ),
    "poison": TagSpec(
        key="poison",
        name="中毒",
        summary=f"命中后施加中毒，每轮开始时每层造成 {C.POISON_DAMAGE} 点伤害，最多 {C.POISON_MAX_STACKS} 层。",
        detail=(
            f"每次命中为目标的毒层 +1，每层持续 {C.POISON_DURATION} 轮并造成 {C.POISON_DAMAGE} 点伤害，"
            f"层数上限 {C.POISON_MAX_STACKS}（超过上限时刷新即将到期的一层，不增加层数）。"
            "中毒在每轮开始时统一结算，直接扣除生命，不经过护盾与重装盔甲，但会正常触发死亡与复活判定。"
        ),
    ),
    "aoe": TagSpec(
        key="aoe",
        name="群伤",
        summary="攻击时对敌方所有存活单位造成等量伤害。",
        detail=(
            "出手时不再选择单一目标，而是对敌方全部存活单位各结算一次伤害。"
            "每个目标分别计算狂暴、斩杀等攻击方修正，也分别触发护盾、重装盔甲与反弹。"
        ),
    ),
}


@dataclass
class TagRuntime:
    """标签在战斗流程中的挂载点。未提供的钩子表示该标签不参与该阶段。"""

    key: str
    targets_all: bool = False                     # 群伤：攻击敌方全体
    self_destruct_after_attack: bool = False      # 自爆：出手后自身死亡
    explosive_kill_max_hp: int | None = None       # 自爆：直接击杀的血量阈值
    damage_ignores_shield: bool = False            # 自爆伤害不被护盾分摊
    damage_ignores_reflect: bool = False           # 自爆伤害不被反弹
    on_battle_start: Callable[[Battle, Fighter], None] | None = None
    on_round_start: Callable[[Battle, Fighter], None] | None = None
    modify_outgoing: Callable[[Battle, Fighter, Fighter, int, HitContext], int] | None = None
    modify_incoming: Callable[[Battle, Fighter, Fighter, int, HitContext], int] | None = None
    on_hit: Callable[[Battle, Fighter, Fighter, int, HitContext], None] | None = None

    hooks: list[str] = field(default_factory=list)


def _berserk_outgoing(battle: Battle, attacker: Fighter, target: Fighter, damage: int, ctx: HitContext) -> int:
    if attacker.hp <= C.BERSERK_THRESHOLD:
        return _scale(damage, *C.BERSERK_SCALE)
    return damage


def _heavy_armor_incoming(battle: Battle, defender: Fighter, attacker: Fighter, damage: int, ctx: HitContext) -> int:
    if damage >= C.HEAVY_ARMOR_THRESHOLD:
        reduced = _scale(damage, *C.HEAVY_ARMOR_SCALE)
        battle.log(
            "tag",
            f"{defender.position} {defender.name} 的重装盔甲生效，伤害 {damage} → {reduced}",
            defender=defender.uid,
            attacker=attacker.uid,
            before=damage,
            after=reduced,
        )
        return reduced
    return damage


def _poison_on_hit(battle: Battle, attacker: Fighter, target: Fighter, dealt: int, ctx: HitContext) -> None:
    # 追击伤害不再叠加中毒，避免一次攻击叠两层
    if ctx.follow_up:
        return
    battle.apply_poison(attacker, target)


def _pierce_on_hit(battle: Battle, attacker: Fighter, target: Fighter, dealt: int, ctx: HitContext) -> None:
    battle.resolve_pierce(attacker, target, dealt, ctx)


def _curse_on_battle_start(battle: Battle, owner: Fighter) -> None:
    battle.resolve_curse(owner)


def _scale(value: int, num: int, den: int) -> int:
    """整数比例运算，一律向下取整，避免浮点误差影响对局复现。"""

    return value * num // den


RUNTIME: dict[str, TagRuntime] = {
    "explosive": TagRuntime(
        key="explosive",
        self_destruct_after_attack=True,
        explosive_kill_max_hp=C.EXPLOSIVE_KILL_MAX_HP,
        damage_ignores_shield=True,
        damage_ignores_reflect=True,
        hooks=["self_destruct_after_attack", "explosive_kill"],
    ),
    "curse": TagRuntime(key="curse", on_battle_start=_curse_on_battle_start, hooks=["on_battle_start"]),
    "necromancy": TagRuntime(key="necromancy", hooks=["on_death:revive"]),
    "heavy_armor": TagRuntime(key="heavy_armor", modify_incoming=_heavy_armor_incoming, hooks=["modify_incoming"]),
    "shield": TagRuntime(key="shield", hooks=["on_damage:split"]),
    "pierce": TagRuntime(key="pierce", on_hit=_pierce_on_hit, hooks=["on_hit"]),
    "berserk": TagRuntime(key="berserk", modify_outgoing=_berserk_outgoing, hooks=["modify_outgoing"]),
    "poison": TagRuntime(key="poison", on_hit=_poison_on_hit, hooks=["on_hit"]),
    "aoe": TagRuntime(key="aoe", targets_all=True, hooks=["targets_all"]),
}


def get_spec(tag: str) -> TagSpec:
    """取标签说明，未知标签按「无标签」处理，保证数据脏了也不会崩。"""

    return TAGS.get(tag, TAGS[NONE_TAG])


def get_runtime(tag: str) -> TagRuntime | None:
    return RUNTIME.get(tag)


def tag_name(tag: str) -> str:
    return get_spec(tag).name


def tag_names(tags: list[str]) -> list[str]:
    return [tag_name(t) for t in tags]


def runtime_for(fighter: Fighter) -> list[TagRuntime]:
    """取一名战士当前生效的所有标签钩子。"""

    result = []
    for tag in fighter.tags:
        runtime = RUNTIME.get(tag)
        if runtime is not None:
            result.append(runtime)
    return result


def scale_ratio(value: int, ratio: tuple[int, int]) -> int:
    """对外暴露的比例计算工具，规则层与引擎共用同一套取整方式。"""

    return _scale(value, *ratio)


def initial_uses(tags: list[str]) -> dict[str, int]:
    """标签自带的使用次数（护盾次数、复活次数）。"""

    uses: dict[str, int] = {}
    for tag in tags:
        spec = TAGS.get(tag)
        if spec is not None and spec.uses > 0:
            uses[tag] = spec.uses
    return uses
