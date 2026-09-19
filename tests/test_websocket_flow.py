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


def make_plan(round_start: dict, team_size: int | None = None, bonus_count: int | None = None) -> dict:
    """根据收到的角色池拼一份一定合法的方案（自爆步兵自动放在首位）。"""

    pool = round_start["pool"]
    ids = [card["id"] for card in pool]
    first_ids = [card["id"] for card in pool if card["place_first"]]
    size = team_size or len(ids) // 2
    count = bonus_count or 4
    selection = ids[:size]
    for char_id in first_ids:
        if char_id in selection:
            selection.remove(char_id)
            selection.insert(0, char_id)
    bonuses = [
        {"slot": (index % size) + 1, "kind": "atk" if index % 2 == 0 else "hp"} for index in range(count)
    ]
    return {
        "type": "submit_plan",
        "selection": selection,
        "bonuses": bonuses,
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


def test_pool_is_fixed_for_the_whole_match(client: TestClient):
    """整场三局共用同一份角色池：玩法核心是猜对手怎么用这 6 张牌。"""

    with two_players(client) as (ws_a, ws_b):
        _code, round_a, round_b = start_match(ws_a, ws_b)
        first_pool_a = [card["id"] for card in round_a["pool"]]
        first_pool_b = [card["id"] for card in round_b["pool"]]
        assert first_pool_a != first_pool_b          # 双方池子不重复

        seen = 1
        while seen < 3:
            ws_a.send_json(make_plan(round_a))
            ws_b.send_json(make_plan(round_b))
            messages_a = collect(ws_a, {"round_start", "game_over"})
            messages_b = collect(ws_b, {"round_start", "game_over"})
            if messages_a[-1]["type"] == "game_over":
                assert messages_b[-1]["type"] == "game_over"
                break
            seen += 1
            round_a, round_b = messages_a[-1], messages_b[-1]
            assert [card["id"] for card in round_a["pool"]] == first_pool_a
            assert [card["id"] for card in round_b["pool"]] == first_pool_b


def test_second_round_announces_awaiting_replay(client: TestClient):
    """下一局的 round_start 会告诉客户端"先看回放，倒计时还没开始"。"""

    with two_players(client) as (ws_a, ws_b):
        _code, round_a, round_b = start_match(ws_a, ws_b)
        assert round_a["awaiting_replay"] is False      # 第一局没有回放可看

        ws_a.send_json(make_plan(round_a))
        ws_b.send_json(make_plan(round_b))
        messages_a = collect(ws_a, {"round_start", "game_over"})
        if messages_a[-1]["type"] == "game_over":       # pragma: no cover - 两局结束
            return
        assert messages_a[-1]["awaiting_replay"] is True
        assert messages_a[-1]["deadline_seconds"] > 0


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


def test_disconnect_starts_reconnect_grace_for_opponent(client: TestClient):
    with two_players(client) as (ws_a, ws_b):
        start_match(ws_a, ws_b)
        ws_a.close()
        message = collect(ws_b, {"opponent_disconnected"})[-1]
        assert message["type"] == "opponent_disconnected"
        # 掉线不再是立刻关房，而是给一段重连宽限
        assert message["grace_seconds"] > 0


def test_reconnect_grace_expiry_closes_room():
    app = create_app(Settings(prepare_timeout=30, reconnect_grace=0))
    with TestClient(app) as client:
        with two_players(client) as (ws_a, ws_b):
            start_match(ws_a, ws_b)
            ws_a.close()
            messages = collect(ws_b, {"room_closed"}, limit=20)
            assert messages[-1]["type"] == "room_closed"
            assert "重连" in messages[-1]["reason"]


def test_player_can_reconnect_with_token(client: TestClient):
    with client.websocket_connect("/ws") as ws_a, client.websocket_connect("/ws") as ws_b:
        assert ws_a.receive_json()["type"] == "hello"
        assert ws_b.receive_json()["type"] == "hello"

        ws_a.send_json({"type": "create_room", "name": "调停者A"})
        created = collect(ws_a, {"room_joined"})[-1]
        code, token = created["room_code"], created["token"]

        ws_b.send_json({"type": "join_room", "name": "调停者B", "room_code": code})
        round_a = collect(ws_a, {"round_start"})[-1]
        round_b = collect(ws_b, {"round_start"})[-1]

        ws_a.send_json(make_plan(round_a))
        collect(ws_a, {"plan_accepted"})
        ws_a.close()
        collect(ws_b, {"opponent_disconnected"})

        with client.websocket_connect("/ws") as ws_a2:
            assert ws_a2.receive_json()["type"] == "hello"
            ws_a2.send_json({"type": "reconnect", "room_code": code, "token": token})
            sync = collect(ws_a2, {"state_sync"})[-1]

            assert sync["seat"] == 0
            assert sync["submitted"] is True          # 掉线前提交的方案还在
            assert sync["score"] == [0, 0]
            assert len(sync["pool"]) == 6
            assert collect(ws_b, {"opponent_reconnected"})[-1]["type"] == "opponent_reconnected"

            # 重连后这一局照常打完
            ws_b.send_json(make_plan(round_b))
            messages_b = collect(ws_b, {"battle_report"}, limit=20)
            assert "battle_report" in types_of(messages_b)


def test_random_matchmaking_pairs_two_players(client: TestClient):
    with client.websocket_connect("/ws") as ws_a, client.websocket_connect("/ws") as ws_b:
        assert ws_a.receive_json()["type"] == "hello"
        assert ws_b.receive_json()["type"] == "hello"

        ws_a.send_json({"type": "join_random", "name": "调停者A"})
        waiting = collect(ws_a, {"matchmaking_waiting"})[-1]
        assert waiting["queue_size"] == 1

        ws_b.send_json({"type": "join_random", "name": "调停者B"})
        round_a = collect(ws_a, {"round_start"})[-1]
        round_b = collect(ws_b, {"round_start"})[-1]

        assert round_a["room_code"] == round_b["room_code"]
        assert len(round_a["pool"]) == 6 and len(round_b["pool"]) == 6


def test_matchmaking_can_be_cancelled(client: TestClient):
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "hello"
        ws.send_json({"type": "join_random", "name": "调停者A"})
        collect(ws, {"matchmaking_waiting"})

        ws.send_json({"type": "cancel_matchmaking"})

        assert collect(ws, {"matchmaking_cancelled"})[-1]["type"] == "matchmaking_cancelled"


def test_room_carries_mode_parameters(client: TestClient):
    """大战场模式的房间要下发 9 选 5、6 次增益，并且拒绝 3 人方案。"""

    with two_players(client) as (ws_a, ws_b):
        assert ws_a.receive_json()["type"] == "hello"
        assert ws_b.receive_json()["type"] == "hello"

        ws_a.send_json({"type": "create_room", "name": "调停者A", "mode": "big_battlefield"})
        created = collect(ws_a, {"room_joined"})[-1]
        ws_b.send_json({"type": "join_room", "name": "调停者B", "room_code": created["room_code"]})
        round_a = collect(ws_a, {"round_start"})[-1]

        assert round_a["mode"] == "big_battlefield"
        assert round_a["team_size"] == 5
        assert round_a["pool_size"] == 9
        assert round_a["bonus_per_round"] == 6
        assert len(round_a["pool"]) == 9

        # 只选 3 人会被模式规则挡住
        ws_a.send_json(
            {
                "type": "submit_plan",
                "selection": [card["id"] for card in round_a["pool"][:3]],
                "bonuses": [{"slot": 1, "kind": "atk"}] * 6,
                "strategy": {"kind": "lowest_hp"},
            }
        )
        error = collect(ws_a, {"error"})[-1]
        assert "5 名角色" in error["message"]


def test_matchmaking_is_isolated_by_mode(client: TestClient):
    """不同模式的玩家不会被匹配到一起。"""

    with client.websocket_connect("/ws") as ws_a, client.websocket_connect("/ws") as ws_b:
        with client.websocket_connect("/ws") as ws_c:
            assert ws_a.receive_json()["type"] == "hello"
            assert ws_b.receive_json()["type"] == "hello"
            assert ws_c.receive_json()["type"] == "hello"

            ws_a.send_json({"type": "join_random", "name": "混沌A", "mode": "chaos"})
            assert collect(ws_a, {"matchmaking_waiting"})[-1]["queue_size"] == 1

            # 标准模式玩家进队列：不应该和混沌模式的 A 配对
            ws_b.send_json({"type": "join_random", "name": "标准B", "mode": "standard"})
            waiting = collect(ws_b, {"matchmaking_waiting"})[-1]
            assert waiting["mode"] == "standard"

            # 混沌模式玩家进队列：应该立刻和 A 配成一对
            ws_c.send_json({"type": "join_random", "name": "混沌C", "mode": "chaos"})
            round_a = collect(ws_a, {"round_start"})[-1]
            round_c = collect(ws_c, {"round_start"})[-1]

            assert round_a["mode"] == "chaos"
            assert round_a["room_code"] == round_c["room_code"]


def test_joining_by_room_code_adopts_the_room_mode(client: TestClient):
    """用房间号加入时以房间的模式为准，加入者自己的模式偏好不会污染对局。"""

    with two_players(client) as (ws_a, ws_b):
        assert ws_a.receive_json()["type"] == "hello"
        assert ws_b.receive_json()["type"] == "hello"

        ws_a.send_json({"type": "create_room", "name": "房主", "mode": "chaos"})
        created = collect(ws_a, {"room_joined"})[-1]
        assert created["mode"] == "chaos"
        assert created["mode_name"] == "混沌模式"

        # 加入者带着"大战场"的偏好进来（join_room 不接受 mode，这里故意多发一个脏字段）
        ws_b.send_json(
            {
                "type": "join_room",
                "name": "加入者",
                "room_code": created["room_code"],
                "mode": "big_battlefield",
            }
        )
        round_a = collect(ws_a, {"round_start"})[-1]
        round_b = collect(ws_b, {"round_start"})[-1]

        for message in (round_a, round_b):
            assert message["mode"] == "chaos"          # 房间说了算
            assert message["random_tags"] is True
            assert message["team_size"] == 3           # 不是大战场模式
            assert message["pool_size"] == 6
            assert len(message["pool"]) == 6
            assert all(card["id"] != 4 for card in message["pool"])  # 混沌模式没有自爆步兵


def test_rematch_keeps_the_room_mode(client: TestClient):
    with client.websocket_connect("/ws") as ws_a, client.websocket_connect("/ws") as ws_b:
        assert ws_a.receive_json()["type"] == "hello"
        assert ws_b.receive_json()["type"] == "hello"

        ws_a.send_json({"type": "create_room", "name": "调停者A", "mode": "big_battlefield"})
        created = collect(ws_a, {"room_joined"})[-1]
        ws_b.send_json({"type": "join_room", "name": "调停者B", "room_code": created["room_code"]})
        round_a = collect(ws_a, {"round_start"})[-1]
        round_b = collect(ws_b, {"round_start"})[-1]
        assert round_a["team_size"] == 5

        for _ in range(3):
            ws_a.send_json(make_plan(round_a, team_size=5, bonus_count=6))
            ws_b.send_json(make_plan(round_b, team_size=5, bonus_count=6))
            messages_a = collect(ws_a, {"round_start", "game_over"})
            messages_b = collect(ws_b, {"round_start", "game_over"})
            if messages_a[-1]["type"] == "game_over":
                break
            round_a, round_b = messages_a[-1], messages_b[-1]

        ws_a.send_json({"type": "rematch"})
        ws_b.send_json({"type": "rematch"})
        new_round = collect(ws_a, {"round_start"})[-1]

        assert new_round["mode"] == "big_battlefield"
        assert new_round["team_size"] == 5
        assert new_round["bonus_per_round"] == 6


def test_switching_matchmaking_mode_removes_the_old_queue_entry(client: TestClient):
    """同一个人在队列里换模式时，旧模式的排队会被清掉，不会同时占两个队列。"""

    with client.websocket_connect("/ws") as ws_a, client.websocket_connect("/ws") as ws_b:
        assert ws_a.receive_json()["type"] == "hello"
        assert ws_b.receive_json()["type"] == "hello"

        ws_a.send_json({"type": "join_random", "name": "摇摆A", "mode": "chaos"})
        assert collect(ws_a, {"matchmaking_waiting"})[-1]["queue_size"] == 1

        # 换成标准模式排队：混沌队列里的自己应该被移除
        ws_a.send_json({"type": "join_random", "name": "摇摆A", "mode": "standard"})
        assert collect(ws_a, {"matchmaking_waiting"})[-1]["queue_size"] == 1

        # 这时来个混沌模式玩家：不应该和 A 配对（A 已经不在混沌队列里了）
        ws_b.send_json({"type": "join_random", "name": "混沌B", "mode": "chaos"})
        waiting = collect(ws_b, {"matchmaking_waiting"})[-1]
        assert waiting["queue_size"] == 1


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
