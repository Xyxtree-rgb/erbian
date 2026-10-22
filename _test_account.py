"""账号 + 付费接口冒烟：登录 → 云存档 → 套餐 → 兑换码 → 权益生效。"""
import json
import sys

import requests

BASE = "http://127.0.0.1:5001"
ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name} {extra}")


def post(path, payload, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return requests.post(BASE + path, json=payload, headers=headers, timeout=10)


def get(path, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return requests.get(BASE + path, headers=headers, timeout=10)


print("1) 邀请码登录")
r = post("/api/account/login", {"channel": "invite", "credential": "EBTEST1", "nick": "小林"})
body = r.json()
check("登录成功", r.status_code == 200 and body.get("ok"), body)
token = body.get("token", "")
check("拿到 token", bool(token))
check("标记为新用户", body.get("is_new") is True)
check("初始权益是 free", body.get("entitlement", {}).get("tier") == "free", body.get("entitlement"))

print("2) 错误邀请码应被拒绝")
r = post("/api/account/login", {"channel": "invite", "credential": "NOTEXIST"})
check("无效码返回 400", r.status_code == 400, r.text[:80])

print("3) 未登录访问受保护接口")
check("me 未登录 401", get("/api/account/me").status_code == 401)
check("save 未登录 401", get("/api/account/save").status_code == 401)

print("4) 云存档读写")
r = post("/api/account/save", {"scope": "voyage", "payload": '{"cleared":["d_001"]}'}, token)
check("写入成功", r.status_code == 200 and r.json().get("ok"), r.text[:80])
r = get("/api/account/save?scope=voyage", token)
check("读回内容一致", r.json().get("saves", {}).get("voyage") == '{"cleared":["d_001"]}', r.text[:120])
r = get("/api/account/save", token)
check("不带 scope 读全部", "voyage" in r.json().get("saves", {}), r.text[:120])

print("5) 套餐列表")
r = get("/api/billing/plans")
plans = r.json().get("plans", [])
check("返回 3 个套餐", len(plans) == 3, r.text[:120])
check("月卡 38 元", any(p["code"] == "monthly" and p["price_yuan"] == 38 for p in plans))
check("支付开关默认关闭", r.json().get("pay_enabled") is False)

print("6) 下单（当前不该产生收款动作）")
r = post("/api/billing/order", {"plan_code": "monthly"}, token)
body = r.json()
check("下单返回 ok", r.status_code == 200 and body.get("ok"), r.text[:120])
check("状态是 pending_off", body.get("status") == "pending_off", body)
check("没有支付链接", body.get("pay_url") is None, body)
check("提示内测期不收款", "内测" in (body.get("note") or ""), body)

print("7) 兑换码开通")
sys.path.insert(0, ".")
from services import billing  # noqa: E402

codes = billing.generate_licenses("monthly", 1, note="内测")
r = post("/api/license/redeem", {"code": codes[0]}, token)
body = r.json()
check("兑换成功", r.status_code == 200 and body.get("ok"), r.text[:120])
check("权益变为 monthly", body.get("entitlement", {}).get("tier") == "monthly", body)
check("剩余天数 29-30", body.get("entitlement", {}).get("days_left") in (29, 30), body)

print("8) 同一码不能重用")
r = post("/api/license/redeem", {"code": codes[0]}, token)
check("重复使用被拒", r.status_code == 400, r.text[:80])

print("9) 重新登录后权益仍在")
r = post("/api/account/login", {"channel": "invite", "credential": "EBTEST1"})
body = r.json()
check("老用户登录不新建", body.get("is_new") is False)
check("权益保留 monthly", body.get("entitlement", {}).get("tier") == "monthly", body)

print("10) 退出登录")
r = post("/api/account/logout", {}, token)
check("登出成功", r.status_code == 200)
check("token 失效", get("/api/account/me", token).status_code == 401)

print(f"\n结果：{ok} 通过 / {fail} 失败")
sys.exit(1 if fail else 0)
