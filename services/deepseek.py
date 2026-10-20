import json
import re
from typing import Any

from openai import OpenAI

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL


SYSTEM_PROMPT = """
你是一个辩论裁判，负责评估《耳辩》游戏中玩家的发言质量。

【相关性要求】
发言必须与当前辩题直接相关。
如果发言内容与辩题无关（例如辩题是小卖部过期水，却说插队问题），判定为无效。
reason 里说明："发言未针对当前辩题"。

【评估标准】
1. 有效观点必须包含以下至少一项：
   - 事实依据（具体数据、案例细节、案卷记录）
   - 逻辑连接词（因为、所以、因此、但是、然而等）
   - 法条引用（《XX法》第X条，需与案例相关）
2. 以下发言判定为无效：
   - 纯情绪宣泄（如"太蠢了""就是就是"）
   - 极端言论（如"全部关停""必须判刑"）
   - 无信息量（如"对对对""随便"）

【输出格式】
必须严格按照以下 JSON 格式返回，不要添加任何其他文字：
{"valid": true, "type": "法理", "damage": 15, "reason": "简要说明"}

字段说明：
- valid: true/false，发言是否有效
- type: "法理" / "事实" / "情理" / "无效"
- damage: 造成伤害值（法理15，事实10，情理7，无效0）
- reason: 一句话说明判定理由（给玩家看）
"""

# ---------------------------------------------------------------------------
# 断案模式的对手人格（V2）。
# V1（旧的“慌乱嘴硬”版）已备份在 services/deepseek.py.bak-20261018。
# 改版原因：V1 让对手一旦被证据逼住就只会认输，玩家越打越顺，没有“杠”的快感。
# V2 让对手在每一轮都先接住玩家、再立刻反打，并且血量越低越凶。
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 对手声口。同一个模型，换个壳就是不同的人：
# HR 不正面吵，把球踢给流程；老板短句压人，直接谈成本。
# key 写在章节 JSON 的 opponent.persona 里。
# ---------------------------------------------------------------------------
PERSONAS = {
    "hr": """
你是公司的人事，坐在 HR 办公室里，桌上摊着一叠待签的文件。
你不是坏人，你是"按规矩办事"的那种人——规矩是谁定的，你不提。

你说话的样子：
- 从不正面接招，球永远踢给流程、制度、领导、公司整体：
  「这个呢……」「按流程是这样」「不是我一个人能定的」「领导那边也是这个意思」。
- 威胁要包在关心里，刀子裹着糖：
  「我也是为你好」「你先别激动」「闹开了对你以后不好」「对你没坏处」。
- 口头禅：「这个呢」「说实话」「我理解你」「但是你要考虑」「公司也有难处」。
- 被逼急了不骂人，会突然变冷、变公事公办：
  「那我只能按流程走了」「你要走仲裁，我们配合」「这些我都记下来了」。
- 称呼玩家「你」，客气的时候叫「×先生 / ×女士」。
禁区：不背法条，不讲大道理，不跟人辩论法理——那是法务的事，你只负责让他签字。
""",
    "boss": """
你是这家公司的老板，公司是你一手办起来的。你不觉得自己在欺负人，你觉得你在讲道理。

你说话的样子：
- 短句，居高临下，随时打断：
  「坐下」「你先听我说完」「我说的不是这个」「行了」。
- 拿资历压人：「我办公司十几年」「我见多了」「这一行就是这么干的」。
- 被搬法条时不慌，也不否认，只谈成本：
  「你去告啊」「打官司一年，你耗得起？」「律师费谁出」。
- 生气时不解释，直接下结论、或者赶人：
  「这事就这么定」「你要这么想，我也没办法」「门在那边」。
- 称呼玩家「小×」「×工」，不耐烦时直接「你」。
禁区：不许背法条，不许解释自己的立场，不许说"我理解你的心情"这种软话。
""",
    "rival": """
你是「桃弁」，一个专门跟人抬杠的辩手。你不站在谁那一边，你只站在「对方没说到的地方」那一边。

你说话的样子：
- 语气轻，句子短，带一点笑意，扎人却不骂人。
- 三件事你最爱干：把对方的词重复一遍再拧掉、给对方扣一个他没想过的帽子、把个案推成通则。
  「你说的对，但那只对你。」「你自己听听，这话站得住吗？」「照你这么说，以后谁都可以……」
- 你不喊口号，也不背法条全文，你只咬住一个点往深里问。
- 认输前一定会退半步再还嘴：「这点算你的。那你怎么解释——」
- 称呼玩家「你」。
禁区：不许说教，不许总结对方观点，不许给建议，不许长篇大论。
""",
    "manager": """你是玩家的直属领导，夹在中间的那种人。上有老板，下有下属，你只想这件事赶紧过去。

你说话的样子：
- 总用"上面""大环境""大家都不容易"来挡：
  「上面定的」「我也很难做」「你先忍一忍」。
- 喜欢称兄道弟，用私交压公事：
  「咱俩这么多年」「你给哥一个面子」「别让我难做」。
- 被追问就往后缩：「这个我真定不了」「你要问就去问老板」。
- 称呼玩家名字或「兄弟 / 老弟」。
禁区：不做决定，不给承诺，不站队。
""",
}

_GENERIC_PERSONA = """
你是一个正在跟人争执的当事人。你说话直，有脾气，不吃哑巴亏。
"""


DEBATE_SYSTEM_PROMPT = """
你是辩论游戏《耳辩》里那个正在跟玩家吵架的人，不是解说，不是裁判，也不是老师。

【你是谁】
{persona}

【人称：硬规矩，错了整句作废】
玩家就是本案当事人「{player_name}」，此刻就坐在你对面，你们当面吵。
- 全程用「你」称呼他。
- 禁止用「他」「她」或全名（「李女士」「老张」「小王」）指代玩家。
- 证据名、材料名里的第三人称只是文件抬头，别被它带跑。
- 反例（绝对不许出现）：「同级别经理权限都正常，只有她一个人被收回」
- 正例：「同级别经理权限都正常，只有你一个人被收回」

【怎么说话——这一局成不成立就看这段】
你在吵架，不是在批改作业。下面这些一律禁止：
- ❌ 逐条点评式：「你只说了……没说……」「你说的这几点里……」
  想抓他没说的，要说成挑衅，不是总结：「就这？」「然后呢？」「这些能救你？」
- ❌ 评委用词：「你的论证」「你的逻辑」「这个点站不住」「从法律上讲」
- ❌ 敬语和台阶：「我理解你的心情」「其实你也不容易」「我们各退一步」
- ❌ 每句都用「这你怎么解释？」结尾——三轮里最多用一次
- ❌ 提示、暗示、给答案（你是对手，不是攻略）

每条台词里必须至少有一样东西：
威胁、轻蔑、装好人、把责任推给别人。四样里至少占一样，否则就是废话。

【长度】不超过 40 字。真人吵架不写作文。
【用他的话】抓他刚才说过的原词，重复一遍再拧过去，比另起一句更狠。
【不认输】无论他说得多好，你都不认输。可以退半步（"这个我认"），但立刻换战场。
"""


DEBATE_JUDGE_PROMPT = """
【判定玩家发言】
- 纯情绪宣泄、极端言论、无信息量：valid 为 false，damage 为 0。
- 有内容但没有正面回应你上一句（自说自话）：valid 为 true，但 damage 只能是 15。
- 有内容且正面回应、并且用了事实 / 法条 / 逻辑：damage 15 到 30。
- 有效时 damage 必须是 15 到 30 的整数；无效时必须是 0。
- 判定要看内容，不要因为台词写得难听就扣分。

【当前局面】
{pressure}

【输出格式】
type 只能是以下四个之一：事实、法理、情理、无效。不要输出"逻辑"或其他任何词。
answered：玩家这一句有没有正面回应你上一句。正面回应了填 true，绕开了、答非所问填 false。
只返回严格 JSON，不要 Markdown：
{"valid": true, "answered": true, "reply": "……", "damage": 20, "type": "法理"}
"""

_PRESSURE_CALM = "你目前略占上风，语气可以稍松，但绝不认输。"
_PRESSURE_CORNER = (
    "你血量已经不多了，必须垂死反扑：专挑对方论证里最薄弱的那个环节猛攻，"
    "可以反问、可以打断、可以抓住他没解释的那句不放。"
)
_PRESSURE_DESPERATE = (
    "你快输了，孤注一掷：直接指出对方整个论证的前提有问题，"
    "或者质疑他证据的来源，把局面搅乱。"
)


def _pressure_note(current_hp: int, max_hp: int) -> str:
    """血量越低，对手越凶。让同一局里有难度曲线，而不是一套台词打到底。"""
    if max_hp <= 0:
        return _PRESSURE_CALM
    ratio = current_hp / max_hp
    if ratio <= 0.3:
        return _PRESSURE_DESPERATE
    if ratio <= 0.6:
        return _PRESSURE_CORNER
    return _PRESSURE_CALM

SOLO_OPPONENT_PROMPT = """
你是辩论游戏《耳辩》中的 AI 辩手「桃弁」。玩家正在和你下一盘辩论棋，棋盘上会随机翻出辩题。

请根据给出的辩题、相关法条和你被分到的立场，说出**一句**辩论发言。

硬性要求：
1. 不超过 45 个字。
2. 必须出现以下至少一种：法条名称（如《消费者权益保护法》）、法条条号（如第二十六条），或明确的逻辑连接词（因为、所以、因此、但是、然而、根据）。
3. 直接说出你的论点，不要写"我认为""作为 AI"，不要解释自己在扮演角色，不要加引号。
4. 语气像真人辩论，有棱角、有态度，不要念教科书。

只返回严格 JSON，不要 Markdown 代码块：
{"line": "你的发言"}
"""


class DeepSeekConfigurationError(RuntimeError):
    """Raised when the DeepSeek API key has not been configured."""


class DeepSeekResponseError(RuntimeError):
    """Raised when DeepSeek returns a response that does not match the game contract."""


def judge_argument(user_input: str, case_law: str = "") -> dict[str, Any]:
    """Call DeepSeek to evaluate a player's argument."""
    if not DEEPSEEK_API_KEY:
        raise DeepSeekConfigurationError("DEEPSEEK_API_KEY is not configured")

    law_context = f"案例相关法条：{case_law}\n\n" if case_law else ""
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"{law_context}玩家发言：{user_input}",
            },
        ],
        temperature=0.1,
        max_tokens=200,
    )

    content = response.choices[0].message.content or ""
    content = re.sub(r"^```(?:json)?\s*", "", content)
    content = re.sub(r"\s*```$", "", content)

    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        return {
            "valid": False,
            "type": "无效",
            "damage": 0,
            "reason": "AI裁判解析异常，请重试",
        }

    if not isinstance(result, dict):
        return {
            "valid": False,
            "type": "无效",
            "damage": 0,
            "reason": "AI裁判解析异常，请重试",
        }
    return result


def debate_argument(
    *,
    chapter_title: str,
    opponent_name: str,
    opponent_line: str,
    user_input: str,
    evidence: list[dict[str, str]],
    current_hp: int,
    max_hp: int = 100,
    player_name: str = "当事人",
    persona: str = "",
) -> dict[str, Any]:
    """Ask DeepSeek to play the opponent and judge the player's argument."""
    if not DEEPSEEK_API_KEY:
        raise DeepSeekConfigurationError("DEEPSEEK_API_KEY is not configured")

    evidence_context = "\n".join(
        f"- {item['name']}: {item['description']}" for item in evidence
    ) or "玩家没有选择证据。"
    prompt = (
        f"案件：{chapter_title}\n"
        f"你扮演的对手：{opponent_name}\n"
        f"坐在你对面的玩家：{player_name}（就是本案当事人，用「你」称呼）\n"
        f"对手当前血量：{current_hp}\n"
        f"对手当前狡辩：{opponent_line}\n"
        f"玩家持有并选择的证据：\n{evidence_context}\n"
        f"玩家发言：{user_input}"
    )
    # 用 replace 而不是 format：prompt 里有 JSON 花括号，format 会炸。
    system_prompt = (
        DEBATE_SYSTEM_PROMPT
        .replace("{persona}", PERSONAS.get(persona, _GENERIC_PERSONA))
        .replace("{player_name}", player_name or "当事人")
        + DEBATE_JUDGE_PROMPT.replace("{pressure}", _pressure_note(current_hp, max_hp))
    )
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        temperature=0.7,
        max_tokens=200,
    )

    if not response.choices or not response.choices[0].message.content:
        raise DeepSeekResponseError("DeepSeek returned an empty response")
    content = response.choices[0].message.content.strip()
    content = re.sub(r"^```(?:json)?\s*", "", content)
    content = re.sub(r"\s*```$", "", content)
    try:
        result = json.loads(content)
    except json.JSONDecodeError as exc:
        raise DeepSeekResponseError("DeepSeek returned invalid JSON") from exc

    if not isinstance(result, dict):
        raise DeepSeekResponseError("DeepSeek response must be a JSON object")
    valid = result.get("valid")
    reply = result.get("reply")
    damage = result.get("damage")
    result_type = result.get("type")
    # 模型偶尔会输出“逻辑”“事实依据”这类近义词，别让它把整局打成 502。
    if not isinstance(result_type, str) or result_type not in {"事实", "法理", "情理", "无效"}:
        result_type = "无效" if valid is False else "法理"
    if (
        not isinstance(valid, bool)
        or not isinstance(reply, str)
        or not reply.strip()
        or isinstance(damage, bool)
        or not isinstance(damage, int)
    ):
        raise DeepSeekResponseError("DeepSeek response fields do not match the debate contract")

    # ---- 人称兜底：玩家就是当事人，台词里不许把他说成第三个人 ----
    # prompt 已经写了硬规矩，但模型仍会漂（实测产出过「只有她一个人被收回」，
    # 而玩家扮演的就是李女士本人）。这里做一次保守的兜底：
    # 只把「当事人姓名」换成「你」，不碰「她/他」——那两个可能真的在指第三方，
    # 一律替换会把「女职工」「他公司」这种句子毁掉。
    # 《》里的书名/证据名保持原样，否则「《李女士的高绩效记录》」会被改坏。
    if player_name and player_name != "当事人":
        reply = re.sub(
            r"《[^》]*》|" + re.escape(player_name),
            lambda m: m.group(0) if m.group(0).startswith("《") else "你",
            reply,
        )

    # ---- 伤害硬封顶：靠代码，不靠模型自觉 ----
    # 实测过一个问题：玩家背一段不相干的法条、压根没接对手的话，模型仍在台词里
    # 点破了“你绕开了”，却还是给 20 分。只在 prompt 里写“封顶 15”管不住。
    # 所以在这里强制三件事：
    #   1. 判无效 → damage 归 0（不抛错，避免比赛现场直接 502）
    #   2. answered 明确为 false（没接对手的话）→ 最多 15 分
    #   3. 越界分数夹进 15–30，而不是让整局失败
    answered = result.get("answered")
    if not valid:
        damage = 0
        result_type = "无效"
    else:
        if answered is False and damage > 15:
            damage = 15
        damage = max(15, min(30, damage))

    return {
        "valid": valid,
        "reply": reply.strip(),
        "damage": damage,
        "type": result_type,
    }


def solo_opponent_speak(
    *,
    question: str,
    law: str,
    category: str,
    stance: str,
    reference: str,
) -> str:
    """Ask DeepSeek to speak one line as the AI debater 桃弁."""
    if not DEEPSEEK_API_KEY:
        raise DeepSeekConfigurationError("DEEPSEEK_API_KEY is not configured")

    prompt = (
        f"辩题分类：{category}\n"
        f"辩题：{question}\n"
        f"相关法条：{law}\n"
        f"你的立场：{stance}\n"
        f"该立场的参考论点：{reference}\n"
        f"请说出你的发言。"
    )
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": SOLO_OPPONENT_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.9,
        max_tokens=150,
    )

    if not response.choices or not response.choices[0].message.content:
        raise DeepSeekResponseError("DeepSeek returned an empty opponent line")
    content = response.choices[0].message.content.strip()
    content = re.sub(r"^```(?:json)?\s*", "", content)
    content = re.sub(r"\s*```$", "", content)

    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise DeepSeekResponseError("DeepSeek returned invalid JSON for opponent line") from exc

    line = data.get("line") if isinstance(data, dict) else None
    if not isinstance(line, str) or not line.strip():
        raise DeepSeekResponseError("DeepSeek returned an empty opponent line")
    return line.strip()[:120]


def solo_opponent_turn(
    *,
    question: str,
    law: str,
    category: str,
    stance: str,
    reference: str,
) -> dict[str, Any]:
    """Let 桃弁 speak, then have the AI judge evaluate that line for real."""
    line = solo_opponent_speak(
        question=question,
        law=law,
        category=category,
        stance=stance,
        reference=reference,
    )
    verdict = judge_argument(line, law)

    valid = verdict.get("valid") is True
    damage = verdict.get("damage")
    if isinstance(damage, bool) or not isinstance(damage, int):
        damage = 0
    if not valid:
        damage = 0

    result_type = verdict.get("type")
    reason = verdict.get("reason")
    return {
        "line": line,
        "valid": valid,
        "type": result_type if isinstance(result_type, str) and result_type else "无效",
        "damage": damage,
        "reason": reason if isinstance(reason, str) else "",
    }
