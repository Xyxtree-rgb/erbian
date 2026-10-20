# -*- coding: utf-8 -*-
"""本地自检：造数据 -> 渲染后台页 -> 断言关键元素 -> 存 HTML 预览。"""
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

TMP_DB = os.path.join(tempfile.gettempdir(), "erbian_fb_test.db")
if os.path.exists(TMP_DB):
    os.remove(TMP_DB)
os.environ["ERBIAN_STATS_DB"] = TMP_DB
os.environ["FEEDBACK_TOKEN"] = "test123"
os.environ["DEEPSEEK_API_KEY"] = "sk-test"

from app import create_app  # noqa: E402
from services import stats  # noqa: E402

LONG_U = (
    "试用期不是白用期，公司理应为其缴纳社保。小王是否签了自愿放弃声明，需要公司双方的"
    "签字盖章。试用期给的金额应当为正式员工的80%，大家都这么签，说明严重违法，涉案公司"
    "将面临更大的风险。法律是法律，行业行情怎么样也压不过法律红线。另外根据《社会保险法》"
    "第五十八条，用人单位应当自用工之日起三十日内为其职工申请办理社会保险登记，未办理的"
    "由社会保险经办机构核定其应当缴纳的社会保险费。这一点不因劳动者是否同意而改变。"
    "如果公司以「自愿放弃」为由拒绝补缴，劳动者可以向社保经办机构投诉，也可以申请劳动仲裁。"
    "本案里小王保留了入职通知书、工资流水和聊天记录，足以证明用工事实存在；公司主张的"
    "「行业惯例」在《劳动合同法》第二十六条面前不成立，因为免除法定责任的约定无效。"
)

PROMPT = "试用期工资按50%给，是行业惯例。小王同意了，我们也没强迫他。"
REPLY = "行业惯例就是行业惯例，你们不给够，谁还愿意来上班？"

for i in range(8):
    stats.log_turn(
        mode="story",
        user_text=LONG_U if i % 3 else "试用期也该缴社保，这是法定义务。",
        ai_prompt=PROMPT,
        ai_text=REPLY,
        ai_name="公司HR",
        question="第一章：入职第一天的陷阱",
        valid=True,
        dtype="法理",
        damage=18 + i,
        reason="引用《社会保险法》第五十八条，构成有效法理反驳。",
        ip="117.151.29.142",
        ua="Mozilla/5.0 (iPhone)",
        sid="s_test_%d" % (i % 3),
        elapsed_ms=900,
    )

# 旧数据兼容：改动之前入库的记录把「靶子」存在 ai_text 里，没有 ai_prompt
stats.log_turn(
    mode="story",
    user_text="行业惯例是否有证据支撑？用人单位有义务遵循《劳动法》规定支付劳动者相应的酬劳。",
    ai_text=PROMPT,
    question="第一章：入职第一天的陷阱",
    valid=True,
    dtype="法理",
    damage=22,
    reason="追问证据来源，属于有效法理反驳。",
    ip="117.151.29.142",
    sid="s_old",
    elapsed_ms=880,
)

# 交锋里的桃弁自述回合：玩家没开口，判定的是桃弁这句话
stats.log_turn(
    mode="solo",
    ai_text="格式条款如果不提示说明，就不产生效力，这是常识。",
    ai_name="桃弁",
    question="商家格式条款是否有效",
    valid=True,
    dtype="法理",
    damage=15,
    reason="引用了《民法典》第四百九十六条。",
    ip="117.151.29.142",
    sid="s_test_0",
    elapsed_ms=1600,
)

stats.log_turn(
    mode="judge",
    user_text="因为商家虚假宣传，所以应当退一赔三。",
    ai_text="",
    question="《消费者权益保护法》第五十五条",
    valid=False,
    dtype="情绪",
    damage=0,
    reason="只是结论，没有说明为什么构成欺诈。",
    ip="14.24.128.92",
    sid="s_x",
    elapsed_ms=1100,
)

for ev, det in (("home_open", ""), ("mode_select", "case"), ("mode_case", ""), ("story_enter", "")):
    stats.log_beat(ev, det, "117.151.29.142", "Mozilla/5.0 (iPhone)", "s_test_0")

app = create_app()
c = app.test_client()

r = c.get("/admin/feedback?k=test123")
assert r.status_code == 200, r.status_code
html = r.get_data(as_text=True)
print("len:", len(html))

checks = {
    "有你标签": '<b class="who me">你</b>' in html,
    "有桃弁标签": '<b class="who ai">桃弁</b>' in html,
    "有对手标签": '<b class="who npc">公司HR</b>' in html,
    "有靶子标注": "对手先说的 · 你反驳的就是这句" in html,
    "有回击标注": "回击" in html,
    "有展开全文": "展开全文（共" in html,
    "不再用旧格式": "桃弁：" not in html,
    "有搜索框": 'name="q"' in html,
    "有条数选择": 'name="n"' in html,
    "默认60选中": '<option value="60" selected>' in html,
    "标题动态": "最新 60 条" in html,
    "有说明": "只记录「已经出过判定」的发言" in html,
    "无残留占位符": "@" not in re.sub(r"@media|@keyframes|@[\w-]+\.", "", html),
    "KPI 数字带标记": 'data-k="total"' in html and 'data-k="avg"' in html,
    "宽屏栅格": 'class="grid g3"' in html and 'class="grid g2"' in html,
    "趋势图 7 根柱": html.count('class="bar"') == 7,
    "漏斗 4 级": html.count('class="flow-row"') == 4,
    "模式条存在": 'class="mrow"' in html,
    "柱高变量": '--h:' in html,
    "实时状态位": 'id="liveTxt"' in html,
    "轮询脚本": "/admin/feedback/live?k=" in html,
}
for k, v in checks.items():
    print(("  OK  " if v else "  FAIL") + "  " + k)
    assert v, k

# 时序：对手靶子 -> 你 -> 对手回击，必须在同一张卡里按这个顺序出现
# （列表按 id 倒序，最新的是 solo 卡，所以要回退到含「靶子」的那张剧情卡）
_j = html.find('class="say say-prompt"')
_start = html.rfind('<div class="turn', 0, _j)
card = html[_start : _start + 2000]
i_p = card.find('class="say say-prompt"')
i_u = card.find('class="say say-me"')
i_a = card.find('class="say say-ai"')
print("  顺序索引 prompt/me/ai:", i_p, i_u, i_a)
assert -1 < i_p < i_u < i_a, "卡片时序不对：靶子应排在玩家发言之前"
print("  OK    卡片时序：靶子 → 你的发言 → 对手回击")

# solo 卡片：标注「自述 · 判定的是这句」，且不伪装成玩家的发言
solo_i = html.find('class="turn is-solo"')
assert solo_i > 0, "没渲染出 solo 卡"
solo_end = html.find('<div class="turn"', solo_i + 10)
solo_card = html[solo_i:solo_end]
assert "自述 · 判定的是这句" in solo_card, "solo 卡没标自述"
assert "判定桃弁这句：" in solo_card, "solo 卡没标判定对象"
assert '<b class="who me">你</b>' not in solo_card, "solo 卡不该出现「你」"
print("  OK    solo 卡片标注了判定对象（长 %d 字符）" % len(solo_card))

# 实时 JSON 接口
rl = c.get("/admin/feedback/live?k=test123")
assert rl.status_code == 200, rl.status_code
live = rl.get_json()
assert live["total_turns"] == 10, live["total_turns"]
assert live["ai_turns"] == 1, live["ai_turns"]
assert len(live["daily"]) == 7, live["daily"]
assert live["avg_ms"] > 0, live["avg_ms"]
assert "db" not in live, "live 不该暴露库路径"
assert live["daily"][-1]["n"] == 10, live["daily"]
print("  OK    live 接口: total=%s ai=%s avg_ms=%s daily_last=%s"
      % (live["total_turns"], live["ai_turns"], live["avg_ms"], live["daily"][-1]))
assert c.get("/admin/feedback/live?k=wrong").status_code == 403
print("  OK    live 口令拦截")

# 搜索
r2 = c.get("/admin/feedback?k=test123&q=" + "社保")
h2 = r2.get_data(as_text=True)
print("search hits:", h2.count('<div class="turn">'))
assert "没有匹配" not in h2

r3 = c.get("/admin/feedback?k=test123&q=不存在的词xyz")
h3 = r3.get_data(as_text=True)
assert "没有匹配「不存在的词xyz」的发言" in h3, "空结果提示缺失"

# 条数
r4 = c.get("/admin/feedback?k=test123&n=300")
h4 = r4.get_data(as_text=True)
assert '<option value="300" selected>' in h4
assert "最新 300 条" in h4

# 口令
r5 = c.get("/admin/feedback?k=wrong")
assert r5.status_code == 403

out = os.path.join(HERE, "_fb_preview.html")
open(out, "w", encoding="utf-8").write(html)
print("saved:", out)
print("ALL PASS")
