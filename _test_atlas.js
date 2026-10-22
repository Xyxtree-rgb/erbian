/**
 * 验证：穿越转场 + 世界观地图 + 渡口去金色。
 * 同时截几张图，看看实际长什么样。
 */
const CDP_PORT = Number(process.argv[2] || 9336);
const APP_URL = process.env.APP_URL || "http://127.0.0.1:5001/index.html?preview=intro";
const fs = require("fs");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let ws = null, nextId = 1;
const pending = new Map();

async function connect() {
  const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
  const page = list.find((t) => t.type === "page");
  ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = (e) => rej(new Error(e.message)); });
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      const { resolve, reject } = pending.get(m.id);
      pending.delete(m.id);
      m.error ? reject(new Error(m.error.message)) : resolve(m.result);
    }
  };
}
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const id = nextId++;
  pending.set(id, { resolve, reject });
  ws.send(JSON.stringify({ id, method, params }));
});
async function evaluate(expression) {
  const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true, userGesture: true });
  if (r.exceptionDetails) throw new Error("页面异常: " + (r.exceptionDetails.exception?.description || r.exceptionDetails.text));
  return r.result.value;
}
async function waitFor(expr, timeout = 20000, iv = 200) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeout) { if (await evaluate(expr)) return true; await sleep(iv); }
  throw new Error("超时: " + expr);
}
let pass = 0, fail = 0;
const check = (n, c, x = "") => { c ? (pass++, console.log("  PASS  " + n)) : (fail++, console.log("  FAIL  " + n + " " + x)); };

async function shot(name, scale = 1) {
  const r = await send("Page.captureScreenshot", { format: "png" });
  fs.writeFileSync(name, Buffer.from(r.data, "base64"));
  console.log("  截图 -> " + name);
}

async function main() {
  await connect();
  await send("Page.enable");
  await send("Runtime.enable");
  // 清干净：否则上一轮存下的 playerRole 会让 currentRole() 直接跳过选角色
  await send("Storage.clearDataForOrigin", { origin: "http://127.0.0.1:5001", storageTypes: "local_storage,session_storage" });
  await send("Emulation.setDeviceMetricsOverride", { width: 1024, height: 760, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: APP_URL });
  await waitFor(`document.readyState === "complete"`, 30000);
  await sleep(1800);
  await evaluate(`window.__errs=[];window.addEventListener("error",e=>window.__errs.push(String(e.message)));true`);

  console.log("1) 引子页");
  await waitFor(`document.getElementById("voyageIntro").classList.contains("open")`, 15000);
  check("引子页打开", true);
  await shot("_shot1_intro.png");

  console.log("2) 点击上船 → 转场");
  await evaluate(`document.getElementById("voyageIntroGo").click(), true`);
  await sleep(600);
  const orbState = await evaluate(`JSON.stringify({
    on: document.getElementById("crossing").classList.contains("on"),
    orbs: document.querySelectorAll(".crossing-orb").length,
    water: !!document.getElementById("crossWater")
  })`);
  console.log("  转场中:", orbState);
  check("转场层已打开", JSON.parse(orbState).on);
  check("双球存在", JSON.parse(orbState).orbs === 2);
  await shot("_shot2_crossing.png");

  await sleep(500);
  check("logo 已绽开", await evaluate(`document.getElementById("crossLogo").classList.contains("on")`));
  await shot("_shot3_logo.png");

  await sleep(900);
  check("水面已升起", await evaluate(`document.getElementById("crossWater").classList.contains("on")`));
  await shot("_shot4_water.png");

  console.log("3) 转场结束后落到角色选择");
  await sleep(1400);
  check("角色选择打开", await evaluate(`document.getElementById("roleSelect").classList.contains("open")`));

  console.log("4) 选角色 → 确认 → 应落到地图");
  await evaluate(`(() => { const el = document.querySelector(".role-grid button, [data-role]"); if (el) el.click(); return true; })()`);
  await sleep(500);
  const disabled = await evaluate(`document.getElementById("roleConfirm").disabled`);
  console.log("  确认按钮禁用?", disabled);
  await evaluate(`document.getElementById("roleConfirm").click(), true`);
  await sleep(600);
  check("落到航线图", await evaluate(`document.getElementById("atlasMask").classList.contains("open")`));
  await sleep(600);
  const nodes = await evaluate(`document.querySelectorAll(".atlas-node").length`);
  check("地图上有 3 个渡口", nodes === 3, "实际 " + nodes);
  await shot("_shot5_atlas.png");

  console.log("5) 地图交互：拖拽与缩放");
  const before = await evaluate(`document.getElementById("atlasWorld").style.transform`);
  await send("Input.dispatchMouseEvent", { type: "mousePressed", x: 500, y: 400, button: "left", clickCount: 1 });
  await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: 420, y: 340, button: "left" });
  await send("Input.dispatchMouseEvent", { type: "mouseReleased", x: 420, y: 340, button: "left", clickCount: 1 });
  await sleep(300);
  const after = await evaluate(`document.getElementById("atlasWorld").style.transform`);
  check("拖拽改变了位置", before !== after, `before=${before} after=${after}`);

  await evaluate(`document.getElementById("atlasZoomIn").click(), true`);
  await sleep(300);
  check("放大生效", /scale\(1\.[1-9]/.test(await evaluate(`document.getElementById("atlasWorld").style.transform`)));
  await evaluate(`document.getElementById("atlasReset").click(), true`);
  await sleep(300);
  check("复位生效", /scale\(1(?:\.0+)?\)/.test(await evaluate(`document.getElementById("atlasWorld").style.transform`)));
  await shot("_shot6_atlas_zoom.png");

  console.log("6) 点渡口靠岸");
  await evaluate(`document.querySelector('.atlas-node').click(), true`);
  await sleep(1200);
  check("地图已收起", !(await evaluate(`document.getElementById("atlasMask").classList.contains("open")`)));
  check("渡口已打开", await evaluate(`document.getElementById("dock").classList.contains("open")`));
  await shot("_shot7_dock.png");

  console.log("7) 去金色检查");
  const gold = await evaluate(`(() => {
    const s = getComputedStyle(document.documentElement);
    return JSON.stringify({
      gold: s.getPropertyValue("--gold").trim(),
      deep: s.getPropertyValue("--gold-deep").trim(),
      yellowLeft: document.documentElement.outerHTML.includes("#f6c945"),
      brownLeft: document.documentElement.outerHTML.includes("#8a7a58")
    });
  })()`);
  console.log("  变量:", gold);
  const g = JSON.parse(gold);
  check("--gold 已定义为墨色", g.gold !== "");
  check("旧金黄色 #f6c945 已清除", !g.yellowLeft);
  check("旧棕色 #8a7a58 已清除", !g.brownLeft);

  console.log("\n页面报错:", await evaluate(`JSON.stringify(window.__errs||[])`));
  console.log(`结果：${pass} 通过 / ${fail} 失败`);
  process.exit(fail ? 1 : 0);
}
main().catch((e) => { console.error("FAIL:", e.message); process.exit(1); });
