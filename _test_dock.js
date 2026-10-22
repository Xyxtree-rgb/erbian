/**
 * 验证渡口机制改造（方案 A）：骰子只决定水多急，不决定输赢。
 *   1. 摇出的水势决定对手血量（easy 24 / flat 30 / hard 39）
 *   2. 三张话术只给四成伤害
 *   3. 光靠点三张卡**打不死**他 —— 必须自己写，这点是设计意图
 */
const CDP_PORT = Number(process.argv[2] || 9334);
const APP_URL = process.env.APP_URL || "http://127.0.0.1:5001/index.html";
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

let ws = null;
let nextId = 1;
const pending = new Map();

async function connect() {
  const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
  const page = list.find((t) => t.type === "page");
  if (!page) throw new Error("找不到 page target");
  ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    ws.onopen = resolve;
    ws.onerror = (e) => reject(new Error("CDP 连接失败: " + e.message));
  });
  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) reject(new Error(msg.error.message));
      else resolve(msg.result);
    }
  };
}

function send(method, params = {}) {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
}

async function evaluate(expression) {
  const res = await send("Runtime.evaluate", {
    expression, awaitPromise: true, returnByValue: true, userGesture: true
  });
  if (res.exceptionDetails) {
    throw new Error("页面异常: " + (res.exceptionDetails.exception?.description || res.exceptionDetails.text));
  }
  return res.result.value;
}

async function waitFor(expression, timeout = 25000, interval = 200) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeout) {
    if (await evaluate(expression)) return true;
    await sleep(interval);
  }
  throw new Error("等待超时: " + expression);
}

const log = (...a) => console.log(...a);
const hpText = () => evaluate(`(() => {
  const el = document.querySelector(".dock-hp-label");
  return el ? el.textContent.trim() : "";
})()`);
const hpNum = async () => {
  const t = await hpText();
  const m = t.match(/还剩\s*(\d+)/);
  return m ? Number(m[1]) : NaN;
};

let pass = 0, fail = 0;
function check(name, cond, extra = "") {
  if (cond) { pass++; log(`  PASS  ${name}`); }
  else { fail++; log(`  FAIL  ${name} ${extra}`); }
}

async function runCase(forceRandom, expectTide, expectMaxHp) {
  log(`\n--- 骰子随机值 ${forceRandom}（预期 ${expectTide}，血量 ${expectMaxHp}）---`);
  await evaluate(`(() => {
    if (!window.__origRandom) window.__origRandom = Math.random;
    window.__forceRandom = ${forceRandom};
    Math.random = function () { return window.__forceRandom; };
    window.openDock("d_001");
    return true;
  })()`);
  await waitFor(`document.querySelector(".dock-roll") !== null`, 20000);
  log("  摇骰前（meet 阶段）:", await hpText());

  await evaluate(`document.getElementById("dockDice").click(), true`);
  await waitFor(`document.querySelector(".dock-choices") !== null`, 20000);
  const maxHp = await hpNum();
  log("  摇骰后（cards 阶段）血量:", maxHp, "| 文案:", await evaluate(`document.querySelector(".dock-pick").textContent`));
  check(`${expectTide} 水量对应 ${expectMaxHp} 血`, maxHp === expectMaxHp, `实际 ${maxHp}`);

  // 把三张话术全点一遍
  for (let i = 0; i < 3; i++) {
    await evaluate(`(() => {
      const btns = document.querySelectorAll(".dock-choice");
      for (const b of btns) if (!b.disabled) { b.click(); return true; }
      return false;
    })()`);
    await sleep(350);
  }
  const left = await hpNum();
  const dealt = maxHp - left;
  log(`  三张卡共打出 ${dealt} 点，对手剩 ${left}`);
  check("三选一只给约四成伤害（18 点）", dealt === 18, `实际 ${dealt}`);
  check("光点卡打不死他（必须自己写）", left > 0, `剩 ${left}`);
  check("提示改成引导自己写", /自己/.test(await evaluate(`document.querySelector(".dock-pick").textContent`)));
}

async function main() {
  await connect();
  await send("Page.enable");
  await send("Runtime.enable");
  await send("Page.navigate", { url: APP_URL });
  await waitFor(`document.readyState === "complete"`, 30000);
  await sleep(1500);

  await runCase(0.05, "easy 顺水", 24);
  await runCase(0.95, "hard 逆水", 39);

  log("\n--- 平水 ---");
  await evaluate(`window.__forceRandom = 0.55; true`);
  await runCase(0.55, "flat 平水", 30);

  log(`\n结果：${pass} 通过 / ${fail} 失败`);
  if (fail) process.exit(1);
}

main().catch((e) => { console.error("FAIL:", e.message); process.exit(1); });
