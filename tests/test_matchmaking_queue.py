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


def test_clicking_random_twice_does_not_match_with_yourself():
    """连点两次随机匹配：绝不能把自己排队的那条记录当成对手。

    这是线上真实出现过的 bug：第二次 join_random 会把自己第一次的队列记录
    弹出来当对手，于是同一条连接占了两个座位，玩家瞬间"匹配成功"、
    然后对着一个永远不动的自己打完一场。
    """

    app = create_app(Settings(prepare_timeout=5))
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"] == "hello"
            ws.send_json({"type": "join_random", "name": "手速很快", "mode": "standard"})
            first = ws.receive_json()
            assert first["type"] == "matchmaking_waiting"

            ws.send_json({"type": "join_random", "name": "手速很快", "mode": "standard"})
            second = ws.receive_json()

            assert second["type"] == "matchmaking_waiting"     # 仍然是排队中，不是开局
            assert second["queue_size"] == 1                   # 队列里只有自己一条
            assert len(app.state.hub.rooms) == 0               # 没有创建任何房间


def test_take_opponent_can_exclude_your_own_session():
    """就算调用方忘了先出队，也不允许把自己配给自己。"""

    app = create_app(Settings(prepare_timeout=5))
    hub = app.state.hub
    mine = make_queued("我", WebSocketState.CONNECTED)
    hub.queue.append(mine)

    assert hub.take_opponent("standard", exclude=mine.session) is None

    other = make_queued("别人", WebSocketState.CONNECTED)
    hub.queue.append(other)
    assert hub.take_opponent("standard", exclude=mine.session) is other


def test_health_exposes_queue_and_room_diagnostics():
    app = create_app(Settings(prepare_timeout=5))
    with TestClient(app) as client:
        body = client.get("/api/health").json()

        assert body["queue"] == 0
        assert body["room_list"] == []

        app.state.hub.queue.append(make_queued("排队中", WebSocketState.CONNECTED))
        body = client.get("/api/health").json()
        assert body["queue"] == 1
