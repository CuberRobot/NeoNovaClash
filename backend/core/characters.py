"""角色数据。

数值来源：
- 旧项目手写碎片（NCinit）：十二名基础角色
- 旧项目设定文档与 v1.2 更新日志：平衡性调整（自爆步兵 HP=3、均衡战士 C ATK=5 等），
  以及 13-20 号扩展角色（旧项目 v1.2 起加入卡池）

标签 key 与 tags.py 一一对应；新增角色只需在对应花名册里追加一条数据。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Character:
    """角色的静态定义（不含对局中的临时状态）。"""

    id: int
    name: str
    role: str          # 定位：均衡 / 输出 / 前排 / 辅助 / 特殊
    atk: int
    hp: int
    initiative: int    # 先手值，双方出战角色总和较低的一方先手
    tag: str           # 标签 key，无标签为 "none"
    domain: str        # 星域背景，用于前端展示
    lore: str          # 一句话背景


# 12 名基础角色：标准模式的默认卡池
# 设计原则：每个角色都有清晰且互不重叠的面板曲线与定位，
#           调整数值时保住「脆皮高攻 / 中庸 / 偏肉 / 纯防御」的差别，不要把大家都调成一个样。
BASE_ROSTER: tuple[Character, ...] = (
    Character(1, "均衡战士A", "均衡", 9, 20, 5, "none", "平衡星域", "三棱水晶构成的进攻型基石，攻击最高但最脆。"),
    Character(2, "均衡战士B", "均衡", 6, 25, 5, "none", "平衡星域", "攻守之间取中，面板最标准的一名。"),
    Character(3, "均衡战士C", "均衡", 5, 29, 4, "none", "平衡星域", "偏肉的中庸战士，靠血量拖住阵线。"),
    Character(4, "自爆步兵", "特殊", 18, 3, 2, "explosive", "碎晶星域", "体内封存不稳定爆炸水晶，出手即燃尽自己。"),
    Character(5, "诅咒巫师", "特殊", 6, 20, 4, "curse", "幽暗星域", "开战瞬间剥夺敌方一名角色的标签。"),
    Character(6, "死灵法师", "辅助", 4, 24, 5, "necromancy", "重生星域", "以残余星能唤醒倒下的同伴。"),
    Character(7, "铁甲卫士", "前排", 4, 30, 4, "heavy_armor", "重装星域", "沉稳的重装主力，靠盔甲把重击削成小伤害。"),
    Character(8, "护盾部署者", "前排", 3, 33, 4, "shield", "守护星域", "以自身水晶为队友分担伤害。"),
    Character(9, "风行射手", "输出", 8, 20, 6, "pierce", "迅流星域", "箭矢高速旋转，是全场最脆但穿透最狠的射手。"),
    Character(10, "狂战士", "输出", 8, 19, 5, "berserk", "怒火星域", "血量越低，水晶越红热，出手越凶猛。"),
    Character(11, "毒药投手", "输出", 6, 21, 6, "poison", "蚀骨星域", "投掷的晶石会持续侵蚀敌人结构。"),
    Character(12, "重炮统领", "输出", 4, 25, 8, "aoe", "毁灭星域", "一次攻击震荡全场，代价是极难抢到先手。"),
)

# 8 名扩展角色（旧项目 v1.2 加入卡池，让阵容构筑真正有取舍）
EXTENDED_ROSTER: tuple[Character, ...] = (
    Character(13, "均衡战士D", "均衡", 7, 22, 5, "none", "平衡星域", "尖锐菱面水晶构成，比 B 更偏进攻。"),
    Character(14, "均衡战士E", "均衡", 4, 33, 3, "none", "平衡星域", "厚实立方水晶构成，是无标签角色里的纯肉墙。"),
    Character(15, "暗影猎手", "输出", 6, 24, 6, "pierce", "迅流星域", "比风行射手更耐打的穿透手，箭矢同样能追击脆皮。"),
    Character(16, "血怒斗士", "输出", 7, 23, 6, "berserk", "怒火星域", "暴躁但比狂战士更耐打的精英狂暴。"),
    Character(17, "晶壁守卫", "前排", 4, 31, 3, "heavy_armor", "重装星域", "极重的防御体：出手比铁甲卫士轻，但更难被打穿。"),
    Character(18, "噬魂者", "输出", 7, 21, 5, "lifesteal", "幽暗星域", "深紫吞噬水晶，命中即抽取伤者的生机。"),
    Character(19, "荆棘守卫", "前排", 4, 28, 4, "thorns", "守护星域", "体表布满反向晶刺，受伤时反噬来敌。"),
    Character(20, "处决者", "输出", 7, 20, 4, "execute", "毁灭星域", "刀锋状水晶，专门收割残血目标。"),
)

# 当前启用的卡池：12 名基础角色 + 8 名扩展角色
ACTIVE_ROSTER: tuple[Character, ...] = BASE_ROSTER + EXTENDED_ROSTER

CHARACTER_BY_ID: dict[int, Character] = {c.id: c for c in ACTIVE_ROSTER}


def get_character(char_id: int) -> Character:
    """按编号取角色，编号不存在时抛出 KeyError（调用方应先做校验）。"""

    return CHARACTER_BY_ID[char_id]


def roster() -> tuple[Character, ...]:
    """当前启用的角色池。"""

    return ACTIVE_ROSTER
