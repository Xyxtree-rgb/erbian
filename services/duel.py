"""联机对辩房间。

设计取舍：
- 用内存字典存房间，不上数据库。重启即清空——演示场景够用，
  真要做长线再换成 SQLite（结构已经按可持久化的形状写）。
- 同步用「客户端轮询 + rev 版本号」，不上 WebSocket。
  辩论本来就是回合制，1~2 秒延迟不影响体验，而 WebSocket 要动部署。
- 房间只有两个座位。空位由 AI 补，真人进来可以把 AI 顶掉。
"""

from __future__ import annotations

import random
import threading
import time
from typing import Any

ROOM_TTL = 60 * 60  # 一小时没人动就回收
MAX_HP = 100
CODE_ALPHABET = "ACDEFGHJKLMNPQRTUVWXY34679"  # 去掉易混字符

# 题库：每条都带法条，AI 判定时能落地
TOPICS = [
    {
        "q": "公司要求员工下班后必须秒回工作消息，不回就扣绩效。",
        "side": "你认为公司无权这样要求",
        "law": "《劳动法》第四十一条规定延长工作时间需与工会和劳动者协商，且每日不得超过一小时、每月不得超过三十六小时；下班后的实质劳动应付加班费。",
    },
    {
        "q": "商家宣传「全网最低价」，你买到手发现比别家贵三十块。",
        "side": "你认为这构成虚假宣传",
        "law": "《消费者权益保护法》第二十条规定经营者不得作虚假或者引人误解的宣传；第五十五条对欺诈行为规定了退一赔三。",
    },
    {
        "q": "房东说押金不退，因为「墙面有正常使用的痕迹」。",
        "side": "你认为押金应当退还",
        "law": "《民法典》第七百一十条规定承租人按约定方法使用租赁物致使损耗的，不承担赔偿责任；押金不是违约金，扣减须有约定与证据。",
    },
    {
        "q": "公司规定女员工入职三年内不得生育。",
        "side": "你认为这条规定无效",
        "law": "《妇女权益保障法》第四十三条、《女职工劳动保护特别规定》第五条禁止用人单位限制女职工生育或因此降低待遇、解除合同。",
    },
    {
        "q": "外卖骑手闯红灯被撞，平台说「他不是我们员工」。",
        "side": "你认为平台应当担责",
        "law": "《民法典》第一千一百九十一条规定用人单位工作人员执行工作任务造成他人损害的，由用人单位承担侵权责任；新业态用工按实际管理关系认定。",
    },
]

_lock = threading.Lock()
_rooms: dict[str, dict[str, Any]] = {}


def _new_code() -> str:
    while True:
        code = "".join(random.choice(CODE_ALPHABET) for _ in range(6))
        if code not in _rooms:
            return code


def _token() -> str:
    return "%08x%08x" % (random.getrandbits(32), random.getrandbits(32))


def _sweep() -> None:
    now = time.time()
    for code in [c for c, r in _rooms.items() if now - r["touched"] > ROOM_TTL]:
        _rooms.pop(code, None)


def _seat_of(room: dict[str, Any], token: str) -> int | None:
    for idx, seat in room["seats"].items():
        if seat.get("token") and seat["token"] == token:
            return idx
    return None


def _public(room: dict[str, Any], me: int | None) -> dict[str, Any]:
    seats = []
    for idx in (0, 1):
        seat = room["seats"][idx]
        seats.append(
            {
                "index": idx,
                "name": seat["name"],
                "is_ai": seat["is_ai"],
                "hp": seat["hp"],
                "you": idx == me,
            }
        )
    return {
        "code": room["code"],
        "rev": room["rev"],
        "status": room["status"],
        "turn": room["turn"],
        "me": me,
        "seats": seats,
        "topic": room["topic"],
        "log": room["log"][-40:],
        "winner": room.get("winner"),
        "thinking": room.get("thinking", False),
    }


def create(name: str) -> dict[str, Any]:
    with _lock:
        _sweep()
        code = _new_code()
        token = _token()
        topic = dict(random.choice(TOPICS))
        room = {
            "code": code,
            "created": time.time(),
            "touched": time.time(),
            "rev": 1,
            "status": "playing",
            "turn": 0,
            "topic": topic,
            "seats": {
                0: {"name": name or "红方", "is_ai": False, "hp": MAX_HP, "token": token},
                1: {"name": "桃弁", "is_ai": True, "hp": MAX_HP, "token": None},
            },
            "log": [],
            "winner": None,
            "thinking": False,
        }
        _rooms[code] = room
        return {"code": code, "token": token, "seat": 0, "state": _public(room, 0)}


def join(code: str, name: str) -> tuple[int, dict[str, Any]]:
    code = (code or "").strip().upper()
    with _lock:
        _sweep()
        room = _rooms.get(code)
        if not room:
            return 404, {"error": "没有这个房间，检查一下房间码"}
        if room["status"] == "over":
            return 409, {"error": "这一局已经打完了，让房主重开一间"}
        target = None
        for idx in (1, 0):
            if room["seats"][idx]["is_ai"]:
                target = idx
                break
        if target is None:
            return 409, {"error": "这个房间已经坐满了"}
        token = _token()
        room["seats"][target] = {
            "name": name or ("红方" if target == 0 else "蓝方"),
            "is_ai": False,
            "hp": room["seats"][target]["hp"],
            "token": token,
        }
        room["rev"] += 1
        room["touched"] = time.time()
        room["log"].append(
            {"from": -1, "name": "系统", "text": "对手进来了，开打。", "ts": time.time()}
        )
        return 200, {"code": code, "token": token, "seat": target, "state": _public(room, target)}


def snapshot(code: str, token: str = "") -> tuple[int, dict[str, Any]]:
    with _lock:
        room = _rooms.get((code or "").strip().upper())
        if not room:
            return 404, {"error": "房间不存在或已过期"}
        me = _seat_of(room, token)
        return 200, _public(room, me)


def _finish(room: dict[str, Any], loser: int) -> None:
    room["status"] = "over"
    room["winner"] = 1 - loser
    room["rev"] += 1
    room["log"].append(
        {
            "from": -1,
            "name": "系统",
            "text": "%s 说不出话了。" % room["seats"][loser]["name"],
            "ts": time.time(),
        }
    )


def say(code: str, token: str, text: str, judge) -> tuple[int, dict[str, Any]]:
    """玩家发言：判一次，结算伤害，把回合交给对方。

    judge(text, topic) 由调用方注入，返回 {valid, damage, type, reply, reason}。
    这样这个模块不依赖 DeepSeek，测试可以塞假裁判进来。
    """
    code = (code or "").strip().upper()
    with _lock:
        room = _rooms.get(code)
        if not room:
            return 404, {"error": "房间不存在或已过期"}
        me = _seat_of(room, token)
        if me is None:
            return 403, {"error": "你不在这个房间里"}
        if room["status"] != "playing":
            return 409, {"error": "这一局已经结束了"}
        if room["turn"] != me:
            return 409, {"error": "还没轮到你"}
        text = (text or "").strip()
        if not text:
            return 400, {"error": "说点什么"}
        if len(text) > 300:
            text = text[:300]
        room["thinking"] = True
        room["rev"] += 1

    result = judge(text, room["topic"])

    with _lock:
        room = _rooms.get(code)
        if not room:
            return 404, {"error": "房间不存在或已过期"}
        room["thinking"] = False
        me = _seat_of(room, token)
        opp = 1 - me
        damage = int(result.get("damage") or 0)
        room["seats"][opp]["hp"] = max(0, room["seats"][opp]["hp"] - damage)
        room["log"].append(
            {
                "from": me,
                "name": room["seats"][me]["name"],
                "text": text,
                "valid": bool(result.get("valid")),
                "kind": result.get("type") or "无效",
                "damage": damage,
                "reason": result.get("reason") or "",
                "ts": time.time(),
            }
        )
        room["rev"] += 1
        room["touched"] = time.time()
        if room["seats"][opp]["hp"] <= 0:
            _finish(room, opp)
        else:
            room["turn"] = opp
        return 200, _public(room, me)


def ai_turn(code: str, judge) -> tuple[int, dict[str, Any]]:
    """AI 回合：取上一轮模型给的台词，判一次，扣玩家的血。"""
    code = (code or "").strip().upper()
    with _lock:
        room = _rooms.get(code)
        if not room:
            return 404, {"error": "房间不存在或已过期"}
        seat = room["seats"][room["turn"]]
        if not seat["is_ai"]:
            return 409, {"error": "现在不是 AI 的回合"}
        if room["status"] != "playing":
            return 409, {"error": "这一局已经结束了"}
        if room.get("thinking"):
            return 202, _public(room, None)
        line = room.get("pending_ai") or "你说的这些，站不住。"
        room["thinking"] = True
        room["rev"] += 1

    result = judge(line, room["topic"])

    with _lock:
        room = _rooms.get(code)
        if not room:
            return 404, {"error": "房间不存在或已过期"}
        room["thinking"] = False
        me = room["turn"]
        opp = 1 - me
        damage = int(result.get("damage") or 0)
        room["seats"][opp]["hp"] = max(0, room["seats"][opp]["hp"] - damage)
        room["log"].append(
            {
                "from": me,
                "name": room["seats"][me]["name"],
                "text": line,
                "valid": bool(result.get("valid")),
                "kind": result.get("type") or "无效",
                "damage": damage,
                "reason": result.get("reason") or "",
                "ts": time.time(),
            }
        )
        room["rev"] += 1
        room["touched"] = time.time()
        if room["seats"][opp]["hp"] <= 0:
            _finish(room, opp)
        else:
            room["turn"] = opp
        return 200, _public(room, None)


def stash_ai_line(code: str, line: str) -> None:
    """把模型刚给出的反击存起来，轮到 AI 时直接用，省一次调用。"""
    with _lock:
        room = _rooms.get(code)
        if room and line:
            room["pending_ai"] = line
