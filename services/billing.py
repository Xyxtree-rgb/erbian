"""付费与权益。零外部依赖。

当前阶段（用户明确要求）：**接口全部做好，但不收钱。**
开关 ERBIAN_PAY_ENABLED=1 之前，create_order 只登记意向订单、不产生任何收款动作；
兑换码通道现在就能用——内测期给学生发码手动开通，正是要走的这条路。
"""

from __future__ import annotations

import os
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from services import account

_LOCK = threading.Lock()
_READY = False
_CST = timezone(timedelta(hours=8))

# 套餐表。改价只改这里，前端 /api/billing/plans 直接读它。
PLANS = [
    {"code": "monthly", "name": "月卡", "price_fen": 3800, "days": 30, "desc": "解锁全部关卡与渡口"},
    {"code": "quarterly", "name": "季卡", "price_fen": 6800, "days": 90, "desc": "解锁全部关卡与渡口"},
    {"code": "yearly", "name": "年卡", "price_fen": 26800, "days": 365, "desc": "解锁全部关卡与渡口 · 含每周更新"},
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    order_no TEXT PRIMARY KEY,
    uid TEXT,
    plan_code TEXT,
    amount_fen INTEGER,
    status TEXT,
    provider TEXT,
    provider_order TEXT,
    created_at TEXT,
    paid_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_orders_uid ON orders(uid);

CREATE TABLE IF NOT EXISTS licenses (
    code TEXT PRIMARY KEY,
    plan_code TEXT,
    days INTEGER,
    note TEXT,
    created_at TEXT,
    used_by TEXT,
    used_at TEXT
);
"""


class BillingError(Exception):
    pass


def pay_enabled() -> bool:
    return os.environ.get("ERBIAN_PAY_ENABLED", "0") == "1"


def db_path() -> str:
    return account.db_path()


def _now() -> str:
    return datetime.now(_CST).strftime("%Y-%m-%d %H:%M:%S")


def _ensure() -> None:
    global _READY
    if _READY:
        return
    with _LOCK:
        if _READY:
            return
        conn = sqlite3.connect(db_path(), timeout=3.0)
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()
        _READY = True


def plan_of(code: str) -> dict | None:
    for p in PLANS:
        if p["code"] == code:
            return p
    return None


def list_plans() -> list:
    return [
        dict(p, price_yuan=p["price_fen"] / 100, enabled=pay_enabled())
        for p in PLANS
    ]


def _new_order_no() -> str:
    return "EB" + datetime.now(_CST).strftime("%Y%m%d%H%M%S") + secrets.token_hex(2).upper()


def create_order(uid: str, plan_code: str) -> dict:
    """下单。开关没开时只登记意向，不产生收款动作。"""
    _ensure()
    plan = plan_of(plan_code)
    if not plan:
        raise BillingError("套餐不存在")
    if not uid:
        raise BillingError("请先登录")

    order_no = _new_order_no()
    status = "pending" if pay_enabled() else "pending_off"
    conn = sqlite3.connect(db_path(), timeout=3.0)
    try:
        conn.execute(
            "INSERT INTO orders (order_no, uid, plan_code, amount_fen, status, provider,"
            " provider_order, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (order_no, uid, plan_code, plan["price_fen"], status,
             os.environ.get("ERBIAN_PAY_PROVIDER", ""), "", _now()),
        )
        conn.commit()
    finally:
        conn.close()

    if not pay_enabled():
        return {
            "order_no": order_no,
            "status": status,
            "pay_url": None,
            "amount_fen": plan["price_fen"],
            "note": "内测期暂不开通收款，已记下你的意向",
        }
    # 开关打开后在这里接微信支付/支付宝的统一下单，返回真实 pay_url
    raise BillingError("支付通道尚未接入")


def mark_paid(order_no: str, provider_order: str = "") -> dict:
    """支付回调：把订单置为已付并开通权益。"""
    _ensure()
    conn = sqlite3.connect(db_path(), timeout=3.0)
    try:
        row = conn.execute(
            "SELECT uid, plan_code, status FROM orders WHERE order_no = ?", (order_no,)
        ).fetchone()
        if not row:
            raise BillingError("订单不存在")
        uid, plan_code, status = row
        if status == "paid":
            return {"ok": True, "already": True}
        conn.execute(
            "UPDATE orders SET status = 'paid', paid_at = ?, provider_order = ? WHERE order_no = ?",
            (_now(), provider_order, order_no),
        )
        conn.commit()
    finally:
        conn.close()
    plan = plan_of(plan_code)
    account.set_tier(uid, plan["code"], plan["days"])
    return {"ok": True, "uid": uid, "plan": plan["code"]}


def generate_licenses(plan_code: str, count: int = 1, note: str = "") -> list:
    """批量生成兑换码（内测期手动发给学生用）。"""
    _ensure()
    plan = plan_of(plan_code)
    if not plan:
        raise BillingError("套餐不存在")
    codes = []
    conn = sqlite3.connect(db_path(), timeout=3.0)
    try:
        for _ in range(max(1, min(count, 200))):
            code = "EB-" + secrets.token_hex(3).upper() + "-" + secrets.token_hex(2).upper()
            conn.execute(
                "INSERT INTO licenses (code, plan_code, days, note, created_at) VALUES (?, ?, ?, ?, ?)",
                (code, plan_code, plan["days"], note, _now()),
            )
            codes.append(code)
        conn.commit()
    finally:
        conn.close()
    return codes


def redeem(uid: str, code: str) -> dict:
    """兑换码开通。"""
    _ensure()
    code = (code or "").strip().upper()
    if not uid:
        raise BillingError("请先登录")
    conn = sqlite3.connect(db_path(), timeout=3.0)
    try:
        row = conn.execute(
            "SELECT plan_code, days, used_by FROM licenses WHERE code = ?", (code,)
        ).fetchone()
        if not row:
            raise BillingError("兑换码无效")
        plan_code, days, used_by = row
        if used_by:
            raise BillingError("这个兑换码已经用过了")
        conn.execute(
            "UPDATE licenses SET used_by = ?, used_at = ? WHERE code = ?", (uid, _now(), code)
        )
        conn.commit()
    finally:
        conn.close()
    account.set_tier(uid, plan_code, days)
    return {"ok": True, "plan": plan_code, "days": days}


def entitlement(uid: str) -> dict:
    """当前权益。过期自动降级为 free。"""
    if not uid:
        return {"tier": "free", "until": "", "days_left": 0}
    _ensure()
    conn = sqlite3.connect(db_path(), timeout=3.0)
    try:
        row = conn.execute(
            "SELECT tier, tier_until FROM users WHERE uid = ?", (uid,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return {"tier": "free", "until": "", "days_left": 0}
    tier, until = (row[0] or "free"), (row[1] or "")
    days_left = 0
    if until:
        try:
            left = datetime.strptime(until, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_CST) - datetime.now(_CST)
            days_left = max(0, left.days)
        except ValueError:
            pass
        if until < _now():
            tier, until, days_left = "free", "", 0
    return {"tier": tier, "until": until, "days_left": days_left}
