"""全部平衡数值与规则常量。

调整数值时只改这里，不要在其它模块里写魔法数字，否则平衡会失控且难以回溯。
每个常量都标注了取值来源，方便日后对照旧项目文档做平衡性回顾。
"""

from __future__ import annotations

# ---------------------------------------------------------------- 对局结构
# 每局角色池大小（6 选 3）
POOL_SIZE = 6
# 每局出战角色数量
TEAM_SIZE = 3
# 每局增益次数
BONUS_PER_ROUND = 4
# 攻击增益：ATK +2
BONUS_ATK = 2
# 生命增益：HP +4
BONUS_HP = 4
# 单个出击位最多获得的增益次数（防止 4 次增益全部堆给一个人）
MAX_BONUS_PER_FIGHTER = 2
# 三局两胜
ROUNDS_TO_WIN = 2
# 准备阶段限时（秒）
PREPARE_TIMEOUT_SECONDS = 60
# 单局最大回合数，超过后按剩余血量判定，避免出现无法结束的对局
MAX_ROUNDS_PER_DUEL = 50
# 战斗日志上限，防御性上限，避免异常情况下事件列表无限增长
MAX_BATTLE_EVENTS = 2000

# ---------------------------------------------------------------- 标签数值
# 自爆：目标最大生命 <= 26 时直接击杀（v1.1 由 24 调整为 26）
EXPLOSIVE_KILL_MAX_HP = 26
# 脆皮判定线：最大生命 <= 24 触发箭矢穿透的追击
FRAGILE_MAX_HP = 24
# 穿透追击伤害比例：半伤
PIERCE_SCALE = (1, 2)
# 死灵法师复活次数
NECROMANCY_CHARGES = 2
# 复活血量比例：最大生命的 40%（向下取整）
NECROMANCY_SCALE = (2, 5)
# 护盾分担比例：50%
SHIELD_SCALE = (1, 2)
# 护盾可分担的次数
SHIELD_CHARGES = 3
# 重装盔甲触发线：单次伤害 >= 7 时减伤
HEAVY_ARMOR_THRESHOLD = 7
# 重装盔甲结算比例：60%
HEAVY_ARMOR_SCALE = (6, 10)
# 狂暴触发线：自身血量 <= 14
BERSERK_THRESHOLD = 14
# 狂暴结算比例：140%
BERSERK_SCALE = (14, 10)
# 中毒：每层每轮伤害
POISON_DAMAGE = 3
# 中毒：每层持续轮数
POISON_DURATION = 3
# 中毒：最大叠加层数
POISON_MAX_STACKS = 3
# 吸血：造成伤害的 25% 转为自身回复
LIFESTEAL_SCALE = (1, 4)
# 吸血：单次攻击的回复上限
LIFESTEAL_CAP = 5
# 反弹：实际受到伤害的 40% 反弹给攻击者
THORNS_SCALE = (2, 5)
# 斩杀：目标当前生命 <= 最大生命的 25% 时触发
EXECUTE_THRESHOLD_SCALE = (1, 4)
# 斩杀：触发后本次伤害为原本的 150%
EXECUTE_SCALE = (15, 10)

# ---------------------------------------------------------------- 房间与协议
# 房间号长度
ROOM_CODE_LENGTH = 4
# 玩家昵称长度限制
PLAYER_NAME_MIN = 1
PLAYER_NAME_MAX = 12
