"""极轻量的行为统计（只用 sqlite3 标准库，零外部依赖）。

设计原则：
1. 统计失败绝不能影响辩论主流程 —— 所有写入异常一律吞掉。
2. 单条写入 <2ms，对本项目 0.08s 级的响应无感。
3. 库文件默认在项目根目录 stats.db，可用环境变量 ERBIAN_STATS_DB 覆盖。
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

_LOCK = threading.Lock()
_READY = False
_CST = timezone(timedelta(hours=8))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    sid TEXT,
    mode TEXT,
    question TEXT,
    user_text TEXT,
    ai_text TEXT,
    ai_name TEXT,
    ai_prompt TEXT,
    valid INTEGER,
    dtype TEXT,
    damage INTEGER,
    reason TEXT,
    ip TEXT,
    ua TEXT,
    elapsed_ms INTEGER
);
CREATE INDEX IF NOT EXISTS idx_turns_ts ON turns(ts);

CREATE TABLE IF NOT EXISTS beats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    sid TEXT,
    event TEXT NOT NULL,
    detail TEXT,
    ip TEXT,
    ua TEXT
);
CREATE INDEX IF NOT EXISTS idx_beats_ts ON beats(ts);
"""

# 老库补列：只加不删，失败就算了（统计不能影响主流程）
_MIGRATIONS = {
    "turns": ("ai_name", "ai_prompt"),
}


def db_path() -> str:
    """统计库位置。默认项目根目录，避免写进只读的资源目录。"""
    env = os.environ.get("ERBIAN_STATS_DB")
    if env:
        return env
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "stats.db")


def _now() -> str:
    return datetime.now(_CST).strftime("%Y-%m-%d %H:%M:%S")


def _migrate(conn: sqlite3.Connection) -> None:
    """给已存在的库补上后来新增的列。"""
    for table, columns in _MIGRATIONS.items():
        try:
            have = {row[1] for row in conn.execute("PRAGMA table_info(%s)" % table)}
        except sqlite3.Error:
            continue
        if not have:
            continue
        for col in columns:
            if col not in have:
                try:
                    conn.execute("ALTER TABLE %s ADD COLUMN %s TEXT" % (table, col))
                except sqlite3.Error:
                    pass


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
            _migrate(conn)
            conn.commit()
        finally:
            conn.close()
        _READY = True


def _insert(sql: str, params: tuple) -> bool:
    try:
        _ensure()
        conn = sqlite3.connect(db_path(), timeout=3.0)
        try:
            with conn:
                conn.execute(sql, params)
        finally:
            conn.close()
        return True
    except Exception:  # noqa: BLE001 - 统计永不影响主流程
        return False


def _query(sql: str, params: tuple = ()) -> list:
    try:
        _ensure()
        conn = sqlite3.connect(db_path(), timeout=3.0)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return []


def _clip(value, limit: int):
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    return value[:limit]


def log_turn(
    mode: str,
    user_text: str = "",
    ai_text: str = "",
    ai_name: str = "",
    ai_prompt: str = "",
    question: str = "",
    valid=None,
    dtype: str = "",
    damage=None,
    reason: str = "",
    ip: str = "",
    ua: str = "",
    sid: str = "",
    elapsed_ms=None,
) -> bool:
    """记录一次真实辩论回合。

    ai_text   —— 对手对玩家这一句的回应（story 里的 reply / solo 里桃弁自述）
    ai_prompt —— 对手在玩家发言**之前**说的那句，也就是玩家要反驳的靶子
    """
    return _insert(
        "INSERT INTO turns (ts, sid, mode, question, user_text, ai_text, ai_name, ai_prompt,"
        " valid, dtype, damage, reason, ip, ua, elapsed_ms)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            _now(),
            _clip(sid, 40),
            _clip(mode, 20),
            _clip(question, 300),
            _clip(user_text, 600),
            _clip(ai_text, 600),
            _clip(ai_name, 40),
            _clip(ai_prompt, 600),
            1 if valid else 0 if valid is not None else None,
            _clip(dtype, 20),
            damage if isinstance(damage, int) else None,
            _clip(reason, 400),
            _clip(ip, 60),
            _clip(ua, 200),
            elapsed_ms if isinstance(elapsed_ms, int) else None,
        ),
    )


def log_beat(event: str, detail: str = "", ip: str = "", ua: str = "", sid: str = "") -> bool:
    """记录一个前端关键节点，用于还原「一个人走到哪一步」。"""
    if not event:
        return False
    return _insert(
        "INSERT INTO beats (ts, sid, event, detail, ip, ua) VALUES (?, ?, ?, ?, ?, ?)",
        (_now(), _clip(sid, 40), _clip(event, 40), _clip(detail, 300), _clip(ip, 60), _clip(ua, 200)),
    )


def recent_turns(limit: int = 50, kw: str = "") -> list:
    limit = max(1, min(int(limit or 50), 500))
    kw = (kw or "").strip()[:40]
    if kw:
        like = "%" + kw + "%"
        return _query(
            "SELECT * FROM turns WHERE user_text LIKE ? OR ai_text LIKE ?"
            " OR ai_prompt LIKE ? OR question LIKE ? OR reason LIKE ?"
            " ORDER BY id DESC LIMIT ?",
            (like, like, like, like, like, limit),
        )
    return _query("SELECT * FROM turns ORDER BY id DESC LIMIT ?", (limit,))


def recent_beats(limit: int = 120) -> list:
    limit = max(1, min(int(limit or 120), 1000))
    return _query("SELECT * FROM beats ORDER BY id DESC LIMIT ?", (limit,))


def summary() -> dict:
    """给后台页用的汇总数据。

    口径：solo（交锋）那条记录是「桃弁自己说的话 + 对桃弁的判定」，
    玩家并没有开口，所以所有「玩家发言」相关的数字都把它排除，
    否则会把 AI 的判定结果算成玩家的表现。
    """
    today = datetime.now(_CST).strftime("%Y-%m-%d")
    player = "(mode IS NULL OR mode <> 'solo')"

    totals = _query(
        "SELECT COUNT(*) AS n,"
        " SUM(CASE WHEN valid = 1 THEN 1 ELSE 0 END) AS ok,"
        " SUM(CASE WHEN valid = 0 THEN 1 ELSE 0 END) AS bad,"
        " COUNT(DISTINCT CASE WHEN sid IS NOT NULL AND sid <> '' THEN sid END) AS sids,"
        " COUNT(DISTINCT ip) AS ips"
        " FROM turns WHERE " + player
    )
    total = totals[0] if totals else {}

    ai_row = _query("SELECT COUNT(*) AS n FROM turns WHERE mode = 'solo'")
    ai_turns = ai_row[0]["n"] if ai_row else 0

    today_row = _query(
        "SELECT COUNT(*) AS n FROM turns WHERE ts LIKE ? AND " + player, (today + "%",)
    )
    today_turns = today_row[0]["n"] if today_row else 0

    funnel = {
        row["event"]: row["n"]
        for row in _query(
            "SELECT event, COUNT(*) AS n FROM beats GROUP BY event ORDER BY n DESC"
        )
    }

    # 「进入辩论」这一步：交锋走 debate_open，断案走 story_enter，
    # 按会话去重合并，避免一个人两个都点导致虚高，也避免断案玩家被算成 0。
    enter = _query(
        "SELECT COUNT(DISTINCT sid) AS n FROM beats"
        " WHERE event IN ('debate_open','story_enter') AND sid IS NOT NULL AND sid <> ''"
    )
    enter_n = (enter[0]["n"] if enter else 0) or max(
        funnel.get("debate_open", 0), funnel.get("story_enter", 0)
    )
    funnel["debate_enter"] = enter_n

    modes = {
        row["mode"] or "未知": row["n"]
        for row in _query(
            "SELECT mode, COUNT(*) AS n FROM turns GROUP BY mode ORDER BY n DESC"
        )
    }

    # 最近 7 天补齐空缺日期（升序），前端柱状图才能稳定画出 7 根柱子
    rows = _query(
        "SELECT substr(ts, 1, 10) AS day, COUNT(*) AS n FROM turns"
        " WHERE " + player + " GROUP BY day ORDER BY day DESC LIMIT 30"
    )
    dmap = {row["day"]: row["n"] for row in rows}
    now_cst = datetime.now(_CST)
    daily = []
    for i in range(6, -1, -1):
        day = (now_cst - timedelta(days=i)).strftime("%Y-%m-%d")
        daily.append({"day": day, "n": dmap.get(day, 0)})

    speed = _query(
        "SELECT AVG(elapsed_ms) AS ms FROM turns WHERE elapsed_ms IS NOT NULL AND " + player
    )
    avg_ms = (speed[0]["ms"] if speed else None) or 0

    return {
        "total_turns": total.get("n") or 0,
        "ai_turns": ai_turns,
        "valid": total.get("ok") or 0,
        "invalid": total.get("bad") or 0,
        "sessions": total.get("sids") or 0,
        "ips": total.get("ips") or 0,
        "today_turns": today_turns,
        "avg_ms": int(avg_ms),
        "funnel": funnel,
        "modes": modes,
        "daily": daily,
        "db": db_path(),
    }


def stats_payload(limit: int = 60, kw: str = "") -> dict:
    data = summary()
    data["turns"] = recent_turns(limit, kw)
    data["beats"] = recent_beats(120)
    data["limit"] = max(1, min(int(limit or 60), 500))
    data["kw"] = (kw or "").strip()[:40]
    return data


def live_payload() -> dict:
    """轮询用的精简快照：只含图表需要的聚合值，不拉长文本。"""
    data = summary()
    data.pop("db", None)
    return data


def client_meta(req) -> tuple:
    """从 Flask request 提取 ip / ua / sid。"""
    ip = ""
    try:
        forwarded = req.headers.get("X-Forwarded-For", "")
        if forwarded:
            ip = forwarded.split(",")[0].strip()
        else:
            ip = req.remote_addr or ""
    except Exception:  # noqa: BLE001
        ip = ""
    ua = ""
    try:
        ua = req.headers.get("User-Agent", "") or ""
    except Exception:  # noqa: BLE001
        ua = ""
    return ip, ua
