"""联机冒烟测试：让两个机器人客户端在真实服务上跑完一整套三局两胜。

用法：
    # 先启动服务：python run.py
    python -m tools.smoke_pvp                     # 默认连 ws://127.0.0.1:8000/ws
    python -m tools.smoke_pvp --url ws://127.0.0.1:8123/ws

适用场景：改完服务端或前端逻辑后，用来确认「建房 → 加入 → 提交 → 结算 → 结束」
整条链路依然通畅。脚本只使用标准库与 websockets。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random

import websockets

from backend.core import constants as C
from backend.core.models import BONUS_ATK, BONUS_HP


def random_plan(round_start: dict, rng: random.Random) -> dict:
    """根据服务端下发的角色池随机拼一份合法方案。"""

    pool = round_start["pool"]
    first = [card for card in pool if card["place_first"]]
    others = [card for card in pool if not card["place_first"]]

    selection: list[int] = []
    if first and rng.random() < 0.5:
        selection.append(rng.choice(first)["id"])
        selection.extend(card["id"] for card in rng.sample(others, C.TEAM_SIZE - 1))
    else:
        picked = rng.sample(others, C.TEAM_SIZE)
        selection = [card["id"] for card in picked]

    slots = [slot for slot in range(1, C.TEAM_SIZE + 1) for _ in range(C.MAX_BONUS_PER_FIGHTER)]
    rng.shuffle(slots)
    bonuses = [
        {"slot": slot, "kind": rng.choice([BONUS_ATK, BONUS_HP])}
        for slot in slots[: C.BONUS_PER_ROUND]
    ]
    return {
        "type": "submit_plan",
        "selection": selection,
        "bonuses": bonuses,
        "strategy": {"kind": "lowest_hp", "tag": None},
    }


async def receive_until(ws, stop_types: set[str], limit: int = 60) -> list[dict]:
    messages: list[dict] = []
    for _ in range(limit):
        message = json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
        messages.append(message)
        if message.get("type") in stop_types:
            break
    return messages


async def play(url: str, verbose: bool) -> int:
    rng = random.Random()
    async with websockets.connect(url) as ws_a, websockets.connect(url) as ws_b:
        await ws_a.recv(), await ws_b.recv()  # hello

        await ws_a.send(json.dumps({"type": "create_room", "name": "机器人A"}))
        created = (await receive_until(ws_a, {"room_joined"}))[-1]
        code = created["room_code"]
        print(f"房间号 {code}（seat {created['seat']}）")

        await ws_b.send(json.dumps({"type": "join_room", "name": "机器人B", "room_code": code}))
        round_a = (await receive_until(ws_a, {"round_start"}))[-1]
        round_b = (await receive_until(ws_b, {"round_start"}))[-1]

        rounds = 0
        while rounds < 4:
            rounds += 1
            await ws_a.send(json.dumps(random_plan(round_a, rng)))
            await ws_b.send(json.dumps(random_plan(round_b, rng)))
            messages_a = await receive_until(ws_a, {"round_start", "game_over"})
            messages_b = await receive_until(ws_b, {"round_start", "game_over"})
            report = next(m for m in messages_a if m["type"] == "battle_report")
            result = report["result"]
            assert "battle_report" in [m["type"] for m in messages_b], "对手没有收到战报"
            print(
                f"第 {rounds} 局：{'平局' if result['winner_team'] is None else '第 ' + 'AB'[result['winner_team']] + ' 队获胜'}"
                f"（{result['reason']}，{result['rounds']} 回合，{result['event_count']} 条事件）"
            )
            if verbose:
                for event in result["events"]:
                    print("   ", event["text"])
            if messages_a[-1]["type"] == "game_over":
                game_over = messages_a[-1]
                print(f"整场结束：比分 {game_over['score'][0]} : {game_over['score'][1]}")
                break
            round_a = messages_a[-1]
            round_b = messages_b[-1]
        else:
            print("警告：4 局之内没有结束，请检查规则")
            return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="NeoNovaClash 联机冒烟测试")
    parser.add_argument("--url", default="ws://127.0.0.1:8000/ws", help="WebSocket 地址")
    parser.add_argument("--verbose", action="store_true", help="打印完整战报")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(play(args.url, args.verbose)))


if __name__ == "__main__":
    main()
