"""匹配队列的健壮性：不能让"已经断开的连接"当成对手配给真人。"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketState

from backend.config import Settings
from backend.web.app import create_app
from backend.web.hub import QueuedPlayer, Session


class FakeSocket:
    """只用来占位的"连接"：可以伪造掉线状态。"""

    def __init__(self, state: WebSocketState = WebSocketState.CONNECTED) -> None:
        self.client_state = state
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


def make_queued(name: str, state: WebSocketState, mode_key: str = "standard") -> QueuedPlayer:
    session = Session()
    session.name = name
    return QueuedPlayer(session=session, name=name, since=time.monotonic(), websocket=FakeSocket(state), mode_key=mode_key)  # type: ignore[arg-type]


def test_take_opponent_skips_disconnected_players():
    app = create_app(Settings(prepare_timeout=5))
    hub = app.state.hub
    hub.queue.append(make_queued("幽灵玩家", WebSocketState.DISCONNECTED))

    assert hub.take_opponent("standard") is None     # 断开的连接不能当对手
    assert hub.queue_size() == 0                     # 顺手把它清出队列


def test_take_opponent_still_matches_live_players():
    app = create_app(Settings(prepare_timeout=5))
    hub = app.state.hub
    hub.queue.append(make_queued("真人", WebSocketState.CONNECTED))

    opponent = hub.take_opponent("standard")
    assert opponent is not None and opponent.name == "真人"


def test_reap_stale_queue_drops_dead_and_expired_entries():
    app = create_app(Settings(prepare_timeout=5))
    hub = app.state.hub
    fresh = make_queued("活着", WebSocketState.CONNECTED)
    stale = make_queued("排太久", WebSocketState.CONNECTED)
    stale.since -= 100_000
    hub.queue.extend([fresh, stale, make_queued("掉线了", WebSocketState.DISCONNECTED)])

    dropped = hub.reap_stale_queue()

    assert sorted(item.name for item in dropped) == ["掉线了", "排太久"]
    assert [item.name for item in hub.queue] == ["活着"]


def test_cancelled_stale_entry_is_told_why():
    """排队超时被清掉时，如果连接还在就告诉他一声，不要静默消失。"""

    import asyncio

    app = create_app(Settings(prepare_timeout=5))
    hub = app.state.hub
    stale = make_queued("排太久", WebSocketState.CONNECTED)
    stale.since -= 100_000
    hub.queue.append(stale)

    dropped = hub.reap_stale_queue()
    asyncio.run(hub._notify_queue_dropped(dropped[0]))

    assert stale.websocket.sent[0]["type"] == "matchmaking_cancelled"  # type: ignore[attr-defined]


def test_join_random_ignores_ghost_and_queues_the_player():
    """真实场景复现：队列里只有一条死连接时，玩家应该是"排队中"，而不是瞬间开局。"""

    app = create_app(Settings(prepare_timeout=5))
    with TestClient(app) as client:
        app.state.hub.queue.append(make_queued("幽灵玩家", WebSocketState.DISCONNECTED))
        with client.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"] == "hello"
            ws.send_json({"type": "join_random", "name": "真人", "mode": "standard"})
            message = ws.receive_json()

            assert message["type"] == "matchmaking_waiting"
            assert message["queue_size"] == 1


def test_health_exposes_queue_and_room_diagnostics():
    app = create_app(Settings(prepare_timeout=5))
    with TestClient(app) as client:
        body = client.get("/api/health").json()

        assert body["queue"] == 0
        assert body["room_list"] == []

        app.state.hub.queue.append(make_queued("排队中", WebSocketState.CONNECTED))
        body = client.get("/api/health").json()
        assert body["queue"] == 1
