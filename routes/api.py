import asyncio
import gzip
import json
import logging
import os
import re
import time
from io import BytesIO

import aiohttp
import edge_tts
import requests
from edge_tts.exceptions import EdgeTTSException
from flask import Blueprint, Response, current_app, jsonify, request, send_file, send_from_directory
from openai import OpenAIError

from config import resource_path
from services import stats
from services import duel
from services.deepseek import (
    DeepSeekConfigurationError,
    DeepSeekResponseError,
    debate_argument,
    judge_argument,
    solo_opponent_turn,
)
from services.policy import clear_policy_cache, get_policy_knowledge


api = Blueprint("api", __name__)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 文本响应压缩
# 页面是一个 330KB 的单文件 HTML，不压缩等于每次开场白都要多传 260KB。
# 按 mtime 缓存压缩结果，改完文件自动失效；只留最近几份，不吃内存。
# ---------------------------------------------------------------------------
_GZIP_CACHE: dict = {}


def _send_gzip_file(path, mimetype):
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return jsonify({"error": "文件不存在"}), 404
    key = (path, mtime)
    cached = _GZIP_CACHE.get(key)
    if cached is None:
        with open(path, "rb") as fh:
            raw = fh.read()
        cached = (raw, gzip.compress(raw, 6))
        if len(_GZIP_CACHE) > 6:
            _GZIP_CACHE.clear()
        _GZIP_CACHE[key] = cached
    raw, packed = cached
    accepts = "gzip" in (request.headers.get("Accept-Encoding") or "")
    body = packed if accepts else raw
    resp = Response(body, mimetype=mimetype)
    if accepts:
        resp.headers["Content-Encoding"] = "gzip"
        resp.headers["Vary"] = "Accept-Encoding"
    resp.headers["Content-Length"] = str(len(body))
    return resp


@api.after_request
def _compress_json(resp):
    if resp.direct_passthrough or resp.status_code != 200:
        return resp
    if resp.mimetype != "application/json" or resp.headers.get("Content-Encoding"):
        return resp
    if "gzip" not in (request.headers.get("Accept-Encoding") or ""):
        return resp
    data = resp.get_data()
    if len(data) < 1024:
        return resp
    packed = gzip.compress(data, 5)
    resp.set_data(packed)
    resp.headers["Content-Encoding"] = "gzip"
    resp.headers["Content-Length"] = str(len(packed))
    resp.headers["Vary"] = "Accept-Encoding"
    return resp


# 语速白名单：形如 "+12%" / "-5%"。超出 ±100% 或格式不对的一律拒绝。
_RATE_PATTERN = re.compile(r"^[+-]\d{1,3}%$")
DEFAULT_TTS_RATE = "+10%"
SAFE_TTS_RATE = "+0%"


def _normalize_rate(value):
    """校验请求体里的语速。缺省用 +10%，格式非法回退到 +0%。"""
    if value is None:
        return DEFAULT_TTS_RATE
    if not isinstance(value, str):
        return SAFE_TTS_RATE
    text = value.strip()
    if not _RATE_PATTERN.match(text):
        return SAFE_TTS_RATE
    try:
        percent = int(text[1:-1])
    except ValueError:
        return SAFE_TTS_RATE
    if percent > 100:
        return SAFE_TTS_RATE
    return text


async def _synthesize_speech(text, voice, rate=DEFAULT_TTS_RATE):
    communicator = edge_tts.Communicate(text, voice, rate=rate)
    audio_data = bytearray()
    async for chunk in communicator.stream():
        if chunk["type"] == "audio":
            audio_data.extend(chunk["data"])
    return bytes(audio_data)


with open(resource_path("knowledge_base.json"), "r", encoding="utf-8") as knowledge_file:
    KB = json.load(knowledge_file)


@api.route("/")
@api.route("/game")
@api.route("/index")
@api.route("/index.html")
@api.route("/index/")
def index():
    return _send_gzip_file(resource_path("index.html"), "text/html; charset=utf-8")


@api.route("/story.json")
@api.route("/story_ch2.json")
@api.route("/story_ch3.json")
@api.route("/story_team.json")
def story():
    """剧情数据：story.json（第一章）、story_ch2.json（第二章）、story_ch3.json（第三章）、story_team.json（舌战群儒前置剧情）"""
    name = request.path.lstrip("/")
    return send_file(resource_path(name), mimetype="application/json")


@api.route("/voyage/<path:name>")
def voyage_story(name):
    """渡口分站：/voyage/d_001.json。只允许取 voyage/ 下的 json，防目录穿越。"""
    safe = os.path.basename(name)
    if not safe.endswith(".json"):
        return jsonify({"error": "无效的渡口文件"}), 404
    path = resource_path(os.path.join("voyage", safe))
    if not os.path.isfile(path):
        return jsonify({"error": "未找到该渡口"}), 404
    return send_file(path, mimetype="application/json")


@api.route("/knowledge-base")
@api.route("/knowledge-base/")
@api.route("/knowledge.html")
def knowledge_base():
    return _send_gzip_file(resource_path("knowledge.html"), "text/html; charset=utf-8")


@api.route("/mobile")
@api.route("/mobile/")
@api.route("/mobile.html")
def mobile():
    return _send_gzip_file(resource_path("mobile.html"), "text/html; charset=utf-8")


@api.route("/favicon.ico")
def favicon():
    icon = resource_path("assets/icon/icon-192.png")
    if os.path.exists(icon):
        return send_file(icon)
    return ("", 204)


@api.route("/assets/<path:filename>")
def serve_assets(filename):
    return send_from_directory(resource_path("assets"), filename)


@api.route("/manifest.json")
def serve_manifest():
    return send_file(resource_path("manifest.json"))


@api.route("/sw.js")
def serve_sw():
    return send_file(resource_path("sw.js"))


@api.route("/api/tts", methods=["POST", "OPTIONS"])
def tts():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "请求内容必须是 JSON 对象"}), 400

    text = data.get("text")
    voice = data.get("voice")
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        return jsonify({"error": "语音文本无效或过长"}), 400
    if not isinstance(voice, str) or not voice.strip() or len(voice) > 100:
        return jsonify({"error": "语音角色无效"}), 400

    rate = _normalize_rate(data.get("rate"))

    try:
        audio_data = asyncio.run(_synthesize_speech(text.strip(), voice.strip(), rate))
    except (EdgeTTSException, asyncio.TimeoutError, aiohttp.ClientError):
        current_app.logger.exception("Edge TTS 音频生成失败")
        return jsonify({"error": "语音服务暂时不可用，请稍后重试"}), 502
    except ValueError:
        current_app.logger.exception("Edge TTS 请求参数无效")
        return jsonify({"error": "语音角色或请求参数无效"}), 400

    if not audio_data:
        current_app.logger.error("Edge TTS 未返回音频数据")
        return jsonify({"error": "语音服务未生成音频"}), 502

    return send_file(BytesIO(audio_data), mimetype="audio/mpeg")


@api.route("/api/knowledge", methods=["GET"])
def get_knowledge():
    source = request.args.get("source", "cases")
    if source == "policies":
        try:
            return jsonify(get_policy_knowledge())
        except requests.RequestException:
            current_app.logger.exception("国务院政策库请求失败")
            return jsonify({"error": "政策库暂时不可用，请稍后重试"}), 502
        except (KeyError, TypeError, ValueError):
            current_app.logger.exception("解析国务院政策库响应失败")
            return jsonify({"error": "政策库响应格式异常"}), 502

    if source != "cases":
        return jsonify({"error": "source 参数必须是 cases 或 policies"}), 400

    category = request.args.get("category")
    keyword = request.args.get("q")
    results = KB["cases"]
    if category:
        results = [case for case in results if case["category"] == category]
    if keyword:
        results = [
            case
            for case in results
            if keyword in case["title"]
            or keyword in case["summary"]
            or keyword in case["law"]
        ]

    return jsonify(
        {
            "total": len(results),
            "cases": results,
            "updated": KB.get("updated", ""),
            "version": KB.get("version", ""),
        }
    )


@api.route("/api/knowledge/categories", methods=["GET"])
def get_categories():
    categories = {}
    for case in KB["cases"]:
        category = case["category"]
        categories[category] = categories.get(category, 0) + 1
    return jsonify(categories)


@api.route("/api/knowledge/refresh", methods=["POST"])
def api_knowledge_refresh():
    clear_policy_cache()
    return jsonify({"status": "ok", "message": "知识库将在下次请求时刷新"})


@api.route("/api/judge", methods=["POST", "OPTIONS"])
def api_judge():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}

    if "chapter_id" in data:
        return _judge_story_argument(data)

    user_input = data.get("input")
    case_law = data.get("case_law", "")
    if not isinstance(user_input, str) or not isinstance(case_law, str):
        return jsonify(
            {
                "valid": False,
                "type": "无效",
                "damage": 0,
                "reason": "发言内容或案例法条格式无效",
            }
        ), 400

    user_input = user_input.strip()
    case_law = case_law.strip()
    if not user_input:
        return jsonify(
            {
                "valid": False,
                "type": "无效",
                "damage": 0,
                "reason": "发言不能为空",
            }
        ), 400

    if len(user_input) > 300:
        return jsonify(
            {
                "valid": False,
                "type": "无效",
                "damage": 0,
                "reason": "发言过长，请控制在300字以内",
            }
        ), 400

    _t0 = time.perf_counter()
    try:
        result = judge_argument(user_input, case_law)
    except DeepSeekConfigurationError:
        return jsonify(
            {
                "valid": False,
                "type": "无效",
                "damage": 0,
                "reason": "未配置 DEEPSEEK_API_KEY",
            }
        ), 503
    except OpenAIError:
        logger.exception("调用 DeepSeek AI 裁判失败")
        return jsonify(
            {
                "valid": False,
                "type": "无效",
                "damage": 0,
                "reason": "AI裁判服务暂时不可用，请稍后重试",
            }
        ), 502

    _record_turn(
        "judge",
        result=result,
        user_text=user_input,
        question=case_law,
        data=data,
        elapsed_ms=int((time.perf_counter() - _t0) * 1000),
    )
    return jsonify(result)


_STORY_CACHE: dict[str, dict] = {}
# 渡口的每一站也走同一套判定逻辑，只是 chapter_id 换成 d_00X
STORY_FILES = (
    "story.json",
    "story_ch2.json",
    "story_ch3.json",
    "voyage/d_001.json",
    "voyage/d_002.json",
    "voyage/d_003.json",
)


def _load_story_by_chapter(chapter_id):
    """按 chapter_id 找章节。以前写死只读 story.json，加第二章后改成逐个文件查并缓存。"""
    if chapter_id in _STORY_CACHE:
        return _STORY_CACHE[chapter_id]
    for name in STORY_FILES:
        try:
            with open(resource_path(name), "r", encoding="utf-8") as story_file:
                data = json.load(story_file)
        except (OSError, json.JSONDecodeError):
            logger.exception("读取剧情文件 %s 失败", name)
            continue
        if isinstance(data, dict) and isinstance(data.get("chapter_id"), str):
            _STORY_CACHE[data["chapter_id"]] = data
    return _STORY_CACHE.get(chapter_id)


def _judge_story_argument(data):
    chapter_id = data.get("chapter_id")
    user_input = data.get("input")
    round_index = data.get("round_index", 0)
    evidence_ids = data.get("evidence", [])
    external_evidence = data.get("external_evidence", [])
    current_hp = data.get("current_hp")

    if not isinstance(chapter_id, str) or not chapter_id:
        return jsonify({"error": "章节 ID 无效"}), 400
    if not isinstance(user_input, str):
        return jsonify({"error": "发言内容格式无效"}), 400
    user_input = user_input.strip()
    if not user_input:
        return jsonify({"error": "发言不能为空"}), 400
    if len(user_input) > 300:
        return jsonify({"error": "发言过长，请控制在300字以内"}), 400
    if isinstance(round_index, bool) or not isinstance(round_index, int) or round_index < 0:
        return jsonify({"error": "回合序号无效"}), 400
    if not isinstance(evidence_ids, list) or any(not isinstance(item, str) for item in evidence_ids):
        return jsonify({"error": "证据列表格式无效"}), 400
    if not isinstance(external_evidence, list) or len(external_evidence) > 8:
        return jsonify({"error": "知识库证据格式无效"}), 400
    for item in external_evidence:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("name"), str)
            or not isinstance(item.get("description"), str)
            or not item["name"].strip()
            or len(item["name"]) > 200
            or len(item["description"]) > 1000
        ):
            return jsonify({"error": "知识库证据内容无效"}), 400

    try:
        story = _load_story_by_chapter(chapter_id)
    except Exception:
        logger.exception("读取剧情配置失败 chapter_id=%s", chapter_id)
        return jsonify({"error": "剧情配置暂时不可用"}), 500

    if not story:
        return jsonify({"error": "未找到对应章节"}), 404
    opponent = story.get("opponent")
    evidence = story.get("evidence")
    if (
        not isinstance(opponent, dict)
        or not isinstance(opponent.get("rounds"), list)
        or not opponent["rounds"]
        or not isinstance(evidence, list)
        or isinstance(opponent.get("hp"), bool)
        or not isinstance(opponent.get("hp"), int)
        or opponent["hp"] <= 0
    ):
        logger.error("剧情章节 %s 的对手/证据配置不完整", chapter_id)
        return jsonify({"error": "章节对手配置不完整"}), 500

    rounds = opponent["rounds"]
    if round_index >= len(rounds):
        return jsonify({"error": "当前辩论回合不存在"}), 400
    if current_hp is None:
        current_hp = opponent["hp"]
    if isinstance(current_hp, bool) or not isinstance(current_hp, int):
        return jsonify({"error": "对手血量格式无效"}), 400
    if not 0 <= current_hp <= opponent["hp"]:
        return jsonify({"error": "对手血量超出有效范围"}), 400
    if current_hp == 0:
        return jsonify({"error": "对手已被击败"}), 409

    known_evidence = {
        item.get("id"): item
        for item in evidence
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    if any(evidence_id not in known_evidence for evidence_id in evidence_ids):
        return jsonify({"error": "提交了当前章节不存在的证据"}), 400
    selected_evidence = [
        {
            "name": known_evidence[evidence_id].get("name", ""),
            "description": known_evidence[evidence_id].get("description", ""),
        }
        for evidence_id in dict.fromkeys(evidence_ids)
    ]
    selected_evidence.extend(
        {
            "name": item["name"].strip(),
            "description": item["description"].strip(),
        }
        for item in external_evidence
    )
    current_round = rounds[round_index]
    if (
        not isinstance(current_round, dict)
        or not isinstance(current_round.get("opponent_line"), str)
        or not isinstance(opponent.get("name"), str)
    ):
        logger.error("剧情章节 %s 的第 %s 回合配置不完整", chapter_id, round_index)
        return jsonify({"error": "当前辩论回合配置不完整"}), 500

    try:
        _t0 = time.perf_counter()
        result = debate_argument(
            chapter_title=story.get("title", ""),
            opponent_name=opponent["name"],
            opponent_line=current_round["opponent_line"],
            user_input=user_input,
            evidence=selected_evidence,
            current_hp=current_hp,
            max_hp=opponent["hp"],
            # 玩家就是本章当事人，对手要当面叫他「你」，不能写成第三人称
            player_name=story.get("player_name", ""),
            # 对手声口：hr / boss / manager，决定他怎么说话
            persona=opponent.get("persona", ""),
        )
    except DeepSeekConfigurationError:
        return jsonify({"error": "未配置 DEEPSEEK_API_KEY"}), 503
    except DeepSeekResponseError:
        logger.exception("DeepSeek 返回的辩论结果格式无效")
        return jsonify({"error": "AI 对手返回了无效结果，请重试"}), 502
    except OpenAIError:
        logger.exception("调用 DeepSeek AI 对手失败")
        return jsonify({"error": "AI 对手服务暂时不可用，请稍后重试"}), 502

    _record_turn(
        "story",
        result=result,
        user_text=user_input,
        # 真正的「对手回应」是 result["reply"]；opponent_line 是玩家开口前就摆在那儿的靶子
        ai_text=result.get("reply", "") if isinstance(result, dict) else "",
        ai_name=opponent["name"],
        ai_prompt=current_round["opponent_line"],
        question=story.get("title", "") or chapter_id,
        data=data,
        elapsed_ms=int((time.perf_counter() - _t0) * 1000),
    )

    damage = min(result["damage"], current_hp)
    return jsonify(
        {
            **result,
            "damage": damage,
            "opponent_hp": current_hp - damage,
            "max_hp": opponent["hp"],
        }
    )


@api.route("/api/solo-turn", methods=["POST", "OPTIONS"])
def api_solo_turn():
    """单人模式：AI 对手（桃弁）发言，并由 AI 裁判真实判定。"""
    if request.method == "OPTIONS":
        return jsonify({}), 200

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}

    question = data.get("question")
    law = data.get("law", "")
    category = data.get("category", "")
    stance = data.get("stance", "正方")
    reference = data.get("reference", "")

    if not isinstance(question, str) or not question.strip():
        return jsonify({"error": "辩题不能为空"}), 400
    if not isinstance(law, str) or not isinstance(category, str) or not isinstance(reference, str):
        return jsonify({"error": "辩题信息格式无效"}), 400
    if stance not in ("正方", "反方"):
        stance = "正方"

    try:
        _t0 = time.perf_counter()
        result = solo_opponent_turn(
            question=question.strip()[:200],
            law=law.strip()[:200],
            category=category.strip()[:50],
            stance=stance,
            reference=reference.strip()[:200],
        )
    except DeepSeekConfigurationError:
        return jsonify({"error": "未配置 DEEPSEEK_API_KEY"}), 503
    except DeepSeekResponseError:
        logger.exception("DeepSeek 生成的 AI 对手发言无效")
        return jsonify({"error": "AI 对手返回了无效结果，请重试"}), 502
    except OpenAIError:
        logger.exception("调用 DeepSeek 生成 AI 对手发言失败")
        return jsonify({"error": "AI 对手服务暂时不可用，请稍后重试"}), 502

    _record_turn(
        "solo",
        result=result,
        ai_text=result.get("line", "") if isinstance(result, dict) else "",
        ai_name="桃弁",
        question=question,
        data=data,
        elapsed_ms=int((time.perf_counter() - _t0) * 1000),
    )

    return jsonify(result)


@api.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "耳辩 AI 裁判"})


# ============================================================
#   行为埋点 + 后台反馈页
# ============================================================
@api.route("/api/beat", methods=["POST", "OPTIONS"])
def api_beat():
    """前端关键节点埋点：进剧情 / 进棋盘 / 开辩论框 / 放弃退出。"""
    if request.method == "OPTIONS":
        return jsonify({}), 200

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}

    event = data.get("event")
    if not isinstance(event, str) or not event.strip():
        return jsonify({"ok": False, "error": "event 不能为空"}), 400

    ip, ua = stats.client_meta(request)
    detail = data.get("detail", "")
    sid = data.get("sid", "")
    stats.log_beat(
        event.strip()[:40],
        detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)[:300],
        ip,
        ua,
        sid if isinstance(sid, str) else "",
    )
    return jsonify({"ok": True})


def _record_turn(
    mode,
    result=None,
    user_text="",
    ai_text="",
    ai_name="",
    ai_prompt="",
    question="",
    data=None,
    elapsed_ms=None,
):
    """把一次真实辩论写进统计库；失败静默，不影响返回。"""
    try:
        ip, ua = stats.client_meta(request)
        sid = ""
        if isinstance(data, dict) and isinstance(data.get("sid"), str):
            sid = data["sid"]
        r = result if isinstance(result, dict) else {}
        stats.log_turn(
            mode=mode,
            user_text=user_text or "",
            ai_text=ai_text or "",
            ai_name=ai_name or "",
            ai_prompt=ai_prompt or "",
            question=question or "",
            valid=r.get("valid"),
            dtype=r.get("type", "") if isinstance(r.get("type"), str) else "",
            damage=r.get("damage") if isinstance(r.get("damage"), int) else None,
            reason=r.get("reason", "") if isinstance(r.get("reason"), str) else "",
            ip=ip,
            ua=ua,
            sid=sid,
            elapsed_ms=elapsed_ms,
        )
    except Exception:  # noqa: BLE001
        pass


def _feedback_token() -> str:
    return os.environ.get("FEEDBACK_TOKEN", "erbian2026")


@api.route("/admin/feedback", methods=["GET"])
def admin_feedback():
    """查看真实用户行为。访问方式：/admin/feedback?k=口令"""
    if request.args.get("k", "") != _feedback_token():
        return Response(
            "<meta charset='utf-8'><div style='font-family:system-ui;padding:60px;text-align:center;color:#444'>"
            "<h2>需要口令</h2><p>请在地址后加上 ?k=你的口令</p></div>",
            status=403,
            mimetype="text/html",
        )

    def _int(v, default):
        try:
            return int(str(v).strip())
        except Exception:  # noqa: BLE001
            return default

    limit = _int(request.args.get("n", "60"), 60)
    limit = max(10, min(limit, 500))
    kw = (request.args.get("q", "") or "").strip()[:40]
    data = stats.stats_payload(limit=limit, kw=kw)
    data["token"] = _feedback_token()
    return Response(_render_feedback(data), mimetype="text/html")


@api.route("/admin/feedback/live", methods=["GET"])
def admin_feedback_live():
    """后台页轮询用的 JSON 快照。只读聚合值，不返回任何长文本。"""
    if request.args.get("k", "") != _feedback_token():
        return jsonify({"error": "forbidden"}), 403
    resp = jsonify(stats.live_payload())
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


def _render_feedback(d: dict) -> str:
    import html as _html

    def esc(v):
        return _html.escape("" if v is None else str(v))

    # 老记录没存对手名，按章节标题回查剧情配置
    opp_by_title = {}
    for _name in ("story.json", "story_ch2.json", "story_ch3.json", "story_team.json"):
        try:
            with open(resource_path(_name), "r", encoding="utf-8") as _f:
                _s = json.load(_f)
            _t = (_s.get("title") or "").strip()
            _n = ((_s.get("opponent") or {}).get("name") or "").strip()
            if _t and _n:
                opp_by_title[_t] = _n
        except (OSError, json.JSONDecodeError, AttributeError):
            continue

    total = d["total_turns"] or 0
    ok = d["valid"]
    bad = d["invalid"]
    rate = round(ok / total * 100) if total else None
    funnel = d["funnel"]
    avg_ms = d.get("avg_ms") or 0
    avg_s = ("%.1f" % (avg_ms / 1000.0)) if avg_ms else "-"

    stages = [
        ("打开页面", "home_open", funnel.get("home_open", 0)),
        ("选了模式", "mode_select", funnel.get("mode_select", 0)),
        ("进入辩论", "debate_enter", funnel.get("debate_enter", 0)),
        ("真的开口辩", "turns", total),
    ]
    top = max([v for _, _, v in stages] + [1])

    rows = []
    for idx, (label, key, val) in enumerate(stages):
        pct = val / top * 100 if top else 0
        conv = ""
        if idx > 0:
            prev = stages[idx - 1][2]
            if not prev:
                conv = "-"
            elif val > prev:
                conv = "—"  # 口径不同或埋点缺失，不硬算百分比
            else:
                conv = "%.0f%%" % (val / prev * 100)
        rows.append(
            '<div class="flow-row" data-stage="%s">'
            '<div class="flow-label">%s</div>'
            '<div class="flow-track"><div class="flow-fill" style="--w:%.0f%%"></div></div>'
            '<div class="flow-num">%s</div>'
            '<div class="flow-conv">%s</div>'
            "</div>" % (key, esc(label), max(2, pct), val, conv)
        )
    flow_html = "".join(rows)

    # ---------- 每日趋势（7 天柱状，缺失日期已补 0） ----------
    daily = d.get("daily") or []
    dmax = max([r["n"] for r in daily] + [1])
    bars = []
    for r in daily:
        h = (r["n"] / dmax) * 100 if dmax else 0
        bars.append(
            '<div class="bar" data-day="%s" style="--h:%.1f%%">'
            '<div class="bb"><span class="bv">%d</span><i class="%s"></i></div>'
            '<em>%s</em></div>'
            % (r["day"], max(1.2, h), r["n"], "z" if r["n"] == 0 else "", esc(r["day"][5:]))
        )
    daily_html = "".join(bars) or '<div class="empty">还没有数据</div>'


    tips = []
    h = funnel.get("home_open", 0)
    m = funnel.get("mode_select", 0)
    dbx = funnel.get("debate_enter", 0)
    if total == 0 and h == 0:
        tips.append(("还没人访问", "把链接发出去之后，这里会开始出现数据。", "warn"))
    else:
        if h >= 3 and m / h < 0.6:
            tips.append((
                "一大半人没选模式就走了",
                "%s 次打开页面，只有 %s 次点了模式卡片。看看首页说明是不是太绕，或者手机上「开始」够不够显眼。" % (h, m),
                "bad",
            ))
        if m >= 3 and dbx / max(1, m) < 0.5:
            tips.append((
                "进了游戏却没开口",
                "%s 次选了模式，只有 %s 次进了辩论。玩家可能到了棋盘，但不知道踩到辩题格才会开始吵。" % (m, dbx),
                "bad",
            ))
        if dbx > 0:
            tips.append((
                "平均每局开口 %.1f 次" % (total / dbx),
                "低于 2 次说明打一回合就走人，3 次以上才算真的在玩。",
                "info",
            ))
        if total >= 3 and rate is not None and rate < 50:
            tips.append((
                "判定偏严：只有 %d%% 有效" % rate,
                "可以考虑放宽裁判标准，或者在输入框提示「要引用法条或讲因果」。",
                "warn",
            ))
        if total >= 3 and rate is not None and rate >= 80:
            tips.append((
                "判定通过率 %d%%" % rate,
                "玩家基本都能把话说明白，可以适当加大难度。",
                "ok",
            ))
    tips_html = "".join(
        '<div class="tip %s"><b>%s</b><span>%s</span></div>' % (cls, esc(t), esc(s))
        for t, s, cls in tips[:3]
    ) or '<div class="tip info"><b>数据还太少</b><span>再积累几局就能看出规律。</span></div>'

    MODE_NAME = {"judge": "自由辩论", "solo": "交锋 · 桃弁", "story": "断案剧情"}
    MODE_COLOR = {
        "judge": "linear-gradient(90deg,#7fb0ee,#a8cbf8)",
        "solo": "linear-gradient(90deg,#a98ee0,#c9a7ff)",
        "story": "linear-gradient(90deg,#f0a7c8,#ffc4dc)",
    }

    # ---------- 模式分布 ----------
    modes = d.get("modes") or {}
    m_total = sum(modes.values()) or 1
    mode_rows = []
    for k, v in sorted(modes.items(), key=lambda kv: -kv[1]):
        mode_rows.append(
            '<div class="mrow" data-mode="%s">'
            '<div class="mtop"><span>%s</span><b>%s<small> %s%%</small></b></div>'
            '<div class="mtrack"><i style="--w:%.0f%%;background:%s"></i></div>'
            "</div>"
            % (
                esc(k),
                esc(MODE_NAME.get(k, k or "未知")),
                v,
                round(v / m_total * 100),
                max(2, v / m_total * 100),
                MODE_COLOR.get(k, "linear-gradient(90deg,#9aa0ac,#c2c7d1)"),
            )
        )
    modes_html = "".join(mode_rows) or '<div class="empty">还没有人开口辩过</div>'

    def _say(cls, name, chip, tag, text):
        """一句话：说话人 + 这段话说在什么时候 + 内容。"""
        body = esc(text[:220])
        more = ""
        if len(text) > 220:
            more = (
                '<details class="more"><summary>展开全文（共 %d 字）</summary><div>%s</div></details>'
                % (len(text), esc(text[220:]))
            )
        return (
            '<div class="say %s">'
            '<div class="say-head"><b class="who %s">%s</b><span class="tag">%s</span></div>'
            '<div class="say-body">%s%s</div></div>'
            % (cls, chip, esc(name), esc(tag), body, more)
        )

    def turn_card(t):
        v = t["valid"]
        mode = (t["mode"] or "").strip()
        badge = "ok" if v == 1 else ("bad" if v == 0 else "na")
        label = "有效" if v == 1 else ("无效" if v == 0 else "未判定")
        dmg = '<span class="dmg">+%s</span>' % t["damage"] if v == 1 and t["damage"] else ""
        mode_txt = MODE_NAME.get(mode, mode or "")
        parts = [
            '<div class="turn%s">' % (" is-solo" if mode == "solo" else ""),
            '<div class="turn-top">',
            '<span class="badge %s">%s</span>' % (badge, label),
            dmg,
            '<span class="mode">%s</span>' % esc(mode_txt),
            '<span class="time">%s</span>' % esc((t["ts"] or "")[5:]),
            "</div>",
        ]
        q = (t["question"] or "").strip()
        u = (t["user_text"] or "").strip()
        a = (t.get("ai_text") or "").strip()
        p = (t.get("ai_prompt") or "").strip()
        r = (t["reason"] or "").strip()
        name = (
            (t.get("ai_name") or "").strip()
            or opp_by_title.get(q, "")
            or ("桃弁" if mode == "solo" else "对手")
        )

        # 兼容旧数据：老记录把「靶子」存进了 ai_text，没有 ai_prompt
        if mode == "story" and not p and a:
            p, a = a, ""

        if q:
            parts.append('<div class="turn-q">%s</div>' % esc(q[:60]))
        if p:
            # 这句在玩家发言之前，是玩家要反驳的对象
            parts.append(_say("say-prompt", name, "npc", "对手先说的 · 你反驳的就是这句", p))
        if u:
            parts.append(_say("say-me", "你", "me", "你的发言", u))
        if a:
            if mode == "solo":
                parts.append(_say("say-ai", name, "ai", "自述 · 判定的是这句", a))
            else:
                parts.append(_say("say-ai", name, "ai", "回击", a))
        if r:
            prefix = "判定桃弁这句：" if mode == "solo" else ""
            parts.append('<div class="turn-reason">%s%s</div>' % (prefix, esc(r[:160])))
        parts.append("</div>")
        return "".join(parts)

    turns_html = "".join(turn_card(t) for t in d["turns"])

    EVENT_NAME = {
        "home_open": "打开页面",
        "mode_select": "选择模式",
        "mode_case": "选断案",
        "mode_solo": "选交锋",
        "mode_team": "选舌战群儒",
        "story_enter": "进入剧情",
        "board_enter": "进入棋盘",
        "debate_open": "打开辩论框",
        "kb_open": "查知识库",
        "abandon": "退回首页",
    }
    DETAIL_NAME = {"case": "断案", "solo": "交锋", "team": "舌战群儒"}
    beats_html = "".join(
        '<div class="beat"><span class="bt">%s</span><span class="be">%s</span><span class="bd">%s</span></div>'
        % (
            esc((b["ts"] or "")[5:16]),
            esc(EVENT_NAME.get(b["event"], b["event"])),
            esc(DETAIL_NAME.get((b["detail"] or "").strip(), (b["detail"] or "")[:30])),
        )
        for b in d["beats"]
    ) or '<div class="empty">还没有节点记录</div>'

    from datetime import datetime as _dt, timedelta as _td, timezone as _tz

    now_str = _dt.now(_tz(_td(hours=8))).strftime("%m-%d %H:%M:%S")
    updated = d["turns"][0]["ts"] if d["turns"] else "暂无"
    mode_txt = " · ".join(
        "%s %s" % (MODE_NAME.get(k, k), v) for k, v in (d["modes"] or {}).items()
    ) or "暂无"

    tpl = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>耳辩 · 用户反馈</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",system-ui,sans-serif;
  background:#f2f4f8;color:#23262d;padding:22px 20px 60px;line-height:1.6;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:1320px;margin:0 auto}
.top{display:flex;align-items:flex-end;justify-content:space-between;gap:18px;
  margin:2px 2px 18px;flex-wrap:wrap}
h1{font-size:20px;font-weight:700;letter-spacing:.16em}
.sub{font-size:11.5px;color:#9aa0ac;margin-top:5px;word-break:break-all}
.live{display:flex;align-items:center;gap:8px;font-size:11.5px;color:#8a8f9c;
  background:#fff;border-radius:999px;padding:7px 14px;
  box-shadow:0 2px 12px rgba(40,50,70,.055);white-space:nowrap}
.dot{width:7px;height:7px;border-radius:50%;background:#2f8f5b;flex:none;
  animation:pulse 2.2s infinite}
.live.off .dot{background:#c2c7d1;animation:none}
.live.on .dot{background:#2f8f5b}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(47,143,91,.45)}
  70%{box-shadow:0 0 0 7px rgba(47,143,91,0)}100%{box-shadow:0 0 0 0 rgba(47,143,91,0)}}
.bump{color:#2f8f5b;font-weight:700;font-style:normal}
.kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:14px}
@media(min-width:560px){.kpis{gap:12px}}
@media(min-width:780px){.kpis{grid-template-columns:repeat(6,1fr)}}
.kpi{background:#fff;border-radius:15px;padding:15px 10px 13px;text-align:center;
  box-shadow:0 2px 12px rgba(40,50,70,.055);position:relative;overflow:hidden}
.kpi::after{content:"";position:absolute;left:0;right:0;top:0;height:2px;
  background:linear-gradient(90deg,#8fb8f0,#c9a7ff,#f5a9cb);opacity:.7}
.kpi b{display:block;font-size:26px;font-weight:700;letter-spacing:-.02em;
  font-variant-numeric:tabular-nums;line-height:1.2}
.kpi b small{font-size:11px;font-weight:500;color:#9aa0ac;margin-left:1px}
.kpi span{font-size:11.5px;color:#9aa0ac}
.panel{background:#fff;border-radius:16px;padding:16px 18px 18px;margin-bottom:14px;
  box-shadow:0 2px 12px rgba(40,50,70,.055)}
.panel h2{font-size:12px;font-weight:700;letter-spacing:.1em;color:#7b818f;
  margin-bottom:14px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.panel h2 em{font-style:normal;font-weight:500;letter-spacing:0;color:#b8bdc8;font-size:11px}
.grid{display:grid;gap:14px;margin-bottom:14px}
.grid>.panel{margin-bottom:0}
.grid>div>.panel:last-child{margin-bottom:0}
@media(min-width:760px){
  .g3{grid-template-columns:1fr 1fr}
  .g2{grid-template-columns:1fr}
}
@media(min-width:1080px){
  .g3{grid-template-columns:1.18fr 1fr 1fr}
  .g2{grid-template-columns:1.55fr 1fr;align-items:start}
}
/* ---- 图表 ---- */
.chart{display:flex;align-items:flex-end;gap:6px;height:138px;padding-top:14px}
.bar{flex:1;display:flex;flex-direction:column;align-items:center;gap:5px;
  height:100%;min-width:0}
.bar .bb{flex:1;width:100%;display:flex;align-items:flex-end;justify-content:center;
  position:relative}
.bar .bv{position:absolute;left:0;right:0;bottom:calc(var(--h) + 5px);text-align:center;
  font-size:11px;font-weight:700;color:#5b616e;font-variant-numeric:tabular-nums;
  opacity:0;transition:opacity .45s .5s,bottom .4s cubic-bezier(.22,1,.36,1);z-index:1}
.bar .bb i{display:block;width:100%;max-width:46px;height:var(--h);min-height:2px;
  border-radius:7px 7px 3px 3px;
  background:linear-gradient(180deg,#8fb8f0,#c9a7ff);
  transition:height .95s cubic-bezier(.22,1,.36,1)}
.bar .bb i.z{background:#e2e7f0}
.bar:last-child .bb i{background:linear-gradient(180deg,#f0a7c8,#ffc9e0)}
.bar em{font-style:normal;font-size:10.5px;color:#b8bdc8;
  font-variant-numeric:tabular-nums;white-space:nowrap}
html.js .bar .bb i{height:0}
html.js.ready .bar .bb i{height:var(--h)}
html.js .bar .bv{bottom:6px}
html.js.ready .bar .bv{bottom:calc(var(--h) + 5px)}
html.js.ready .bar .bv{opacity:1}
.mrow{margin-bottom:14px}
.mrow:last-child{margin-bottom:0}
.mtop{display:flex;justify-content:space-between;align-items:baseline;
  font-size:12.5px;color:#5b616e;margin-bottom:6px;gap:10px}
.mtop b{font-variant-numeric:tabular-nums;color:#23262d;letter-spacing:-.01em;white-space:nowrap}
.mtop b small{font-weight:500;color:#9aa0ac;font-size:11px}
.mtrack{height:8px;background:#f0f2f7;border-radius:5px;overflow:hidden}
.mtrack i{display:block;height:100%;width:var(--w);border-radius:5px;
  transition:width .95s cubic-bezier(.22,1,.36,1)}
html.js .mtrack i{width:0}
html.js.ready .mtrack i{width:var(--w)}
.tip{border-radius:12px;padding:10px 12px;margin-bottom:8px}
.tip:last-child{margin-bottom:0}
.tip b{display:block;font-size:13px;margin-bottom:2px;letter-spacing:.01em}
.tip span{color:#6b7280;font-size:12px;line-height:1.55;display:block}
.tip.bad{background:#fdf0ef}.tip.bad b{color:#b8493c}
.tip.warn{background:#fdf6e8}.tip.warn b{color:#a9761f}
.tip.ok{background:#eef8f2}.tip.ok b{color:#2f8f5b}
.tip.info{background:#f0f4fc}.tip.info b{color:#4a6fbf}
.flow-row{display:grid;grid-template-columns:74px 1fr 36px 46px;align-items:center;
  gap:8px;margin-bottom:10px;font-size:12px}
.flow-row:last-child{margin-bottom:0}
.flow-label{color:#6b7280;white-space:nowrap}
.flow-track{height:9px;background:#f0f2f7;border-radius:5px;overflow:hidden}
.flow-fill{height:100%;border-radius:5px;background:linear-gradient(90deg,#8fb8f0,#c9a7ff);
  width:var(--w);transition:width .95s cubic-bezier(.22,1,.36,1)}
html.js .flow-fill{width:0}
html.js.ready .flow-fill{width:var(--w)}
.flow-num{text-align:right;font-weight:700;font-variant-numeric:tabular-nums}
.flow-conv{text-align:right;color:#b3b8c4;font-size:11px;font-variant-numeric:tabular-nums}
/* ---- 入场动效 ---- */
html.js .panel,html.js .kpi{animation:fadeUp .55s cubic-bezier(.22,1,.36,1) both}
@keyframes fadeUp{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:none}}
html.still .panel,html.still .kpi{animation:none!important;opacity:1!important;transform:none!important}
@media(prefers-reduced-motion:reduce){
  html.js .panel,html.js .kpi{animation:none!important}
  html.js .flow-fill,html.js .mtrack i,html.js .bar .bb i{transition:none!important}
  html.js .flow-fill,html.js .mtrack i{width:var(--w)}
  html.js .bar .bb i{height:var(--h)}
  html.js .bar .bv{bottom:calc(var(--h) + 5px);opacity:1}
}
.turn{border:1px solid #eef0f5;border-radius:13px;padding:11px 12px;margin-bottom:9px;
  background:#fcfdff}
.turn:last-child{margin-bottom:0}
.turn-top{display:flex;align-items:center;gap:7px;margin-bottom:7px;flex-wrap:wrap}
.badge{font-size:11px;padding:1.5px 9px;border-radius:999px;font-weight:600;letter-spacing:.02em}
.badge.ok{background:#e7f7ee;color:#2f8f5b}
.badge.bad{background:#fdeceb;color:#c05a4d}
.badge.na{background:#f0f2f7;color:#8a8f9c}
.dmg{font-size:12px;font-weight:700;color:#2f8f5b;font-variant-numeric:tabular-nums}
.mode{font-size:11px;color:#9aa0ac}
.time{font-size:11px;color:#c2c7d1;margin-left:auto;font-variant-numeric:tabular-nums}
.turn-q{font-size:11.5px;color:#7b62c9;margin-bottom:6px}
.who{display:inline-block;font-size:10.5px;font-weight:600;padding:1px 6px;border-radius:6px;
  margin-right:6px;line-height:1.55;vertical-align:1.5px;letter-spacing:.02em;white-space:nowrap}
.who.me{background:#1a1a1a;color:#fff}
.who.ai{background:#e4f2ea;color:#3f7d5f}
.who.npc{background:#fbeedd;color:#a2703c}
.say{border-radius:9px;padding:7px 10px;margin-bottom:7px;font-size:13px;line-height:1.62}
.say:last-of-type{margin-bottom:0}
.say-head{display:flex;align-items:center;gap:1px;margin-bottom:4px}
.say-head .who{margin-right:5px}
.say-body{word-break:break-word}
.say-prompt{background:#fdf8f2;border-left:2px solid #e8c9a4;color:#6b5c4a;font-size:12.5px}
.say-me{background:#f8f9fc;border-left:2px solid #1a1a1a;color:#23262d}
.say-ai{background:#f4faf6;color:#3f6f57;font-size:12.5px}
.tag{font-size:10px;color:#b6ada1;letter-spacing:.02em}
.say-ai .tag{color:#8fb3a1}
.say-me .tag{color:#a8adb8}
.turn-reason{font-size:11.5px;color:#9aa0ac;margin-top:6px;line-height:1.5;
  padding-left:2px}
.more{margin-top:5px}
.more summary{font-size:11px;color:#8a8f9c;cursor:pointer;list-style:none}
.more summary::-webkit-details-marker{display:none}
.more summary::before{content:"+ ";font-size:10px}
.more div{margin-top:4px;color:#5b616e}
.legend{display:flex;align-items:center;gap:6px;flex-wrap:wrap;font-size:11px;color:#9aa0ac;
  margin:-2px 0 12px}
.legend .lg{display:inline-flex;align-items:center;gap:4px;white-space:nowrap;margin-right:4px}
.sw{width:9px;height:9px;border-radius:3px;display:inline-block}
.sw-p{background:#f3e3cc;border-left:2px solid #d8b184}
.sw-m{background:#eceef4;border-left:2px solid #1a1a1a}
.sw-a{background:#dcefe4}
.qbar{display:flex;gap:7px;margin:0 0 12px;flex-wrap:wrap}
.qbar input[type=search]{flex:1 1 150px;min-width:0;font-size:12.5px;padding:7px 10px;
  border:1px solid #e4e7ee;border-radius:9px;background:#fbfcfe;color:#23262d;
  -webkit-appearance:none;appearance:none}
.qbar select{font-size:12px;padding:7px 8px;border:1px solid #e4e7ee;border-radius:9px;
  background:#fbfcfe;color:#5b616e}
.qbar button{font-size:12.5px;padding:7px 16px;border:0;border-radius:9px;background:#1a1a1a;
  color:#fff;font-weight:600}
@media(max-width:430px){
  .qbar input[type=search]{flex:1 1 100%}
  .qbar select{flex:1 1 0}
}
.beat{display:flex;gap:8px;font-size:11.5px;padding:6px 0;border-bottom:1px solid #f6f7fa}
.beat:last-child{border-bottom:0}
.bt{color:#c2c7d1;font-variant-numeric:tabular-nums;width:70px;flex:none}
.be{color:#5b616e;width:74px;flex:none;font-weight:600}
.bd{color:#9aa0ac;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
.empty{text-align:center;color:#c7ccd6;font-size:12.5px;padding:24px 0}
.foot{text-align:center;font-size:11px;color:#b8bdc8;margin-top:16px;line-height:1.7}
.foot code{background:#e9edf4;border-radius:5px;padding:1px 5px;color:#8a8f9c;font-size:10.5px}
.rf{font-size:11.5px;padding:7px 15px;border:0;border-radius:999px;background:#1a1a1a;
  color:#fff;font-weight:600;cursor:pointer;white-space:nowrap}
.rf:active{transform:scale(.97)}
.panel h2 em.bump{color:#2f8f5b;font-weight:700}
</style>
<script>document.documentElement.classList.add("js");</script>
</head>
<body><div class="wrap">
<div class="top">
  <div>
    <h1>耳辩 · 用户反馈</h1>
    <div class="sub">页面生成 @NOW@ · 最近一条 @UPDATED@ · 模式分布 @MODES@</div>
  </div>
  <div style="display:flex;align-items:center;gap:8px">
    <div class="live" id="live"><span class="dot"></span><span id="liveTxt">实时同步 · 15s</span></div>
    <button class="rf" onclick="location.reload()">刷新</button>
  </div>
</div>

<div class="kpis">
  <div class="kpi"><b><span class="n" data-k="total" data-v="@TOTAL@" data-dec="0">@TOTAL@</span></b><span>玩家发言</span></div>
  <div class="kpi"><b><span class="n" data-k="today" data-v="@TODAY@" data-dec="0">@TODAY@</span></b><span>今日</span></div>
  <div class="kpi"><b><span class="n" data-k="rate" data-v="@RATEV@" data-dec="0">@RATE@</span><small>%</small></b><span>判定有效</span></div>
  <div class="kpi"><b><span class="n" data-k="sessions" data-v="@SESS@" data-dec="0">@SESS@</span></b><span>玩家人数</span></div>
  <div class="kpi"><b><span class="n" data-k="ips" data-v="@IPS@" data-dec="0">@IPS@</span></b><span>独立 IP</span></div>
  <div class="kpi"><b><span class="n" data-k="avg" data-v="@AVGV@" data-dec="1">@AVG@</span><small>s</small></b><span>平均判定</span></div>
</div>

<div class="grid g3">
  <div class="panel">
    <h2>最近 7 天 <em>每天开口次数</em></h2>
    <div class="chart">@DAILY@</div>
  </div>
  <div class="panel">
    <h2>玩家走到哪一步 <em>右侧为相邻转化率</em></h2>
    @FLOW@
  </div>
  <div class="panel">
    <h2>模式分布 <em>含交锋里桃弁的自述回合</em></h2>
    @MODESHTML@
  </div>
</div>

<div class="grid g2">
  <div class="panel">
    <h2>玩家说了什么 · 最新 @LIMIT@ 条 <em id="newTip"></em></h2>
    <form class="qbar" method="get" action="/admin/feedback">
      <input type="hidden" name="k" value="@TOKEN@">
      <input type="search" name="q" value="@KW@" placeholder="搜发言内容 / 辩题 / 判定理由">
      <select name="n">@OPTS@</select>
      <button type="submit">查</button>
    </form>
    <div class="legend">
      <span class="lg"><i class="sw sw-p"></i>对手先说</span>
      <span class="lg"><i class="sw sw-m"></i>你的发言</span>
      <span class="lg"><i class="sw sw-a"></i>对手回击</span>
    </div>
    @TURNS@
    @NOFOUND@
    <div class="legend" style="margin:12px 0 0">按「先有对手台词 → 你再反驳 → 对手回击」的真实顺序排列。只记录「已经出过判定」的发言；被拦下（不足 3 字、超过 300 字）或接口报错的不会入库。</div>
  </div>
  <div>
    <div class="panel">
      <h2>这些数字在说什么</h2>
      @TIPS@
    </div>
    <div class="panel">
      <h2>页面行为 · 最新 120 条</h2>
      @BEATS@
    </div>
  </div>
</div>

<div class="foot">
  数据文件 <code>@DB@</code> · 上方数字与图表每 15 秒自动同步，发言流按需刷新
</div>
</div>
<script>
(function(){
  var TOKEN="@TOKEN@";
  var API="/admin/feedback/live?k="+encodeURIComponent(TOKEN);
  var r=document.documentElement;
  var lastTotal=@TOTAL@;

  function es(t){return 1-Math.pow(1-t,3)}
  function roll(el,to,dec){
    if(!el)return;
    var num=parseFloat(to);
    if(to===""||to===null||isNaN(num)){el.textContent="-";el.dataset.cur="";return;}
    var from=parseFloat(el.dataset.cur);
    if(isNaN(from))from=0;
    el.dataset.cur=num;
    var seq=(el._seq||0)+1;el._seq=seq;
    function fix(){el.textContent=dec?num.toFixed(dec):Math.round(num);}
    if(from===num){fix();return;}
    var t0=performance.now();
    function step(t){
      if(el._seq!==seq)return;
      var p=Math.min(1,(t-t0)/680),v=from+(num-from)*es(p);
      el.textContent=dec?v.toFixed(dec):Math.round(v);
      if(p<1)requestAnimationFrame(step);else fix();
    }
    requestAnimationFrame(step);
    setTimeout(function(){if(el._seq===seq)fix();},760);
  }
  function byK(k){return document.querySelector('.kpi .n[data-k="'+k+'"]')}

  // 首次进场：数字归零，图表归零，再一起涨起来
  var ns=document.querySelectorAll(".kpi .n");
  var blocks=document.querySelectorAll(".panel,.kpi");
  for(var b=0;b<blocks.length;b++){blocks[b].style.animationDelay=(b*35)+"ms";}
  function start(){
    if(r.classList.contains("ready"))return;
    for(var i=0;i<ns.length;i++){ns[i].dataset.cur="0";ns[i].textContent="0";}
    r.classList.add("ready");
    setTimeout(function(){
      for(var i=0;i<ns.length;i++){
        roll(ns[i],ns[i].dataset.v,parseInt(ns[i].dataset.dec||"0",10));
      }
    },60);
  }
  requestAnimationFrame(function(){requestAnimationFrame(start);});
  setTimeout(start,420);

  var liveEl=document.getElementById("live"),liveTxt=document.getElementById("liveTxt");
  var tip=document.getElementById("newTip");
  function setLive(ok,txt){liveEl.className="live "+(ok?"on":"off");liveTxt.textContent=txt;}
  function flag(n){if(tip)tip.textContent="+"+n+" 条新记录，刷新看内容";}

  function apply(d){
    var total=d.total_turns||0;
    roll(byK("total"),total,0);
    roll(byK("today"),d.today_turns||0,0);
    roll(byK("sessions"),d.sessions||0,0);
    roll(byK("ips"),d.ips||0,0);
    roll(byK("rate"),total?Math.round((d.valid||0)/total*100):"",0);
    roll(byK("avg"),d.avg_ms?d.avg_ms/1000:"",1);

    var f=d.funnel||{};
    var st=[f.home_open||0,f.mode_select||0,f.debate_enter||0,total];
    var top=Math.max(st[0],st[1],st[2],st[3],1);
    var rows=document.querySelectorAll(".flow-row");
    for(var i=0;i<rows.length&&i<4;i++){
      var v=st[i];
      rows[i].querySelector(".flow-fill").style.setProperty("--w",Math.max(2,v/top*100).toFixed(1)+"%");
      var numEl=rows[i].querySelector(".flow-num");
      if(parseInt(numEl.textContent,10)!==v)roll(numEl,v,0);
      if(i>0){
        var prev=st[i-1];
        rows[i].querySelector(".flow-conv").textContent=
          !prev?"-":(v>prev?"—":Math.round(v/prev*100)+"%");
      }
    }

    var daily=d.daily||[],bars=document.querySelectorAll(".bar");
    if(daily.length&&bars.length&&daily[daily.length-1].day!==bars[bars.length-1].dataset.day){
      location.reload();return;
    }
    var max=1,map={};
    for(var i=0;i<daily.length;i++){map[daily[i].day]=daily[i].n;if(daily[i].n>max)max=daily[i].n;}
    for(var i=0;i<bars.length;i++){
      var day=bars[i].dataset.day;
      if(!(day in map))continue;
      var n=map[day];
      bars[i].querySelector(".bv").textContent=n;
      bars[i].style.setProperty("--h",Math.max(1.2,n/max*100).toFixed(1)+"%");
      bars[i].querySelector(".bb i").className=n===0?"z":"";
    }

    var modes=d.modes||{},mt=0,k;
    for(k in modes)mt+=modes[k];
    mt=mt||1;
    var mrows=document.querySelectorAll(".mrow");
    for(var i=0;i<mrows.length;i++){
      var key=mrows[i].dataset.mode,v=modes[key]||0;
      mrows[i].querySelector(".mtop b").innerHTML=
        v+'<small> '+Math.round(v/mt*100)+'%</small>';
      mrows[i].querySelector(".mtrack i").style.setProperty("--w",Math.max(2,v/mt*100).toFixed(1)+"%");
    }

    if(total>lastTotal)flag(total-lastTotal);
    lastTotal=total;
  }

  function sync(){
    fetch(API,{cache:"no-store"}).then(function(res){
      if(!res.ok)throw new Error("http");
      return res.json();
    }).then(function(d){
      apply(d);
      var hh=new Date();
      setLive(true,"实时 · "+("0"+hh.getHours()).slice(-2)+":"+("0"+hh.getMinutes()).slice(-2)+":"+("0"+hh.getSeconds()).slice(-2));
    }).catch(function(){
      setLive(false,"同步失败 · 稍后自动重试");
    });
  }

  setInterval(function(){if(!document.hidden)sync();},15000);
  document.addEventListener("visibilitychange",function(){if(!document.hidden)sync();});
})();
</script>
</body></html>"""

    limit = d.get("limit") or 60
    kw = d.get("kw") or ""
    opts = "".join(
        '<option value="%d"%s>最近 %d 条</option>' % (n, " selected" if n == limit else "", n)
        for n in (60, 120, 300)
    )
    nofound = ""
    if not d["turns"]:
        nofound = (
            '<div class="empty">没有匹配「%s」的发言</div>' % esc(kw)
            if kw
            else '<div class="empty">还没有人开口辩过</div>'
        )

    html = (
        tpl.replace("@NOW@", esc(now_str))
        .replace("@UPDATED@", esc(updated))
        .replace("@MODESHTML@", modes_html)
        .replace("@DAILY@", daily_html)
        .replace("@MODES@", esc(mode_txt))
        .replace("@TOTAL@", str(total))
        .replace("@TODAY@", str(d["today_turns"]))
        .replace("@RATEV@", str(rate) if rate is not None else "")
        .replace("@RATE@", str(rate) if rate is not None else "-")
        .replace("@AVGV@", ("%.1f" % (avg_ms / 1000.0)) if avg_ms else "")
        .replace("@AVG@", avg_s)
        .replace("@SESS@", str(d["sessions"]))
        .replace("@IPS@", str(d["ips"]))
        .replace("@FLOW@", flow_html)
        .replace("@TIPS@", tips_html)
        .replace("@TURNS@", turns_html)
        .replace("@BEATS@", beats_html)
        .replace("@NOFOUND@", nofound)
        .replace("@LIMIT@", str(limit))
        .replace("@KW@", esc(kw))
        .replace("@OPTS@", opts)
        .replace("@TOKEN@", esc(d.get("token", "")))
        .replace("@DB@", esc(d["db"]))
    )

    # ?static=1：直接输出动画终态，方便截图 / 做汇报素材，也方便无脚本环境
    try:
        if request.args.get("static") == "1":
            html = html.replace(
                '<html lang="zh-CN">', '<html lang="zh-CN" class="js ready still">', 1
            )
    except Exception:  # noqa: BLE001 - 无请求上下文时按普通渲染
        pass
    return html

@api.route("/api/law", methods=["GET"])
def get_law():
    query = request.args.get("q", "")
    if not query:
        return jsonify({"laws": []})

    matched = [
        law
        for law in KB.get("laws", [])
        if query in law["name"] + law["article"]
    ]
    return jsonify({"laws": matched})


# ---------------------------------------------------------------------------
# 联机对辩（房间码 + 轮询）
# 为什么不用 WebSocket：服务器只有 1.6G 内存、单进程跑着 Flask，
# 上 socketio/gevent 要动部署，而辩论是回合制，1~2 秒的轮询延迟无感。
# ---------------------------------------------------------------------------


def _duel_judge_player(text, topic):
    """判玩家这一句，同时让对手模型给出下一句（省一次调用）。"""
    try:
        result = debate_argument(
            chapter_title="联机对辩 · %s" % topic["q"],
            opponent_name="桃弁",
            opponent_line=topic["q"],
            user_input=text,
            evidence=[
                {"name": "辩题", "description": topic["q"]},
                {"name": "相关法条", "description": topic["law"]},
                {"name": "你的立场", "description": topic["side"]},
            ],
            current_hp=100,
            max_hp=100,
            player_name="玩家",
            persona="rival",
        )
        return {
            "valid": result.get("valid"),
            "damage": result.get("damage", 0),
            # 模型偶尔给 valid=True 却把 type 写成「无效」，前端会显示成自相矛盾
            "type": ("法理" if result.get("valid") and result.get("type") in (None, "无效")
                     else result.get("type", "无效")),
            "reply": result.get("reply", ""),
            "reason": "",
        }
    except (DeepSeekConfigurationError, DeepSeekResponseError, OpenAIError):
        logger.exception("联机对辩判定失败")
        return {"valid": False, "damage": 0, "type": "无效", "reason": "判定服务暂时不可用"}


def _duel_judge_ai(text, topic):
    """判 AI 自己那句话，免得它永远不掉血。"""
    try:
        result = judge_argument(text, topic["law"])
        return {
            "valid": result.get("valid"),
            "damage": result.get("damage", 0),
            "type": result.get("type", "无效"),
            "reason": result.get("reason", ""),
        }
    except (DeepSeekConfigurationError, OpenAIError):
        logger.exception("联机对辩 AI 判定失败")
        return {"valid": True, "damage": 15, "type": "法理", "reason": ""}


@api.route("/api/duel/create", methods=["POST"])
def duel_create():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()[:12]
    return jsonify(duel.create(name))


@api.route("/api/duel/join", methods=["POST"])
def duel_join():
    body = request.get_json(silent=True) or {}
    status, payload = duel.join(body.get("code", ""), (body.get("name") or "").strip()[:12])
    return jsonify(payload), status


@api.route("/api/duel/<code>", methods=["GET"])
def duel_state(code):
    status, payload = duel.snapshot(code, request.args.get("token", ""))
    return jsonify(payload), status


@api.route("/api/duel/<code>/say", methods=["POST"])
def duel_say(code):
    body = request.get_json(silent=True) or {}
    token = body.get("token", "")

    def judge(text, topic):
        result = _duel_judge_player(text, topic)
        duel.stash_ai_line(code, result.get("reply", ""))
        return result

    status, payload = duel.say(code, token, body.get("text", ""), judge)
    return jsonify(payload), status


@api.route("/api/duel/<code>/ai", methods=["POST"])
def duel_ai(code):
    status, payload = duel.ai_turn(code, _duel_judge_ai)
    return jsonify(payload), status
