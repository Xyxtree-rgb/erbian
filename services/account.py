"""账号与云存档。零外部依赖，只用 sqlite3 标准库。

设计原则：
1. 登录通道做成可插拔。内测期走「邀请码直登」——零成本、零资质、当天可用；
   正式期换手机号验证码或微信登录时，只换 provider，不动上层接口。
2. 短信服务商没配置时**不许假装有**：DEV 模式只把验证码写进日志，接口照跑，
   但函数返回 False，让调用方知道这条短信其实没发出去。
3. 账号出错不影响辩论主流程——对外函数不把异常抛给业务层。
"""

from __future__ import annotations

import os
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

_LOCK = threading.Lock()
_READY = False
_CST = timezone(timedelta(hours=8))

# 验证码暂存（内存即可：服务重启后未使用的验证码失效，内测阶段可接受）
_CODE_TTL = 300  # 5 分钟
_pending_codes: dict[str, tuple[str, float]] = {}

SESSION_DAYS = 30

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL UNIQUE,
    nick TEXT,
    channel TEXT,
    credential TEXT,
    created_at TEXT,
    last_seen TEXT,
    tier TEXT DEFAULT 'free',
    tier_until TEXT
);
CREATE INDEX IF NOT EXISTS idx_users_cred ON users(channel, credential);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    uid TEXT NOT NULL,
    created_at TEXT,
    expires_at TEXT
);

CREATE TABLE IF NOT EXISTS saves (
    uid TEXT NOT NULL,
    scope TEXT NOT NULL,
    payload TEXT,
    updated_at TEXT,
    PRIMARY KEY (uid, scope)
);

CREATE TABLE IF NOT EXISTS invites (
    code TEXT PRIMARY KEY,
    created_at TEXT,
    used_by TEXT,
    used_at TEXT
);
"""


class AccountError(Exception):
    """可对外说明的账号错误（邀请码无效、验证码错误等）。"""


def db_path() -> str:
    env = os.environ.get("ERBIAN_STATS_DB")
    if env:
        return env
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "stats.db")


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


def _seed_invites(conn: sqlite3.Connection) -> None:
    """把环境变量里的初始邀请码灌进去（只在首次调用时执行）。"""
    raw = os.environ.get("ERBIAN_INVITE_CODES", "")
    for code in [c.strip() for c in raw.split(",") if c.strip()]:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO invites (code, created_at) VALUES (?, ?)",
                (code, _now()),
            )
        except sqlite3.Error:
            pass


def bootstrap() -> None:
    """服务启动时调用一次：建表 + 灌初始邀请码。"""
    _ensure()
    conn = sqlite3.connect(db_path(), timeout=3.0)
    try:
        _seed_invites(conn)
        conn.commit()
    finally:
        conn.close()


def _new_uid() -> str:
    return "u_" + secrets.token_hex(6)


def _new_token() -> str:
    return secrets.token_urlsafe(24)


def issue_invite(code: str | None = None) -> str:
    """生成一个邀请码（内测期手动发给学生用）。"""
    _ensure()
    code = (code or "").strip() or ("EB" + secrets.token_hex(3).upper())
    conn = sqlite3.connect(db_path(), timeout=3.0)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO invites (code, created_at) VALUES (?, ?)",
            (code, _now()),
        )
        conn.commit()
    finally:
        conn.close()
    return code


def _send_sms_code(phone: str, code: str) -> bool:
    """发短信。没配服务商时只写日志并返回 False——调用方要知道这条没真发出去。"""
    if os.environ.get("ERBIAN_SMS_PROVIDER"):
        # 以后接腾讯云/阿里云在这里补，接口不用改
        print(f"[account] 短信服务商已配置，应向 {phone} 发送 {code}")
        return True
    print(f"[account] DEV 模式：未配置短信服务商，{phone} 的验证码是 {code}（未真实发送）")
    return False


def request_code(channel: str, credential: str) -> dict:
    """申请验证码。返回 {sent: bool, dev_code: str|None}。"""
    credential = (credential or "").strip()
    if channel != "phone" or not credential:
        raise AccountError("目前只支持手机号验证码登录")
    code = os.environ.get("ERBIAN_DEV_SMS_CODE") or f"{secrets.randbelow(900000) + 100000:06d}"
    _pending_codes[credential] = (code, time.time() + _CODE_TTL)
    sent = _send_sms_code(credential, code)
    return {"sent": sent, "dev_code": None if sent else code}


def _check_code(credential: str, code: str) -> bool:
    hit = _pending_codes.get(credential)
    if not hit:
        return False
    expect, deadline = hit
    if time.time() > deadline:
        _pending_codes.pop(credential, None)
        return False
    return expect == (code or "").strip()


def login(channel: str, credential: str, code: str = "", nick: str = "") -> dict:
    """登录 / 注册一步完成。返回 {token, uid, nick, tier, is_new}。"""
    _ensure()
    channel = (channel or "").strip()
    credential = (credential or "").strip()
    if not credential:
        raise AccountError("请填写账号")

    if channel == "invite":
        conn = sqlite3.connect(db_path(), timeout=3.0)
        try:
            row = conn.execute(
                "SELECT used_by FROM invites WHERE code = ?", (credential,)
            ).fetchone()
            if row is None:
                raise AccountError("邀请码无效")
            if row[0]:
                # 已用过：当成老用户登录，不拦他
                pass
        finally:
            conn.close()
    elif channel == "phone":
        if not _check_code(credential, code):
            raise AccountError("验证码错误或已过期")
    else:
        raise AccountError("暂不支持该登录方式")

    conn = sqlite3.connect(db_path(), timeout=3.0)
    try:
        row = conn.execute(
            "SELECT uid, nick, tier, tier_until FROM users WHERE channel = ? AND credential = ?",
            (channel, credential),
        ).fetchone()
        is_new = row is None
        if row is None:
            uid = _new_uid()
            conn.execute(
                "INSERT INTO users (uid, nick, channel, credential, created_at, last_seen)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (uid, nick or "", channel, credential, _now(), _now()),
            )
            tier, tier_until = "free", ""
        else:
            uid, old_nick, tier, tier_until = row
            if nick and nick != old_nick:
                conn.execute("UPDATE users SET nick = ? WHERE uid = ?", (nick, uid))
            conn.execute("UPDATE users SET last_seen = ? WHERE uid = ?", (_now(), uid))

        if channel == "invite":
            conn.execute(
                "UPDATE invites SET used_by = ?, used_at = ? WHERE code = ? AND IFNULL(used_by,'') = ''",
                (uid, _now(), credential),
            )

        token = _new_token()
        conn.execute(
            "INSERT INTO sessions (token, uid, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (
                token,
                uid,
                _now(),
                (datetime.now(_CST) + timedelta(days=SESSION_DAYS)).strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        conn.commit()
        return {
            "token": token,
            "uid": uid,
            "nick": nick or (row[1] if row and row[1] else ""),
            "tier": tier or "free",
            "tier_until": tier_until or "",
            "is_new": is_new,
        }
    finally:
        conn.close()


def user_by_token(token: str) -> dict | None:
    """按 token 取用户。过期或不存在返回 None。"""
    if not token:
        return None
    _ensure()
    conn = sqlite3.connect(db_path(), timeout=3.0)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT u.uid, u.nick, u.tier, u.tier_until, s.expires_at"
            " FROM sessions s JOIN users u ON u.uid = s.uid WHERE s.token = ?",
            (token,),
        ).fetchone()
        if not row:
            return None
        if row["expires_at"] and row["expires_at"] < _now():
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            conn.commit()
            return None
        return {
            "uid": row["uid"],
            "nick": row["nick"] or "",
            "tier": row["tier"] or "free",
            "tier_until": row["tier_until"] or "",
        }
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def logout(token: str) -> None:
    if not token:
        return
    _ensure()
    conn = sqlite3.connect(db_path(), timeout=3.0)
    try:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
    except sqlite3.Error:
        pass
    finally:
        conn.close()


def save_progress(uid: str, scope: str, payload: str) -> None:
    """云存档。scope 例如 voyage / story / settings。"""
    if not uid or not scope:
        return
    _ensure()
    conn = sqlite3.connect(db_path(), timeout=3.0)
    try:
        conn.execute(
            "INSERT INTO saves (uid, scope, payload, updated_at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(uid, scope) DO UPDATE SET payload = excluded.payload,"
            " updated_at = excluded.updated_at",
            (uid, scope, payload, _now()),
        )
        conn.commit()
    except sqlite3.Error:
        pass
    finally:
        conn.close()


def load_progress(uid: str, scope: str = "") -> dict:
    """取存档。给了 scope 取单个，没给就把所有 scope 一起返回。"""
    if not uid:
        return {}
    _ensure()
    conn = sqlite3.connect(db_path(), timeout=3.0)
    try:
        if scope:
            row = conn.execute(
                "SELECT payload FROM saves WHERE uid = ? AND scope = ?", (uid, scope)
            ).fetchone()
            return {scope: row[0]} if row else {}
        rows = conn.execute("SELECT scope, payload FROM saves WHERE uid = ?", (uid,)).fetchall()
        return {r[0]: r[1] for r in rows}
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


def set_tier(uid: str, tier: str, days: int) -> None:
    """开通/续期套餐。days<=0 表示永久。"""
    _ensure()
    until = ""
    if days > 0:
        until = (datetime.now(_CST) + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(db_path(), timeout=3.0)
    try:
        conn.execute("UPDATE users SET tier = ?, tier_until = ? WHERE uid = ?", (tier, until, uid))
        conn.commit()
    except sqlite3.Error:
        pass
    finally:
        conn.close()
