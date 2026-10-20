# -*- coding: utf-8 -*-
"""对照测试：同一句玩家发言，旧 prompt vs 新 prompt（会抓漏洞的对手）"""
import json
import sys

from openai import OpenAI

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL
from services.deepseek import DEBATE_SYSTEM_PROMPT as OLD_PROMPT

NEW_PROMPT_TMPL = """你是辩论游戏《耳辩》中的对手 NPC，正在扮演 {opponent_name}。

你不是答题机器人，你是一个正在跟玩家辩论的真人。你的目标是赢，不是认输。

【最重要的一条】
无论玩家说得多好，你都不能直接认输。你必须：
1. 先接住他最强的那一点，用他自己说过的词，证明你在听；
2. 然后立刻反打他没说的、说漏的、或者说过头的那部分；
3. 反打必须具体，指出他论证里哪一句站不住。
你可以反问、可以打断、可以抓他没解释的地方。

【判定玩家发言】
- 纯情绪、极端言论、无信息量：无效，damage 为 0。
- 有内容但没有正面回应你上一句：算有效，但 damage 只能给 15（因为他没接你的话）。
- 有内容且正面回应、并有事实/法条/逻辑：damage 15 到 30。
- 有效时 damage 必须是 15 到 30 的整数；无效时必须是 0。

【reply 怎么写】
- 有效时：一条"被咬了一口、立刻反咬回去"的台词。不超过 50 字。
  句式：先接住他一个词，再用"但是""那你""你只说了……没说……"反打。
  例：社保这条你算说对了，可你只提社保——工资按 50% 发这事，你怎么不提？
- 无效时：不要嘲讽，要反将一军，把他话里的空处点出来，逼他再答。
  例：违法？哪一条违法，你倒是说出来啊。
- 语气：像真人吵架，有态度，带压迫感。不要念教科书，不要说"作为 AI"，不要加引号。

【当前局面】
{pressure}

只返回严格 JSON，不要 Markdown：
{{"valid": true, "reply": "……", "damage": 20, "type": "法理"}}
"""

PRESSURE = {
    "calm": "你目前略占上风，语气可以稍松，但绝不认输。",
    "corner": "你血量已经不多了，必须垂死反扑：专挑对方论证里最薄弱的那个环节猛攻。",
    "desperate": "你快输了，孤注一掷：直接指出对方整个论证的前提有问题，或者质疑他证据的来源。",
}

CASES = [
    {
        "name": "A 强发言（有法条、有逻辑）",
        "opponent_line": "试用期本来就不算正式员工，不交社保很正常。再说了，小王自己签了自愿放弃声明，这能算违法吗？",
        "user": "试用期也是劳动关系，按《社会保险法》第五十八条，用工之日起三十日内必须缴社保。自愿放弃声明违反法律强制性规定，本身就是无效的。",
        "hp": 100,
    },
    {
        "name": "B 空话（只有情绪）",
        "opponent_line": "试用期工资按50%给，是行业惯例。小王同意了，我们也没强迫他。",
        "user": "你们这样反正是违法的，太欺负人了。",
        "hp": 100,
    },
    {
        "name": "C 有内容但没接话（自说自话）",
        "opponent_line": "试用期工资按50%给，是行业惯例。小王同意了，我们也没强迫他。",
        "user": "我觉得企业应该承担社会责任，尊重劳动者的合法权益，这才是长远发展之道。",
        "hp": 40,
    },
]


def press(hp):
    if hp <= 30:
        return PRESSURE["desperate"]
    if hp <= 60:
        return PRESSURE["corner"]
    return PRESSURE["calm"]


def call(prompt, case, hp_for_prompt):
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    user = (
        f"案件：第一章：入职第一天的陷阱\n"
        f"你扮演的对手：公司HR\n"
        f"对手当前血量：{case['hp']}\n"
        f"对手当前狡辩：{case['opponent_line']}\n"
        f"玩家持有并选择的证据：\n- 自愿放弃社保声明: 一张要求员工自愿放弃社保的声明书。\n"
        f"玩家发言：{case['user']}"
    )
    resp = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": user},
        ],
        temperature=0.2,
        max_tokens=200,
    )
    raw = (resp.choices[0].message.content or "").strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw}


if not DEEPSEEK_API_KEY:
    sys.exit("没有 API key")

for case in CASES:
    print("=" * 70)
    print(case["name"], "| 对手血量", case["hp"])
    print("玩家:", case["user"])
    print("-" * 70)
    old = call(OLD_PROMPT, case, None)
    print("[旧] valid=%s type=%s dmg=%s" % (old.get("valid"), old.get("type"), old.get("damage")))
    print("[旧] 对手说:", old.get("reply") or old.get("_raw"))
    new_p = NEW_PROMPT_TMPL.format(opponent_name="公司HR", pressure=press(case["hp"]))
    new = call(new_p, case, case["hp"])
    print("[新] valid=%s type=%s dmg=%s" % (new.get("valid"), new.get("type"), new.get("damage")))
    print("[新] 对手说:", new.get("reply") or new.get("_raw"))
    print()
