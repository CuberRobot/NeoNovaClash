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


# ---------------------------------------------------------------- 补卡
def test_first_round_has_no_card_draft():
    room = make_room()
    assert [len(player.pool) for player in room.players] == [6, 6]
    assert all(player.candidates == [] for player in room.players)
    assert room.deck                       # 牌堆已经备好（20 - 12 = 8）
    assert len(room.deck) == 8


def test_card_draft_grows_pool_by_one_each_round():
    room = make_room()
    room.start_round(after_battle=True)     # 第二局

    assert [len(player.candidates) for player in room.players] == [3, 3]
    assert [len(player.pool) for player in room.players] == [6, 6]

    pick_one_each(room)
    assert [len(player.pool) for player in room.players] == [7, 7]   # 第二局 7 选 3
    assert all(player.candidates == [] for player in room.players)

    room.start_round(after_battle=True)     # 第三局
    assert [len(player.candidates) for player in room.players] == [3, 3]
    assert [len(player.pool) for player in room.players] == [7, 7]

    pick_one_each(room)
    assert [len(player.pool) for player in room.players] == [8, 8]   # 第三局 8 选 3


def test_card_draft_never_duplicates_between_players():
    room = make_room()
    for _ in range(2):                      # 第二、三局各选一次
        room.start_round(after_battle=True)
        offered = [card.id for player in room.players for card in player.candidates]
        assert len(offered) == len(set(offered))          # 同一局两人不会看到同一张候选
        pick_one_each(room)

    ids = [[card.id for card in player.pool] for player in room.players]
    assert len(set(ids[0]) & set(ids[1])) == 0            # 补完卡仍然不重名
    assert [len(pool) for pool in ids] == [8, 8]


def test_rejected_cards_go_back_to_the_deck():
    room = make_room()
    room.start_round(after_battle=True)
    before = len(room.deck)
    offered = list(room.players[0].candidates)
    room.pick_candidate(0, offered[0].id)
    # 送出去一张，退回来两张
    assert len(room.deck) == before + 2


def test_submitting_without_picking_auto_drafts():
    room = make_room()
    room.start_round(after_battle=True)
    player = room.players[0]
    plan = _plan_for(player)

    outgoings = room.submit(0, plan)
    kinds = [o.payload["type"] for o in outgoings if o.seat == 0]
    assert "pool_updated" in kinds            # 系统代选了一张
    assert len(player.pool) == 7
    assert player.picked is True


def test_timeout_auto_drafts_before_auto_submit():
    room = make_room(prepare_timeout=1)
    room.start_round(after_battle=True)
    room.players[0].replay_done = True
    room.players[1].replay_done = True
    room.begin_prepare()

    outgoings = room.tick(now=room.deadline + 0.1)
    kinds = [o.payload["type"] for o in outgoings]
    assert "pool_updated" in kinds
    assert "battle_report" in kinds
    assert all(len(player.pool) == 7 for player in room.players)


def test_big_battlefield_has_no_card_draft():
    """大战场 9 选 5 用掉 18 张，只剩 2 张，不够发补卡候选。"""

    from backend.core import modes

    room = Room("BIG", seed=1, mode=modes.get_mode("big_battlefield"))
    room.join("占位甲")
    room.join("占位乙")
    room.start_round(after_battle=True)

    assert all(player.candidates == [] for player in room.players)
    room.start_round(after_battle=True)
    assert all(player.candidates == [] for player in room.players)


def _plan_for(player):
    """给这名玩家手里 7~8 张牌拼一份合法方案（自爆步兵自动放首位）。"""

    from backend.core.models import Bonus, Plan, Strategy

    pool = player.pool
    selection = [card.id for card in pool[: 3]]
    first = next((card.id for card in pool if getattr(card, "place_first", False)), None)
    if first is not None and first not in selection:
        selection = [first, *selection[:2]]
    bonuses = tuple(
        Bonus(slot=(index % 3) + 1, kind="atk" if index % 2 == 0 else "hp") for index in range(4)
    )
    return Plan(
        selection=selection,
        bonuses=bonuses,
        strategy=Strategy(kind="lowest_hp"),
    )


def pick_one_each(room: Room) -> None:
    """双方各从自己的 3 张候选里选第 1 张。"""

    for player in room.players:
        room.pick_candidate(player.seat, player.candidates[0].id)
