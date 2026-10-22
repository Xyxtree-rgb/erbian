/**
 * 冒烟：验证「取证闸门兜底」是否真的会在 25 秒后放行。
 * 走法：进第一章 → 把剧情点到完 → 一个证据都不点 → 干等，看按钮是否解锁。
 */
const CDP_PORT = Number(process.argv[2] || 9333);
const APP_URL = process.env.APP_URL || "http://127.0.0.1:5001/index.html";
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

let ws = null;
let nextId = 1;
const pending = new Map();

async function connect() {
  const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
  const page = list.find((t) => t.type === "page");
  if (!page) throw new Error("找不到可用的 page target");
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
    const detail = res.exceptionDetails.exception?.description || res.exceptionDetails.text;
    throw new Error("页面执行异常: " + detail);
  }
  return res.result.value;
}

async function waitFor(expression, timeout = 30000, interval = 200) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeout) {
    if (await evaluate(expression)) return true;
    await sleep(interval);
  }
  throw new Error("等待超时: " + expression);
}

const log = (...args) => console.log(...args);
const gateState = () => evaluate(`JSON.stringify({
  btn: document.getElementById("toDebate").textContent,
  disabled: document.getElementById("toDebate").disabled,
  count: document.getElementById("evidenceCount").textContent,
  nudge: document.querySelectorAll(".story-hotspot.nudge").length,
  shown: document.querySelectorAll(".story-hotspot:not([hidden])").length
})`);

async function main() {
  await connect();
  await send("Page.enable");
  await send("Runtime.enable");
  await send("Page.navigate", { url: APP_URL });
  await waitFor(`document.readyState === "complete"`, 30000);
  await sleep(1500);
  await evaluate(`window.__pageErrors = []; window.addEventListener("error", (e) => window.__pageErrors.push(String(e.message))); true`);

  await evaluate(`window.launchStoryCases()`);
  await waitFor(`document.querySelectorAll("#storyCaseList .story-chapter").length >= 3`, 20000);
  await evaluate(`document.querySelectorAll("#storyCaseList .story-chapter")[0].click(), true`);
  await sleep(4000);
  log("DIAG storyData:", await evaluate(`JSON.stringify({has: !!window.storyData, keys: window.storyData ? Object.keys(window.storyData) : null})`));
  log("DIAG errors:", await evaluate(`JSON.stringify(window.__pageErrors || [])`));
  log("DIAG sections:", await evaluate(`JSON.stringify(Array.from(document.querySelectorAll("section")).map((s) => s.id + ":" + (s.offsetParent !== null)))`));
  log("DIAG cases:", await evaluate(`JSON.stringify(Array.from(document.querySelectorAll("#storyCaseList .story-chapter strong")).map((e) => e.textContent))`));
  await waitFor(`window.storyData && window.storyData.evidence && window.storyData.evidence.length > 0`, 20000);
  log("章节:", await evaluate(`window.storyData.title`), "| 证据数:", await evaluate(`window.storyData.evidence.length`));

  for (let i = 0; i < 15; i++) {
    const hint = await evaluate(`document.getElementById("dialogueHint").textContent`);
    if (/疑点|证据齐了/.test(hint)) break;
    await evaluate(`document.getElementById("dialogueBox").click(), true`);
    await sleep(600);
  }
  log("播完后的提示:", await evaluate(`document.getElementById("dialogueHint").textContent`));
  log("[0 秒] 闸门:", await gateState());

  log("--- 一个证据都不点，干等 14 秒 ---");
  await sleep(14000);
  log("[14 秒] 闸门:", await gateState());

  log("--- 继续等到 27 秒 ---");
  await sleep(13000);
  log("[27 秒] 闸门:", await gateState());

  await evaluate(`document.getElementById("toDebate").click(), true`);
  await sleep(2000);
  log("点击后 →", await evaluate(`JSON.stringify({
    debateShown: document.getElementById("debateView").offsetParent !== null,
    collected: document.querySelectorAll(".story-hotspot.collected").length,
    heading: (document.getElementById("debateHeading") || {}).textContent,
    evidenceChips: document.querySelectorAll("#debateEvidence button, #debateEvidence .debate-evidence").length
  })`));
  log("页面报错:", await evaluate(`JSON.stringify(window.__pageErrors || [])`));
}

main().catch((e) => { console.error("FAIL:", e.message); process.exit(1); });
