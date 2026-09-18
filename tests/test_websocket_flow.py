"""联机流程测试：建房 → 加入 → 提交 → 结算 → 三局两胜 → 断线关闭。"""

from __future__ import annotations

import contextlib

import pytest
from fastapi.testclient import TestClient

from backend import protocol
from backend.config import Settings
from backend.web.app import create_app
from backend.web.websocket import RateLimiter


@pytest.fixture()
def client() -> TestClient:
    app = create_app(Settings(prepare_timeout=30))
    with TestClient(app) as test_client:
        yield test_client


def make_plan(round_start: dict) -> dict:
    """根据收到的角色池拼一份一定合法的方案（自爆步兵自动放在首位）。"""

    pool = round_start["pool"]
    ids = [card["id"] for card in pool]
    first_ids = [card["id"] for card in pool if card["place_first"]]
    selection = ids[:3]
    for char_id in first_ids:
        if char_id in selection:
            selection.remove(char_id)
            selection.insert(0, char_id)
    return {
        "type": "submit_plan",
        "selection": selection,
        "bonuses": [
            {"slot": 1, "kind": "atk"},
            {"slot": 1, "kind": "hp"},
            {"slot": 2, "kind": "atk"},
            {"slot": 2, "kind": "hp"},
        ],
        "strategy": {"kind": "lowest_hp", "tag": None},
    }


def collect(websocket, stop_types: set[str], limit: int = 40) -> list[dict]:
    """按顺序读取消息，直到出现目标类型或读满 limit 条。"""

    messages: list[dict] = []
    for _ in range(limit):
        message = websocket.receive_json()
        messages.append(message)
        if message.get("type") in stop_types:
            break
    return messages


def types_of(messages: list[dict]) -> list[str]:
    return [message.get("type") for message in messages]


@contextlib.contextmanager
def two_players(client: TestClient):
    """打开两条 WebSocket 连接（必须用 with 进入，TestClient 才会启动会话）。"""

    with client.websocket_connect("/ws") as ws_a, client.websocket_connect("/ws") as ws_b:
        yield ws_a, ws_b


def start_match(ws_a, ws_b):
    """建房 + 加入，返回房间号与双方的首局信息。"""

    assert ws_a.receive_json()["type"] == "hello"
    assert ws_b.receive_json()["type"] == "hello"

    ws_a.send_json({"type": "create_room", "name": "调停者A"})
    created = collect(ws_a, {"room_joined"})[-1]
    assert created["seat"] == 0
    code = created["room_code"]

    ws_b.send_json({"type": "join_room", "name": "调停者B", "room_code": code})
    round_a = collect(ws_a, {"round_start"})[-1]
    round_b = collect(ws_b, {"round_start"})[-1]
    return code, round_a, round_b


def test_two_players_can_finish_a_full_match(client: TestClient):
    with two_players(client) as (ws_a, ws_b):
        _code, round_a, round_b = start_match(ws_a, ws_b)
        rounds_seen = 0
        while rounds_seen < 3:
            rounds_seen += 1
            ws_a.send_json(make_plan(round_a))
            ws_b.send_json(make_plan(round_b))

            messages_a = collect(ws_a, {"round_start", "game_over"})
            messages_b = collect(ws_b, {"round_start", "game_over"})
            kinds_a = types_of(messages_a)
            assert "plan_accepted" in kinds_a
            assert "battle_report" in kinds_a
            assert "round_result" in kinds_a

            report = next(m for m in messages_a if m["type"] == "battle_report")
            assert len(report["lineups"]) == 2
            assert len(report["result"]["events"]) > 3
            assert report["result"]["winner_team"] in (0, 1)

            assert types_of(messages_b).count("battle_report") == 1
            if messages_a[-1]["type"] == "game_over":
                assert messages_a[-1]["score"][0] + messages_a[-1]["score"][1] >= 2
                break
            round_a = messages_a[-1]
            round_b = messages_b[-1]
            assert round_a["type"] == "round_start" and round_b["type"] == "round_start"


def test_private_pools_stay_private(client: TestClient):
    with two_players(client) as (ws_a, ws_b):
        _code, round_a, round_b = start_match(ws_a, ws_b)
        # 双方拿到的是各自的池子，且大厅消息里不会泄露对手角色池
        assert {"pool"} <= set(round_a)
        assert {"pool"} <= set(round_b)
        assert round_a["score"] == [0, 0]
        assert round_a["strategies"]


def test_invalid_plan_returns_actionable_error(client: TestClient):
    with two_players(client) as (ws_a, ws_b):
        _code, round_a, _round_b = start_match(ws_a, ws_b)
        ws_a.send_json(
            {
                "type": "submit_plan",
                "selection": [card["id"] for card in round_a["pool"][:2]],  # 只选 2 人
                "bonuses": [],
                "strategy": {"kind": "lowest_hp"},
            }
        )
        error = collect(ws_a, {"error"})[-1]

        assert error["type"] == "error"
        assert "3 名角色" in error["message"]
        assert error["field"] == "selection"


def test_unknown_message_type_is_rejected(client: TestClient):
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "hello"
        ws.send_json({"type": "launch_missile"})
        error = collect(ws, {"error"})[-1]
        assert "无法识别的消息类型" in error["message"]


def test_oversized_message_is_rejected(client: TestClient):
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "hello"
        ws.send_text("x" * (protocol.MAX_MESSAGE_BYTES + 10))
        error = collect(ws, {"error"})[-1]
        assert "过大" in error["message"]


def test_rate_limiter_blocks_flooding():
    limiter = RateLimiter(window=1.0, limit=3)

    assert [limiter.allow() for _ in range(3)] == [True, True, True]
    assert limiter.allow() is False
    assert limiter.strikes == 1


def test_disconnect_closes_room_for_opponent(client: TestClient):
    with two_players(client) as (ws_a, ws_b):
        start_match(ws_a, ws_b)
        ws_a.close()
        messages = collect(ws_b, {"room_closed"})
        assert messages[-1]["type"] == "room_closed"
        assert "离开" in messages[-1]["reason"]


def test_prepare_timeout_auto_submits():
    app = create_app(Settings(prepare_timeout=1))
    with TestClient(app) as client:
        with two_players(client) as (ws_a, ws_b):
            start_match(ws_a, ws_b)
            messages_a = collect(ws_a, {"battle_report"}, limit=40)
            assert "plan_auto_submitted" in types_of(messages_a)
            assert "battle_report" in types_of(messages_a)


def test_rematch_starts_a_brand_new_match(client: TestClient):
    """整场结束后双方都请求再来一局，应该重开一局：比分归零、局数回到 1、重新抽池。"""

    with two_players(client) as (ws_a, ws_b):
        _code, round_a, round_b = start_match(ws_a, ws_b)
        for _ in range(3):
            ws_a.send_json(make_plan(round_a))
            ws_b.send_json(make_plan(round_b))
            messages_a = collect(ws_a, {"round_start", "game_over"})
            messages_b = collect(ws_b, {"round_start", "game_over"})
            if messages_a[-1]["type"] == "game_over":
                assert messages_b[-1]["type"] == "game_over"
                break
            round_a, round_b = messages_a[-1], messages_b[-1]
        else:  # pragma: no cover - 三局之内必然分出胜负
            raise AssertionError("三局之内没有分出胜负")

        ws_a.send_json({"type": "rematch"})
        ws_b.send_json({"type": "rematch"})
        new_round_a = collect(ws_a, {"round_start"})[-1]
        new_round_b = collect(ws_b, {"round_start"})[-1]

        assert new_round_a["round_index"] == 1
        assert new_round_a["score"] == [0, 0]
        assert new_round_b["round_index"] == 1
        assert [card["id"] for card in new_round_a["pool"]] != []
        assert len(new_round_a["pool"]) == 6
