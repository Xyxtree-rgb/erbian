/**
 * 实跑脚本（临时验证用）：用 Chrome CDP 驱动 index.html，完整打一遍指定章节，
 * 记录每轮出示线索前后的对手血量与 /api/judge 的请求/响应，用于验证
 * 「线索模式非最后一轮血量不低于 1」这道下限，以及第一、二章未被影响。
 *
 * 用法：node _test_hpfloor.js <cdpPort> <caseIndex> <maxRounds> [rebuttalSeed]
 *   caseIndex：案件列表里第几张卡（0=第一章 ch_002，1=第二章 ch_003，2=第三章 ch_004）
 */
const CDP_PORT = Number(process.argv[2] || 9333);
const CASE_INDEX = Number(process.argv[3] || 2);
const MAX_ROUNDS = Number(process.argv[4] || 3);

const APP_URL = process.env.APP_URL || "http://127.0.0.1:5377/index.html";
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
    expression,
    awaitPromise: true,
    returnByValue: true,
    userGesture: true
  });
  if (res.exceptionDetails) {
    const detail = res.exceptionDetails.exception?.description || res.exceptionDetails.text;
    throw new Error("页面执行异常: " + detail);
  }
  return res.result.value;
}

async function waitFor(expression, timeout = 40000, interval = 200) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeout) {
    if (await evaluate(expression)) return true;
    await sleep(interval);
  }
  throw new Error("等待超时: " + expression);
}

const log = (...args) => console.log(...args);

async function readHp() {
  return evaluate(`Number(document.getElementById("opponentHp").textContent)`);
}

async function main() {
  await connect();
  await send("Page.enable");
  await send("Runtime.enable");
  await send("Page.navigate", { url: APP_URL });
  await waitFor(`document.readyState === "complete"`, 30000);
  await sleep(1200);

  // 埋点：记录每一次 /api/judge 的请求体与响应体，顺便收集页面报错。
  // MOCK_JUDGE=1 时在页面内把 /api/judge 换成一个确定性的假裁判：
  // 一直返回 damage=30 / opponent_hp=0，用来逼出「非最后一轮不许打死」这条下限。
  const mockJudge = process.env.MOCK_JUDGE === "1";
  await evaluate(`(() => {
    window.__judgeCalls = [];
    window.__pageErrors = [];
    window.__mockJudge = ${mockJudge ? "true" : "false"};
    if (!window.__fetchHooked) {
      const origin = window.fetch.bind(window);
      window.fetch = function (input, init) {
        const url = typeof input === "string" ? input : (input && input.url) || "";
        const isJudge = url.includes("/api/judge");
        const entry = { url, at: Date.now() };
        if (isJudge) {
          try { entry.req = JSON.parse(init && init.body ? init.body : "{}"); } catch (e) { entry.req = null; }
          window.__judgeCalls.push(entry);
          if (window.__mockJudge) {
            const body = {
              valid: true,
              type: "法理",
              damage: 30,
              reason: "模拟判定：他抓住你没说透的那半句反打",
              reply: "模拟回话：这套说法我先记下，可考核确认那一步你还没解释。",
              opponent_hp: 0,
              max_hp: 100
            };
            entry.res = body;
            entry.status = 200;
            entry.mocked = true;
            return new Promise((resolve) => setTimeout(() => resolve(new Response(
              JSON.stringify(body),
              { status: 200, headers: { "Content-Type": "application/json" } }
            )), 300));
          }
        }
        return origin(input, init).then((res) => {
          if (isJudge) {
            const copy = res.clone();
            copy.json().then((data) => { entry.res = data; entry.status = res.status; }).catch(() => {});
          }
          return res;
        });
      };
      window.__fetchHooked = true;
      window.addEventListener("error", (e) => window.__pageErrors.push(String(e.message)));
    }
    return true;
  })()`);
  if (mockJudge) log("!! 使用假裁判（damage=30 / opponent_hp=0），用于验证血量下限");

  // 进入案件列表
  await evaluate(`window.launchStoryCases()`);
  await waitFor(`document.querySelectorAll("#storyCaseList .story-chapter").length >= 3`, 20000);
  const caseTitles = await evaluate(`Array.from(document.querySelectorAll("#storyCaseList .story-chapter strong")).map(el => el.textContent)`);
  log("案件列表:", JSON.stringify(caseTitles));

  const expectedChapter = process.argv[5] || "";
  // 点卡片之前先把上一份剧情从 window 上摘掉，否则会读到旧的 storyData 以为已经加载完
  await evaluate(`(() => {
    const current = window.storyData && window.storyData.chapter_id;
    if (${JSON.stringify(expectedChapter)} && current !== ${JSON.stringify(expectedChapter)}) {
      window.storyData = undefined;
    }
    return current || null;
  })()`);
  await evaluate(`document.querySelectorAll("#storyCaseList .story-chapter")[${CASE_INDEX}].click(), true`);
  await waitFor(`(() => {
    const s = window.storyData;
    if (!s || !s.opponent || !Array.isArray(s.evidence)) return false;
    if (${JSON.stringify(expectedChapter)}) return s.chapter_id === ${JSON.stringify(expectedChapter)};
    return true;
  })()`, 25000, 250);
  await waitFor(`document.getElementById("exploreStoryTitle").textContent.length > 0`, 10000, 250);
  const meta = await evaluate(`(() => {
    const s = window.storyData;
    return { chapter: s.chapter_id, clue: Boolean(s.opponent && s.opponent.clue_mode === true),
             hp: s.opponent.hp, rounds: s.opponent.rounds.length,
             evidence: s.evidence.map(e => e.id),
             best: s.opponent.rounds.map(r => r.best_evidence || null),
             dmg: s.opponent.rounds.map(r => r.damage || 0) };
  })()`);
  log("进入章节:", JSON.stringify(meta));

  // 非线索模式要先收集全部证据才能进辩论
  if (!meta.clue) {
    await evaluate(`Promise.all(window.storyData.evidence.map((item) => {
      const el = document.querySelector('.story-hotspot[data-evidence="' + item.id + '"]');
      if (el) el.click();
      return Promise.resolve();
    }))`);
    await sleep(600);
    const collected = await evaluate(`document.querySelectorAll(".story-hotspot.collected").length`);
    log("已收集证据数:", collected, "/", meta.evidence.length);
  }

  await evaluate(`document.getElementById("toDebate").click(), true`);
  if (meta.clue) {
    await waitFor(`!document.getElementById("cluePanel").hidden`, 15000);
  } else {
    await waitFor(`document.getElementById("debateInput") && document.getElementById("submitArgument").textContent.length > 0`, 15000);
  }
  await sleep(300);

  // SHIELD_TEST=1：连递三张错线索，验证护盾 +10 / 满 30 判负这套机制没被动过
  if (process.env.SHIELD_TEST === "1") {
    const wrongClues = await evaluate(`(() => {
      const best = ${JSON.stringify(meta.best && meta.best[0] ? meta.best[0] : "")};
      return Array.from(document.querySelectorAll(".clue-card"))
        .filter(el => !el.disabled && el.dataset.clue !== best)
        .map(el => el.dataset.clue);
    })()`);
    log("错递候选:", JSON.stringify(wrongClues));
    let last = null;
    for (let i = 0; i < 3; i += 1) {
      const clueId = wrongClues[i] || wrongClues[0];
      await evaluate(`document.querySelector('.clue-card[data-clue="${clueId}"]').click(), true`);
      await sleep(1400);
      last = await evaluate(`(() => ({
        shield: document.getElementById("shieldValue").textContent,
        badgeHidden: document.getElementById("shieldBadge").hidden,
        hp: Number(document.getElementById("opponentHp").textContent),
        status: document.getElementById("clueStatus").textContent,
        feedback: document.getElementById("debateFeedback").textContent,
        inputDisabled: document.getElementById("debateInput").disabled,
        failed: document.querySelector(".debate-panel").classList.contains("is-clue-failed"),
        closingHidden: document.getElementById("debateClosing").hidden,
        closingTitle: document.getElementById("debateClosingTitle").textContent
      }))()`);
      log(`错递 ${i + 1}（${clueId}）:`, JSON.stringify(last));
    }
    log("护盾结论:", JSON.stringify(last));
    ws.close();
    return;
  }

  const trace = [];
  let hp = await readHp();
  log("初始血量:", hp);
  trace.push({ step: "start", hp });

  for (let roundNo = 0; roundNo < MAX_ROUNDS; roundNo += 1) {
    const state = await evaluate(`(() => ({
      hp: Number(document.getElementById("opponentHp").textContent),
      closing: !document.getElementById("debateClosing").hidden,
      closingTitle: document.getElementById("debateClosingTitle").textContent,
      clueStatus: document.getElementById("clueStatus").textContent,
      submitText: document.getElementById("submitArgument").textContent,
      inputDisabled: document.getElementById("debateInput").disabled,
      cards: Array.from(document.querySelectorAll(".clue-card")).map(el => ({
        id: el.dataset.clue, disabled: el.disabled, state: el.dataset.state || "", hit: el.classList.contains("is-hit")
      }))
    }))()`);
    log(`--- 第 ${roundNo} 轮开打前 ---`, JSON.stringify(state));
    trace.push({ step: `round${roundNo}-before`, hp: state.hp, submit: state.submitText });

    if (state.closing || state.submitText === "对手已破防" || state.submitText === "这一局输了") {
      log("已结束，停止推进");
      break;
    }

    if (meta.clue) {
      const usable = state.cards.filter((card) => !card.disabled);
      log("可出示线索:", JSON.stringify(usable.map((c) => c.id)));
      if (!usable.length) {
        log("没有可出示的线索，停止");
        break;
      }

      // 优先递当轮 best_evidence（第三章三轮的正确答案依次是 ev_03 / ev_05 / ev_06）
      const wanted = meta.best && meta.best[roundNo] ? meta.best[roundNo] : usable[0].id;
      const pick = usable.some((c) => c.id === wanted) ? wanted : usable[0].id;
      log(`出示线索: ${pick}`);
      await evaluate(`document.querySelector('.clue-card[data-clue="${pick}"]').click(), true`);
      await sleep(900);
      hp = await readHp();
      log(`出示后血量: ${hp}`, "| 状态:", await evaluate(`document.getElementById("clueStatus").textContent`));
      trace.push({ step: `round${roundNo}-afterClue`, clue: pick, hp });
    } else {
      log("非线索模式：本轮直接发言");
      trace.push({ step: `round${roundNo}-noCluePhase`, hp });
    }

    const canType = !(await evaluate(`document.getElementById("debateInput").disabled`));
    if (!canType) {
      log("输入框未解锁（血量为 0 或已判负），本轮到此结束");
      const closing = await evaluate(`({ hidden: document.getElementById("debateClosing").hidden,
        title: document.getElementById("debateClosingTitle").textContent,
        text: document.getElementById("debateClosingText").textContent.slice(0, 60) })`);
      log("结案卡片:", JSON.stringify(closing));
      trace.push({ step: `round${roundNo}-noinput`, hp, closing });
      break;
    }

    const rebuttals = [
      "你说的口径是考核上限，可这份记录写明我实际考核达标，按系数 3.5 计算，差额必须补",
      "仲裁裁决里说得很清楚，奖金发放属于劳动争议受案范围，公司不能拿内部制度挡我",
      "所以既然制度要公示才生效，这份没公示的制度对我没有约束力，你凭什么拿来扣我钱"
    ];
    const text = rebuttals[roundNo % rebuttals.length];
    await evaluate(`(() => {
      const input = document.getElementById("debateInput");
      input.value = ${JSON.stringify(text)};
      input.dispatchEvent(new Event("input", { bubbles: true }));
      return true;
    })()`);

    const callsBefore = await evaluate(`window.__judgeCalls.length`);
    await evaluate(`document.getElementById("submitArgument").click(), true`);
    await waitFor(`window.__judgeCalls.length > ${callsBefore} && window.__judgeCalls[${callsBefore}].res`, 60000, 300);
    await sleep(1200);
    const call = await evaluate(`window.__judgeCalls[${callsBefore}]`);
    hp = await readHp();
    log("判定请求 round_index/current_hp:", call.req.round_index, "/", call.req.current_hp);
    log("判定返回:", JSON.stringify({ valid: call.res.valid, damage: call.res.damage, opponent_hp: call.res.opponent_hp }));
    log(`判定后血量: ${hp}`);
    trace.push({ step: `round${roundNo}-afterJudge`, hp, judgeDamage: call.res.damage, serverHp: call.res.opponent_hp });
  }

  await sleep(800);
  const finalState = await evaluate(`(() => ({
    hp: Number(document.getElementById("opponentHp").textContent),
    closingHidden: document.getElementById("debateClosing").hidden,
    closingTitle: document.getElementById("debateClosingTitle").textContent,
    closingText: document.getElementById("debateClosingText").textContent.slice(0, 80),
    submitText: document.getElementById("submitArgument").textContent,
    clueFailed: document.querySelector(".debate-panel").classList.contains("is-clue-failed"),
    errors: window.__pageErrors
  }))()`);
  log("最终状态:", JSON.stringify(finalState));
  log("血量轨迹:", JSON.stringify(trace));
  log("判定次数:", await evaluate(`window.__judgeCalls.length`));

  await evaluate(`(async () => {
    document.title = "DONE";
    return true;
  })()`);
  ws.close();
}

main().then(() => {
  console.log("SCRIPT_OK");
  process.exit(0);
}).catch((error) => {
  console.error("SCRIPT_FAIL:", error.message);
  process.exit(1);
});
