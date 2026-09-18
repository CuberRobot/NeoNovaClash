"""基础角色数据。

数值来源：
- 旧项目手写碎片（NCinit）：v0.5 十二名基础角色
- 旧项目设定文档：v1.1 / v1.2 平衡性调整（自爆步兵 HP=3、均衡战士 C ATK=5 等）

标签 key 与 tags.py 一一对应，新增角色只需在 BASE_ROSTER 里追加一条数据。
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
BASE_ROSTER: tuple[Character, ...] = (
    Character(1, "均衡战士A", "均衡", 8, 19, 5, "none", "平衡星域", "由稳定三棱水晶构成的可靠基石。"),
    Character(2, "均衡战士B", "均衡", 6, 23, 5, "none", "平衡星域", "攻守之间取中，耐久略胜一筹。"),
    Character(3, "均衡战士C", "均衡", 5, 26, 5, "none", "平衡星域", "面板均衡，靠血量拖住阵线。"),
    Character(4, "自爆步兵", "特殊", 16, 3, 2, "explosive", "碎晶星域", "体内封存不稳定爆炸水晶，出手即燃尽自己。"),
    Character(5, "诅咒巫师", "特殊", 6, 20, 3, "curse", "幽暗星域", "开战瞬间剥夺敌方一名角色的标签。"),
    Character(6, "死灵法师", "辅助", 4, 24, 5, "necromancy", "重生星域", "以残余星能唤醒倒下的同伴。"),
    Character(7, "铁甲卫士", "前排", 4, 30, 6, "heavy_armor", "重装星域", "致密晶钢构成，能削掉大部分重击。"),
    Character(8, "护盾部署者", "前排", 2, 33, 5, "shield", "守护星域", "以自身水晶为队友分担伤害。"),
    Character(9, "风行射手", "输出", 7, 21, 4, "pierce", "迅流星域", "箭矢高速旋转，命中脆弱目标后继续追击。"),
    Character(10, "狂战士", "输出", 8, 19, 5, "berserk", "怒火星域", "血量越低，水晶越红热，出手越凶猛。"),
    Character(11, "毒药投手", "输出", 6, 21, 5, "poison", "蚀骨星域", "投掷的晶石会持续侵蚀敌人结构。"),
    Character(12, "重炮统领", "输出", 4, 25, 7, "aoe", "毁灭星域", "一次攻击震荡全场，同时压低敌方血线。"),
)

CHARACTER_BY_ID: dict[int, Character] = {c.id: c for c in BASE_ROSTER}


def get_character(char_id: int) -> Character:
    """按编号取角色，编号不存在时抛出 KeyError（调用方应先做校验）。"""

    return CHARACTER_BY_ID[char_id]


def roster() -> tuple[Character, ...]:
    """当前启用的角色池。"""

    return BASE_ROSTER
