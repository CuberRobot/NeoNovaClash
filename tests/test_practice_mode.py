"""练习模式：单人和一个电脑对手直接开局，不进匹配队列。"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from backend.config import Settings
from backend.core import rules
from backend.core.room import BOT_NAME, Phase, Room
from backend.web.app import create_app


def make_practice_room(prepare_timeout: int = 30) -> Room:
    room = Room("PRAC", seed=11, prepare_timeout=prepare_timeout)
    room.join("玩家")
    room.join_bot()
    return room


def test_practice_room_starts_with_a_bot_seat():
    room = make_practice_room()
    assert room.phase is Phase.PREPARING
    assert room.round_index == 1
    bot = room.players[1]
    assert bot.is_bot is True
    assert bot.name == BOT_NAME
    assert bot.to_dict()["is_bot"] is True


def test_round_start_tells_clients_who_the_opponent_is():
    room = make_practice_room()
    outgoings = room.start_round()
    payload = next(o.payload for o in outgoings if o.seat == 0)

    assert payload["players"][1]["name"] == BOT_NAME
    assert payload["players"][1]["is_bot"] is True
    assert payload["players"][0]["is_bot"] is False


def test_bot_submits_a_plan_by_itself():
    room = make_practice_room()
    bot = room.players[1]
    bot.bot_action_at = 0.0          # 让机器人"立刻"行动，测试不用等

    outgoings = room.tick(now=time.monotonic())
    kinds = [o.payload["type"] for o in outgoings if o.seat == bot.seat]

    assert bot.submitted is True
    assert "plan_accepted" in kinds


def test_bot_acknowledges_replay_and_waits_for_timer():
    room = make_practice_room()
    room.start_round(after_battle=True)
    bot = room.players[1]
    assert room.awaiting_replay is True

    bot.bot_action_at = 0.0
    room.tick(now=time.monotonic())
    assert bot.replay_done is True
    assert room.awaiting_replay is True      # 玩家还没确认，倒计时仍然没开始

    room.mark_replay_done(0)
    assert room.awaiting_replay is False
    assert room.deadline is not None


def test_bot_picks_a_draft_card():
    room = make_practice_room()
    room.start_round(after_battle=True)
    bot = room.players[1]
    bot.replay_done = True
    room.players[0].replay_done = True
    room.begin_prepare()
    assert len(bot.candidates) == 3

    bot.bot_action_at = 0.0
    room.tick(now=time.monotonic())

    assert bot.picked is True
    assert len(bot.pool) == 7               # 机器人也会补卡（7 选 3）


def test_bot_agrees_to_rematch():
    room = make_practice_room()
    room.phase = Phase.FINISHED
    outgoings = room.request_rematch(0)

    assert any(o.payload["type"] == "rematch_started" for o in outgoings)
    assert room.round_index == 1


def test_practice_room_never_enters_matchmaking_queue():
    app = create_app(Settings(prepare_timeout=5))
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"] == "hello"
            ws.send_json({"type": "create_practice", "name": "练习者", "mode": "standard"})

            kinds = []
            for _ in range(6):
                message = ws.receive_json()
                kinds.append(message["type"])
                if message["type"] == "round_start":
                    assert message["players"][1]["is_bot"] is True
                    assert message["players"][1]["name"] == BOT_NAME
                    break

            assert "room_joined" in kinds
            assert "round_start" in kinds
            assert app.state.hub.queue_size() == 0     # 练习模式不占匹配队列
            assert len(app.state.hub.rooms) == 1


def test_practice_room_is_isolated_from_random_matching():
    """练习房里的电脑对手不该被其他玩家匹配到。"""

    app = create_app(Settings(prepare_timeout=5))
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as practice:
            assert practice.receive_json()["type"] == "hello"
            practice.send_json({"type": "create_practice", "name": "练习者", "mode": "standard"})
            while practice.receive_json()["type"] != "round_start":
                pass

            with client.websocket_connect("/ws") as other:
                assert other.receive_json()["type"] == "hello"
                other.send_json({"type": "join_random", "name": "真人", "mode": "standard"})
                waiting = other.receive_json()

                assert waiting["type"] == "matchmaking_waiting"
                assert waiting["queue_size"] == 1


def test_random_plan_is_used_for_the_bot():
    """机器人用的是同一套合法方案生成器，所以练习局也不会出现非法阵容。"""

    room = make_practice_room()
    bot = room.players[1]
    plan = rules.random_plan(bot.pool, room.rng, room.mode)
    rules.validate_plan(bot.pool, plan, room.mode)   # 不抛异常即合法
