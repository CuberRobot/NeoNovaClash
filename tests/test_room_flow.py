"""房间状态机测试：角色池整场固定、回放确认与倒计时、赛后重连恢复结算。"""

from __future__ import annotations

import time

from backend.core import constants as C
from backend.core.room import Phase, Room


def make_room(prepare_timeout: int = 30) -> Room:
    room = Room("TEST", seed=2026, prepare_timeout=prepare_timeout)
    room.join("占位甲")
    room.join("占位乙")          # 第二个加入会触发第一局
    return room


def pool_ids(room: Room) -> list[list[int]]:
    return [[card.id for card in player.pool] for player in room.players]


def test_pool_is_rolled_once_per_match():
    room = make_room()
    first = pool_ids(room)
    assert all(first)                          # 第一局就有池子

    room.start_round()
    assert pool_ids(room) == first             # 第二局沿用同一份池子

    room.start_round()
    assert pool_ids(room) == first


def test_rematch_rolls_a_brand_new_pool():
    room = make_room()
    before = pool_ids(room)

    room.phase = Phase.FINISHED
    room.request_rematch(0)
    outgoings = room.request_rematch(1)        # 双方都同意才重开

    assert any(o.payload.get("type") == "rematch_started" for o in outgoings)
    assert pool_ids(room) != before            # 新的一场重新抽池
    assert room.round_index == 1
    assert [player.score for player in room.players] == [0, 0]


def test_prepare_timer_starts_only_after_both_replay_acks():
    room = make_room(prepare_timeout=30)
    room.start_round(after_battle=True)

    assert room.awaiting_replay is True
    assert room.deadline is None                       # 倒计时还没开始

    assert room.mark_replay_done(0) == []              # 只有一方确认，什么都不发生
    assert room.deadline is None

    outgoings = room.mark_replay_done(1)               # 两边都确认了
    assert [o.payload["type"] for o in outgoings] == ["prepare_started"]
    assert room.awaiting_replay is False
    assert room.deadline is not None
    remaining = room.deadline - time.monotonic()
    assert 25 < remaining <= 30


def test_replay_grace_is_a_fallback_for_missing_ack():
    room = make_room(prepare_timeout=30)
    room.start_round(after_battle=True)
    assert room.replay_deadline is not None

    # 还没到兜底时间：什么都不做
    assert room.tick(now=room.replay_deadline - 1) == []
    assert room.deadline is None

    # 到了兜底时间（有人一直没确认）：照样开始倒计时，不能把对局卡死
    outgoings = room.tick(now=room.replay_deadline + 0.1)
    assert [o.payload["type"] for o in outgoings] == ["prepare_started"]
    assert room.deadline is not None


def test_replay_grace_default_is_sane():
    # 兜底时间要够长（能覆盖一场 5v5 回放），又明显短于备战限时
    assert 15 <= C.REPLAY_GRACE_SECONDS <= C.PREPARE_TIMEOUT_SECONDS / 2


def test_state_sync_of_finished_room_carries_final_result():
    room = make_room()
    room.phase = Phase.FINISHED
    room.final_payload = {
        "winner_seat": 0,
        "score": [2, 1],
        "history": [{"round_index": 1, "winner_seat": 0, "rounds": 5}],
        "players": [{"seat": player.seat, "name": player.name} for player in room.players],
    }

    payload = room.state_sync_payload(room.players[0])
    assert payload["phase"] == "finished"
    assert payload["final"]["score"] == [2, 1]
    assert payload["final"]["winner_seat"] == 0
