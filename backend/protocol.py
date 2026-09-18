"""前后端通信协议：所有消息结构的唯一来源。

改动这里时必须同步更新 docs/通信协议.md，保证前后端与文档不会跑偏。
客户端 → 服务端的消息用 pydantic 校验，服务端 → 客户端的消息用构造函数拼装。
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from . import __version__
from .core import constants as C
from .core import rules
from .core import tags as taglib
from .core.characters import roster
from .core.models import Bonus, Plan, Strategy

SERVER_NAME = "NeoNovaClash"
PROTOCOL_VERSION = 1


class ProtocolError(Exception):
    """消息不合法，message 直接回给客户端展示。"""

    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


def _clean_name(value: str) -> str:
    name = (value or "").strip()
    if not name:
        raise ValueError("昵称不能为空")
    if len(name) > C.PLAYER_NAME_MAX:
        raise ValueError(f"昵称最多 {C.PLAYER_NAME_MAX} 个字符")
    return name


class CreateRoomMessage(BaseModel):
    type: Literal["create_room"]
    name: str

    _clean = field_validator("name")(_clean_name)


class JoinRoomMessage(BaseModel):
    type: Literal["join_room"]
    name: str
    room_code: str = Field(min_length=1, max_length=8)

    _clean = field_validator("name")(_clean_name)

    @field_validator("room_code")
    @classmethod
    def clean_code(cls, value: str) -> str:
        code = (value or "").strip().upper()
        if not code:
            raise ValueError("房间号不能为空")
        return code


class BonusMessage(BaseModel):
    slot: int
    kind: str


class StrategyMessage(BaseModel):
    kind: str
    tag: str | None = None


class SubmitPlanMessage(BaseModel):
    type: Literal["submit_plan"]
    selection: list[int] = Field(default_factory=list)
    bonuses: list[BonusMessage] = Field(default_factory=list)
    strategy: StrategyMessage = Field(default_factory=StrategyMessage)


class RematchMessage(BaseModel):
    type: Literal["rematch"]


class LeaveRoomMessage(BaseModel):
    type: Literal["leave_room"]


class PingMessage(BaseModel):
    type: Literal["ping"]


INBOUND_TYPES = {
    "create_room": CreateRoomMessage,
    "join_room": JoinRoomMessage,
    "submit_plan": SubmitPlanMessage,
    "rematch": RematchMessage,
    "leave_room": LeaveRoomMessage,
    "ping": PingMessage,
}

InboundMessage = (
    CreateRoomMessage
    | JoinRoomMessage
    | SubmitPlanMessage
    | RematchMessage
    | LeaveRoomMessage
    | PingMessage
)


def parse_message(raw: str | bytes | dict) -> InboundMessage:
    """把原始消息解析成协议对象，失败时抛出 ProtocolError。"""

    if isinstance(raw, (str, bytes)):
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProtocolError("消息不是合法的 JSON") from exc
    else:
        data = raw

    if not isinstance(data, dict):
        raise ProtocolError("消息必须是一个 JSON 对象")

    kind = data.get("type")
    if not isinstance(kind, str) or kind not in INBOUND_TYPES:
        raise ProtocolError(f"无法识别的消息类型：{kind!r}")

    try:
        return INBOUND_TYPES[kind].model_validate(data)
    except ValidationError as exc:
        field = ".".join(str(part) for part in exc.errors()[0].get("loc", ()))
        reason = exc.errors()[0].get("ctx", {}).get("error")
        message = str(reason) if reason else f"参数不合法：{field or kind}"
        raise ProtocolError(message, field=field or None) from exc


def plan_from_message(message: SubmitPlanMessage) -> Plan:
    """把提交消息转换成规则层的 Plan。"""

    return Plan(
        selection=tuple(int(cid) for cid in message.selection),
        bonuses=tuple(Bonus(slot=int(b.slot), kind=str(b.kind)) for b in message.bonuses),
        strategy=Strategy(kind=message.strategy.kind, tag=message.strategy.tag),
    )


# ---------------------------------------------------------------- 下行消息
def hello(message: str = "已连接到星核竞技场") -> dict:
    return {
        "type": "hello",
        "server": SERVER_NAME,
        "version": __version__,
        "protocol": PROTOCOL_VERSION,
        "message": message,
    }


def error(message: str, field: str | None = None, fatal: bool = False) -> dict:
    return {"type": "error", "message": message, "field": field, "fatal": fatal}


def pong() -> dict:
    return {"type": "pong"}


def stats_payload(rooms: int, players: int, uptime: float) -> dict:
    return {"rooms": rooms, "players": players, "uptime_seconds": round(uptime, 1)}


def version_payload() -> dict:
    return {
        "name": SERVER_NAME,
        "version": __version__,
        "protocol": PROTOCOL_VERSION,
        "mode": "standard",
        "rules_version": "v1.0",
    }


def rules_payload() -> dict:
    """规则页数据：角色、标签、常量与可选项，全部来自后端唯一数据源。"""

    return {
        "constants": {
            "pool_size": C.POOL_SIZE,
            "team_size": C.TEAM_SIZE,
            "bonus_per_round": C.BONUS_PER_ROUND,
            "bonus_atk": C.BONUS_ATK,
            "bonus_hp": C.BONUS_HP,
            "max_bonus_per_fighter": C.MAX_BONUS_PER_FIGHTER,
            "rounds_to_win": C.ROUNDS_TO_WIN,
            "prepare_timeout": C.PREPARE_TIMEOUT_SECONDS,
            "max_rounds_per_duel": C.MAX_ROUNDS_PER_DUEL,
        },
        "characters": [
            {
                "id": c.id,
                "name": c.name,
                "role": c.role,
                "atk": c.atk,
                "hp": c.hp,
                "initiative": c.initiative,
                "tag": c.tag,
                "tag_name": taglib.get_spec(c.tag).name,
                "domain": c.domain,
                "lore": c.lore,
            }
            for c in roster()
        ],
        "tags": [spec.to_dict() for spec in taglib.TAGS.values()],
        "strategies": rules.available_strategies(),
    }
