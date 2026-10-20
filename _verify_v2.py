# -*- coding: utf-8 -*-
"""验证改后的 debate_argument：不报错、type 合法、reply 会反打"""
import sys

from services.deepseek import debate_argument, _pressure_note

EVIDENCE = [{"name": "自愿放弃社保声明", "description": "一张要求员工自愿放弃社保的声明书。"}]

CASES = [
    ("A 强发言", "试用期本来就不算正式员工，不交社保很正常。再说了，小王自己签了自愿放弃声明，这能算违法吗？",
     "试用期也是劳动关系，按《社会保险法》第五十八条，用工之日起三十日内必须缴社保。自愿放弃声明违反法律强制性规定，本身就是无效的。", 100),
    ("B 空话", "试用期工资按50%给，是行业惯例。小王同意了，我们也没强迫他。",
     "你们这样反正是违法的，太欺负人了。", 100),
    ("C 自说自话", "试用期工资按50%给，是行业惯例。小王同意了，我们也没强迫他。",
     "我觉得企业应该承担社会责任，尊重劳动者的合法权益，这才是长远发展之道。", 40),
    ("D 残血反扑", "试用期工资按50%给，是行业惯例。小王同意了，我们也没强迫他。",
     "《劳动合同法》第二十条规定，试用期工资不得低于本单位相同岗位最低档工资的百分之八十。", 25),
]

for name, opp, user, hp in CASES:
    print("=" * 72)
    print(name, "| hp", hp, "| 压力档:", _pressure_note(hp, 100)[:12], "...")
    try:
        r = debate_argument(
            chapter_title="第一章：入职第一天的陷阱",
            opponent_name="公司HR",
            opponent_line=opp,
            user_input=user,
            evidence=EVIDENCE,
            current_hp=hp,
            max_hp=100,
        )
        print("valid=%s type=%s damage=%s" % (r["valid"], r["type"], r["damage"]))
        print("对手:", r["reply"])
    except Exception as e:
        print("!! 异常:", type(e).__name__, e)
        sys.exit(1)
print("=" * 72)
print("全部通过，没有异常")
