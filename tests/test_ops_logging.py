"""运维日志测试：关键事件必须真的落到日志里，别出现"配了但打不出来"。

覆盖三件事：
1. `configure_logging` 只挂一次 handler（重复调用不会重复打印）；
2. 房间生命周期（创建 / 开打 / 整场结束 / 关房）会打结构化日志；
3. 一场完整的三局两胜走完，日志里能回答"开房几个、打完几局、谁赢"。

实现说明：应用 logger 设了 `propagate=False`（见 backend/config.py），pytest 的 caplog
挂在根 logger 上抓不到它，所以这里自己挂一个内存 handler。
"""

from __future__ import annotations

import contextlib
import logging

import pytest
from fastapi.testclient import TestClient

from backend.config import LOGGER_NAME, Settings, configure_logging
from backend.web.app import create_app
from tests.test_websocket_flow import collect, make_plan

APP_LOGGER = logging.getLogger(LOGGER_NAME)


class ListHandler(logging.Handler):
    """把日志收集到内存列表，方便断言结构化字段。"""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def text(self) -> str:
        return "\n".join(record.getMessage() for record in self.records)

    def matching(self, needle: str) -> list[str]:
        return [record.getMessage() for record in self.records if needle in record.getMessage()]


@pytest.fixture()
def app_logs() -> ListHandler:
    # pytest 的 logging 插件会把根 logger 抬到 WARNING，INFO 应用日志会被有效级别过滤掉；
    # force=True 把级别压回 INFO，和线上 `python run.py` 的行为保持一致。
    configure_logging("info", force=True)
    handler = ListHandler()
    APP_LOGGER.addHandler(handler)
    try:
        yield handler
    finally:
        APP_LOGGER.removeHandler(handler)


@pytest.fixture()
def client() -> TestClient:
    app = create_app(Settings(prepare_timeout=30))
    with TestClient(app) as test_client:
        yield test_client


def test_configure_logging_is_idempotent() -> None:
    configure_logging("info", force=True)
    configure_logging("info", force=True)
    handlers = [h for h in APP_LOGGER.handlers if not isinstance(h, ListHandler)]
    assert len(handlers) == 1
    assert APP_LOGGER.propagate is False


def _open_match(client: TestClient) -> tuple:
    """开一局真实对局：返回 (stack, ws_a, ws_b, round_a, round_b)。

    必须用 `with` 进入 websocket_connect，连接才会真正建立（见 test_websocket_flow）；
    这里把两个连接挂到 ExitStack 上，由调用方在结束时统一关闭。
    **双方的角色池不同**，所以各拿各的 round_start，不能混用。
    """

    stack = contextlib.ExitStack()
    ws_a = stack.enter_context(client.websocket_connect("/ws"))
    ws_b = stack.enter_context(client.websocket_connect("/ws"))
    collect(ws_a, {"hello"}, limit=1)
    collect(ws_b, {"hello"}, limit=1)
    ws_a.send_json({"type": "create_room", "name": "甲", "mode": "standard"})
    room_code = ws_a.receive_json()["room_code"]
    ws_b.send_json({"type": "join_room", "name": "乙", "room_code": room_code})
    round_a = collect(ws_a, {"round_start"}, limit=10)[-1]
    round_b = collect(ws_b, {"round_start"}, limit=10)[-1]
    return stack, ws_a, ws_b, round_a, round_b


def test_room_events_are_logged(client: TestClient, app_logs: ListHandler) -> None:
    stack, ws_a, ws_b, round_a, round_b = _open_match(client)
    with stack:
        ws_a.send_json(make_plan(round_a))
        ws_b.send_json(make_plan(round_b))
        collect(ws_a, {"battle_report"}, limit=10)

    created = app_logs.matching("room_created")
    assert created, "建房没有打 room_created 日志"
    assert "source=create_room" in created[0]
    assert "prepare_timeout=30s" in created[0], "日志里的备战时长必须来自运行时配置"
    started = app_logs.matching("battle_start")
    assert started, "开打没有打 battle_start 日志"
    assert "players=甲 vs 乙" in started[0]
    assert "seed=" in started[0]


def test_match_over_and_room_closed_are_logged(client: TestClient, app_logs: ListHandler) -> None:
    """打完一场（三局两胜），日志里必须同时有 match_over 与 room_closed。

    这里要特别处理"每局开头的那段握手"：打完一局后，房间会先发一条
    `round_start`（`awaiting_replay=True`，等双方确认回放），等两边都发
    `replay_done` 之后才真正开始本局倒计时。不确认也能跑（30 秒兜底），
    但测试会无谓地等 30 秒，所以这里每局都显式确认。
    """

    stack, ws_a, ws_b, round_a, round_b = _open_match(client)
    with stack:
        for _ in range(3):  # 最多三局，先赢两局者结束整场
            ws_a.send_json(make_plan(round_a))
            ws_b.send_json(make_plan(round_b))
            collect(ws_a, {"battle_report"}, limit=20)
            collect(ws_b, {"battle_report"}, limit=20)
            if app_logs.matching("match_over"):
                break  # 整场已经打完：不会再下发下一局的 round_start
            # 下一局的开场握手：先各自确认回放，再收新的 round_start / 新的角色池
            ws_a.send_json({"type": "replay_done"})
            ws_b.send_json({"type": "replay_done"})
            round_a = collect(ws_a, {"round_start"}, limit=10)[-1]
            round_b = collect(ws_b, {"round_start"}, limit=10)[-1]

        # 显式退房并等应答：收到 `room_closed` / `left_room` 就说明服务端已经把这条指令
        # 处理完、关房日志也落了盘（直接 close 连接是异步的，日志可能晚于断言）。
        # 注意主动退房时服务端先广播 `room_closed`，`left_room` 反而在后面。
        ws_a.send_json({"type": "leave_room"})
        collect(ws_a, {"room_closed", "left_room"}, limit=5)

    over = app_logs.matching("match_over")
    closed = app_logs.matching("room_closed")
    assert over, "整场结束没有打 match_over 日志"
    assert "winner=" in over[0] and "score=" in over[0] and "players=甲 vs 乙" in over[0]
    assert closed, "关房没有打 room_closed 日志"
    assert "reason=" in closed[0]
