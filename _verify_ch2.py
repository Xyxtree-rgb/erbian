# -*- coding: utf-8 -*-
"""第二章接入自检：只测路由与章节查找，不消耗太多 API 额度。"""
import json
import os
import sys

os.environ.setdefault("FEEDBACK_TOKEN", "test")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app  # noqa: E402

ok = 0
fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name} {extra}")


c = app.test_client()

print("[1] 两个章节文件都能取到")
r1 = c.get("/story.json")
r2 = c.get("/story_ch2.json")
check("GET /story.json 200", r1.status_code == 200, r1.status_code)
check("GET /story_ch2.json 200", r2.status_code == 200, r2.status_code)
d1 = r1.get_json() if r1.status_code == 200 else {}
d2 = r2.get_json() if r2.status_code == 200 else {}
check("第一章 chapter_id=ch_002", d1.get("chapter_id") == "ch_002", d1.get("chapter_id"))
check("第二章 chapter_id=ch_003", d2.get("chapter_id") == "ch_003", d2.get("chapter_id"))
print(f"       第一章：{d1.get('title')} / {len(d1.get('evidence', []))} 证据 / {len(d1.get('opponent', {}).get('rounds', []))} 回合")
print(f"       第二章：{d2.get('title')} / {len(d2.get('evidence', []))} 证据 / {len(d2.get('opponent', {}).get('rounds', []))} 回合")

print("[2] 第二章数据完整性")
ev = d2.get("evidence", [])
rounds = d2.get("opponent", {}).get("rounds", [])
check("证据 3 条", len(ev) == 3, len(ev))
check("证据 id 为 ev_01/02/03", [e.get("id") for e in ev] == ["ev_01", "ev_02", "ev_03"], [e.get("id") for e in ev])
check("每个证据都有 name+description", all(e.get("name") and e.get("description") for e in ev))
check("回合 ≥4", len(rounds) >= 4, len(rounds))
check("每回合都有 opponent_line", all(r.get("opponent_line") for r in rounds))
check("每回合都有 expected_keywords", all(r.get("expected_keywords") for r in rounds))
check("总伤害潜力 ≥100", sum(r.get("damage", 0) for r in rounds) >= 100,
      sum(r.get("damage", 0) for r in rounds))
check("closing 结案陈词存在", bool(d2.get("closing")))

print("[3] 判定接口按章节分流（不发真实请求，只看参数校验）")
bad = c.post("/api/judge", json={"chapter_id": "ch_999", "input": "测试", "round_index": 0, "evidence": []})
check("不存在的章节返回 404", bad.status_code == 404, bad.status_code)
bad_ev = c.post("/api/judge", json={"chapter_id": "ch_003", "input": "测试", "round_index": 0, "evidence": ["ev_99"]})
check("跨章证据被拒 400", bad_ev.status_code == 400, bad_ev.status_code)
bad_round = c.post("/api/judge", json={"chapter_id": "ch_003", "input": "测试", "round_index": 9, "evidence": []})
check("越界回合被拒 400", bad_round.status_code == 400, bad_round.status_code)

print()
print(f"结果：{ok} 通过 / {fail} 失败")
sys.exit(1 if fail else 0)
