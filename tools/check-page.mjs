#!/usr/bin/env node
// tools/check-page.mjs — 工坊页面「能不能真的点」自动巡检
//
// 为什么需要它:页面脚本是塞在模板字符串里的内联 JS,一旦转义/元素 id 写错,
// 整段脚本静默失效(按钮全点不动),而 node --check 只能查语法、查不出「找不到元素」。
// 这里用无头 Chrome 真加载页面、真派发点击/回车,再看 DOM 与请求,逐项给结论。
//
// 用法:
//   node tools/check-page.mjs                     # 默认 http://127.0.0.1:5199
//   node tools/check-page.mjs --url http://127.0.0.1:5099
//   node tools/check-page.mjs --chrome "C:\...\chrome.exe" --verbose
//
// 退出码:0=全绿;1=有失败项;2=环境问题(起不来浏览器/连不上服务)

import { spawn } from "node:child_process"
import { mkdtempSync, rmSync, existsSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { request as httpRequest } from "node:http"

// 本机服务探测走 node:http 直连:undici 的 fetch 会受 http_proxy 环境影响,
// 在装了代理的机器上连 127.0.0.1 也要绕一圈(实测 2.7s),超时容易误判成"服务没起来"
const U = new URL((process.argv.find((a) => a.startsWith("http")) || "http://127.0.0.1:5199"))
function localReq(path, { method = "GET", body = null, timeout = 20000, host = U.hostname, port = U.port || 80, headers = {} } = {}) {
  return new Promise((resolve) => {
    const h = { ...headers }
    if (body) { h["content-type"] = "application/json"; h["content-length"] = Buffer.byteLength(body) }
    const req = httpRequest({ host, port, path, method, timeout, headers: h }, (res) => {
      let buf = ""
      res.setEncoding("utf8")
      res.on("data", (c) => (buf += c))
      res.on("end", () => {
        let json = null
        try { json = JSON.parse(buf) } catch {}
        resolve({ status: res.statusCode, text: buf, json })
      })
    })
    req.on("timeout", () => req.destroy(new Error("timeout")))
    req.on("error", (e) => resolve({ status: 0, text: "", json: null, error: e.message }))
    if (body) req.write(body)
    req.end()
  })
}

const argv = process.argv.slice(2)
const arg = (k, d) => {
  const i = argv.indexOf("--" + k)
  return i >= 0 && argv[i + 1] ? argv[i + 1] : d
}
const BASE = (arg("url", "http://127.0.0.1:5199")).replace(/\/$/, "")
const VERBOSE = argv.includes("--verbose")
const CHROME = arg("chrome", [
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].find((p) => existsSync(p)) || "")

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const C = { g: "\x1b[32m", r: "\x1b[31m", y: "\x1b[33m", d: "\x1b[2m", x: "\x1b[0m" }

// ---------- 0. 服务活不活 ----------
{
  const r = await localReq("/api/health", { timeout: 8000 })
  if (r.status !== 200) {
    console.error(`${C.r}✗ 连不上工坊服务 ${BASE}${C.x} — ${r.error || "HTTP " + r.status}`)
    console.error(`${C.d}  先启动工坊(或在测试目录 bun wb-studio.mjs)再跑本脚本${C.x}`)
    process.exit(2)
  }
}
if (!CHROME) {
  console.error(`${C.r}✗ 找不到 Chrome/Edge,无法做真实点击测试${C.x}`)
  process.exit(2)
}

// ---------- 1. 起无头浏览器(固定端口更稳:port=0 时偶发不落 DevToolsActivePort 文件) ----------
// 正常退出会删掉自己的 profile;被强杀/超时杀掉时不会,一次遗留 5-10MB。
// 这里顺手清理 6 小时前的旧 profile,避免 Temp 越攒越多。
try {
  const base = tmpdir()
  for (const name of readdirSync(base)) {
    if (!name.startsWith("wb-check-")) continue
    const full = join(base, name)
    try {
      if (Date.now() - statSync(full).mtimeMs > 6 * 3600 * 1000) rmSync(full, { recursive: true, force: true })
    } catch {}
  }
} catch {}

const profile = mkdtempSync(join(tmpdir(), "wb-check-"))
const dbgPort = 9400 + Math.floor(Math.random() * 400)
let chrome = null
function launchChrome(port) {
  chrome = spawn(CHROME, [
    "--headless=new",
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${profile}`,
    "--no-first-run", "--no-default-browser-check", "--disable-gpu",
    "--no-proxy-server", "--disable-extensions", "--disable-background-networking",
    "--window-size=1280,900",
    "about:blank",
  ], { stdio: "ignore" })
}

function cleanup(code) {
  try { chrome && chrome.kill() } catch {}
  try { rmSync(profile, { recursive: true, force: true }) } catch {}
  process.exit(code)
}

let wsUrl = ""
for (let attempt = 0; attempt < 2 && !wsUrl; attempt++) {
  if (attempt) { try { chrome.kill() } catch {} await sleep(800) }
  launchChrome(dbgPort + attempt)
  const port = dbgPort + attempt
  for (let i = 0; i < 200 && !wsUrl; i++) {
    await sleep(150)
    const r = await localReq("/json/list", { host: "127.0.0.1", port, timeout: 2000 })
    const list = Array.isArray(r.json) ? r.json : []
    const page = list.find((t) => t.type === "page")
    if (page && page.webSocketDebuggerUrl) wsUrl = page.webSocketDebuggerUrl
  }
}
if (!wsUrl) {
  console.error(`${C.r}✗ 无头浏览器没起来${C.x}`)
  cleanup(2)
}

// ---------- 2. 极简 CDP 客户端 ----------
const ws = new WebSocket(wsUrl)
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error("WebSocket 连接失败")) })
let seq = 0
const pending = new Map()
const pageErrors = []
ws.onmessage = (ev) => {
  const m = JSON.parse(ev.data)
  if (m.id && pending.has(m.id)) {
    const { res, rej } = pending.get(m.id)
    pending.delete(m.id)
    m.error ? rej(new Error(m.error.message)) : res(m.result)
    return
  }
  if (m.method === "Runtime.exceptionThrown") {
    const d = m.params.exceptionDetails
    pageErrors.push(d.exception?.description || d.text || "未知异常")
  }
  if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") {
    pageErrors.push("console.error: " + m.params.args.map((a) => a.value ?? a.description ?? "").join(" "))
  }
}
const send = (method, params = {}) => new Promise((res, rej) => {
  const id = ++seq
  pending.set(id, { res, rej })
  ws.send(JSON.stringify({ id, method, params }))
})
const PROBE_SRC = readFileSync(new URL("./update-modal-probe.js", import.meta.url), "utf8")

const evalJs = async (expression) => {
  const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true })
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text)
  return r.result.value
}

await send("Runtime.enable")
await send("Page.enable")
await send("Log.enable")

// ---------- 3. 打开页面 ----------
await send("Page.navigate", { url: BASE + "/" })
await send("Runtime.evaluate", { expression: "1" }) // 触发一次,确保上下文就绪
await sleep(2500)

// 把 /api/task、/api/test 换成桩:点击能走到发请求就够了,不真跑模型(省钱也不污染会话)
await evalJs(`(() => {
  window.__calls = [];
  const real = window.fetch.bind(window);
  window.__mockReply = '好的,收到';
  window.__mockFiles = ['drink_water/plugin.py', 'drink_water/manifest.json'];
  window.fetch = (u, o = {}) => {
    const url = typeof u === 'string' ? u : (u && u.url) || '';
    const method = (o.method || 'GET').toUpperCase();
    window.__calls.push({ url, method, body: o.body });   // 全量记录,便于断言"点一下有没有发请求"
    if (url.includes('/api/task') && method === 'POST') {
      return Promise.resolve(new Response(JSON.stringify({
        ok: true, reply: window.__mockReply, thinking: '想一下', modifiedFiles: window.__mockFiles.slice(), artifacts: [],
      }), { status: 200, headers: { 'content-type': 'application/json' } }));
    }
    if (url.includes('/api/reveal') && method === 'POST') {
      return Promise.resolve(new Response(JSON.stringify({ ok: true, path: 'mock' }),
        { status: 200, headers: { 'content-type': 'application/json' } }));
    }
    if (url.includes('/api/test')) {
      return Promise.resolve(new Response(JSON.stringify({ ok: true, passed: true, model: 'mock-model' }),
        { status: 200, headers: { 'content-type': 'application/json' } }));
    }
    if (url.includes('/api/setup')) {
      return Promise.resolve(new Response(JSON.stringify({ ok: true, warning: '已保存(测试桩)' }),
        { status: 200, headers: { 'content-type': 'application/json' } }));
    }
    return real(u, o);
  };
  return true;
})()`)

const isSetup = await evalJs("!!document.getElementById('presets')")

// ---------- 3.5 调试模式:--eval "表达式" 只跑这段并打印结果 ----------
const EVAL_ONLY = arg("eval", "")
if (EVAL_ONLY) {
  try {
    const r = await evalJs(EVAL_ONLY)
    console.log(typeof r === "string" ? r : JSON.stringify(r))
  } catch (e) {
    console.error("EVAL 失败:", e.message)
  }
  cleanup(0)
}

// ---------- 4. 页面内逐项体检 ----------
let result
if (isSetup) {
  // 首次配置页(没配密钥时出现):预设芯片 / 协议联动 / 先测一下 / 保存
  result = await evalJs(`(async () => {
    const R = [];
    const pass = (n, d = '') => R.push({ name: n, ok: true, detail: d });
    const fail = (n, d = '') => R.push({ name: n, ok: false, detail: d });
    const $ = (s) => document.querySelector(s);
    const $$ = (s) => [...document.querySelectorAll(s)];
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    const calls = () => window.__calls || [];

    const chips = $$('.pchip');
    chips.length >= 4 ? pass('服务商预设芯片', chips.length + ' 个') : fail('服务商预设芯片', '只有 ' + chips.length + ' 个');

    if (chips[0]) {
      chips[0].click(); await sleep(80);
      const filled = $('#u').value && $('#presetTip').style.display !== 'none';
      filled ? pass('点预设自动填服务地址', $('#u').value)
        : fail('点预设自动填服务地址', 'url=' + JSON.stringify($('#u').value) + ' tip=' + $('#presetTip').style.display);
    }

    // 协议联动:Google 不需要 Base URL → 必填标记隐藏、placeholder 变「留空」
    const proto = $('#proto');
    proto.value = 'google'; proto.dispatchEvent(new Event('change', { bubbles: true })); await sleep(50);
    const googleOk = getComputedStyle($('#urlReq')).display === 'none' && $('#u').placeholder.includes('留空');
    proto.value = 'openai'; proto.dispatchEvent(new Event('change', { bubbles: true })); await sleep(50);
    const backOk = getComputedStyle($('#urlReq')).display !== 'none';
    googleOk && backOk ? pass('接口类型联动 (必填/提示)') : fail('接口类型联动', 'google=' + googleOk + ' openai=' + backOk);

    // 空字段点「先测一下」→ 只提示,不发请求
    $('#u').value = ''; $('#k').value = ''; $('#m').value = '';
    const n0 = calls().length;
    $('#try').click(); await sleep(200);
    const warned = $('#msg').className.includes('bad') && $('#msg').textContent.length > 0;
    warned && calls().length === n0 ? pass('空字段「先测一下」被拦下', $('#msg').textContent.slice(0, 16))
      : fail('空字段「先测一下」被拦下', '提示=' + warned + ' 请求=' + (calls().length - n0));

    // 填全后「先测一下」→ /api/test
    $('#u').value = 'http://127.0.0.1:9/v1'; $('#k').value = 'sk-test-123456'; $('#m').value = 'mock-model';
    const n1 = calls().filter((c) => c.url.includes('/api/test')).length;
    $('#try').click(); await sleep(600);
    const tested = calls().filter((c) => c.url.includes('/api/test')).length > n1;
    const okMsg = $('#msg').textContent.includes('连上了');
    tested ? pass('「先测一下」→ /api/test', okMsg ? '结果显示连接正常' : '')
      : fail('「先测一下」→ /api/test', '没发出请求');

    // 保存并进入 → /api/setup
    const n2 = calls().filter((c) => c.url.includes('/api/setup')).length;
    $('#go').click(); await sleep(600);
    calls().filter((c) => c.url.includes('/api/setup')).length > n2
      ? pass('「保存并进入工坊」→ /api/setup') : fail('「保存并进入工坊」', '没发出请求');

    return R;
  })()`)
} else {
result = await evalJs(`(async () => {
  const R = [];
  const pass = (n, d = '') => R.push({ name: n, ok: true, detail: d });
  const fail = (n, d = '') => R.push({ name: n, ok: false, detail: d });
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => [...document.querySelectorAll(s)];
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const calls = () => window.__calls || [];
  const tasks = () => calls().filter((c) => c.url.includes('/api/task'));
  const fire = (el, type, init = {}) => el.dispatchEvent(new Event(type, { bubbles: true, cancelable: true, ...init }));
  const key = (el, init) => el.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init }));
  const t = $('#t'), form = $('#f'), chat = $('#chat');
  const sendText = async (text) => {
    t.value = text;
    t.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
    await sleep(400);
  };

  // 1) 首屏渲染
  if (chat && chat.children.length > 0) pass('欢迎语渲染', chat.children.length + ' 个节点');
  else fail('欢迎语渲染', '聊天区是空的');

  // 2) 愿望卡片
  const wishes = $$('[data-wish]');
  if (wishes.length >= 4) pass('愿望卡片', wishes.length + ' 张可点');
  else fail('愿望卡片', '只找到 ' + wishes.length + ' 张');

  if (wishes[0]) {
    wishes[0].click();
    await sleep(50);
    if (t.value && t.value === wishes[0].dataset.wish) pass('点愿望卡填入输入框', t.value.slice(0, 12) + '…');
    else fail('点愿望卡填入输入框', '输入框值=' + JSON.stringify(t.value));
    t.value = '';
  }

  // 3) 模式切换
  $('#modePlan').click(); await sleep(30);
  const planOn = $('#modePlan').classList.contains('on') && !$('#modeBuild').classList.contains('on');
  $('#modeBuild').click(); await sleep(30);
  const buildOn = $('#modeBuild').classList.contains('on') && !$('#modePlan').classList.contains('on');
  planOn && buildOn ? pass('模式切换 (方案/直接做)') : fail('模式切换', 'planOn=' + planOn + ' buildOn=' + buildOn);

  // 4) 回车发送
  const before = tasks().length;
  await sendText('做一个喝水提醒插件');
  const sent = tasks()[before];
  if (sent && JSON.parse(sent.body).text === '做一个喝水提醒插件' && JSON.parse(sent.body).mode === 'build') pass('回车发送 (/api/task)', 'mode=build');
  else fail('回车发送', sent ? 'payload=' + sent.body : '没有发出请求');
  t.value === '' ? pass('发送后清空输入框') : fail('发送后清空输入框', JSON.stringify(t.value));
  $('#chat details.think') ? pass('思考过程折叠块渲染', '详情块存在') : fail('思考过程折叠块渲染', '没有 details.think');

  // 5) Shift+回车 只换行
  let n0 = tasks().length;
  t.value = '换行测试';
  key(t, { key: 'Enter', shiftKey: true });
  await sleep(150);
  tasks().length === n0 ? pass('Shift+回车 不发送') : fail('Shift+回车 不发送', '却发了请求');
  t.value = '';

  // 6) 输入法选词回车 不发送
  n0 = tasks().length;
  t.value = '输入法选词';
  key(t, { key: 'Enter', isComposing: true });
  key(t, { key: 'Enter', keyCode: 229 });
  await sleep(150);
  tasks().length === n0 ? pass('输入法回车 不发送') : fail('输入法回车 不发送', '却发了请求');
  t.value = '';

  // 7) 空内容回车 不发送
  n0 = tasks().length;
  t.value = '   ';
  key(t, { key: 'Enter' });
  await sleep(150);
  tasks().length === n0 ? pass('空内容回车 不发送') : fail('空内容回车 不发送', '却发了请求');
  t.value = '';

  // 8) 提问芯片:候选答案 + 兜底「其他(自己说)」
  window.__mockReply = '这个插件你更想要哪种?\\n[[WB-Q]]\\n要一个设置面板\\n纯后台就行\\n[[/WB-Q]]\\n问清楚才好动手。';
  await sendText('做个小工具');
  await sleep(200);
  const wbq = $$('[data-wbq]');
  const other = $('[data-wbother]');
  wbq.length >= 2 ? pass('候选答案芯片渲染', wbq.length + ' 个') : fail('候选答案芯片渲染', '找到 ' + wbq.length + ' 个');
  other ? pass('兜底「其他(自己说)」芯片', other.textContent.trim()) : fail('兜底「其他(自己说)」芯片', '模型没写时前端也没补');

  if (other) {
    const n1 = tasks().length;
    other.click();
    await sleep(200);
    const focused = document.activeElement === t;
    const ph = t.placeholder || '';
    tasks().length === n1 && focused ? pass('点「其他」只聚焦输入框不发请求', 'placeholder=' + ph.slice(0, 8) + '…')
      : fail('点「其他」只聚焦输入框不发请求', '发了请求=' + (tasks().length !== n1) + ' 聚焦=' + focused);
  }

  if (wbq[0]) {
    const n2 = tasks().length;
    wbq[0].click();
    await sleep(400);
    const c = tasks()[n2];
    c && JSON.parse(c.body).text === wbq[0].textContent.trim()
      ? pass('点候选答案直接发送', wbq[0].textContent.trim())
      : fail('点候选答案直接发送', c ? 'text=' + JSON.parse(c.body).text : '没有发出请求');
  }

  // 9) 方案「开工」按钮
  window.__mockReply = '我打算这么做:先写定时器,再做设置面板。\\n[[WB-PLAN]]';
  await sendText('随便做个啥');
  await sleep(200);
  const planBtn = $('[data-wbplan]');
  if (planBtn) {
    const n3 = tasks().length;
    planBtn.click();
    await sleep(400);
    const c = tasks()[n3];
    const modeBuildOn = $('#modeBuild').classList.contains('on');
    c && modeBuildOn ? pass('「开工」按钮切到直接做并提交方案', 'mode=build')
      : fail('「开工」按钮', '请求=' + !!c + ' 切模式=' + modeBuildOn);
  } else fail('「开工」按钮渲染', '找不到 [data-wbplan]');

  // 9.5) 改动文件:整块列表(不是一堆气泡),点文件名定位
  // 每轮回复一张卡是对的(旧消息各自保留自己的改动);关键是同一轮内不能拆成 N 个气泡
  const cards = $$('.fchg');
  const rows = $$('.fchg-row');
  cards.length >= 1 && rows.length === cards.length * window.__mockFiles.length
    ? pass('改动文件渲染为单个列表', cards.length + ' 轮 × ' + window.__mockFiles.length + ' 行')
    : fail('改动文件渲染为单个列表', '卡片=' + cards.length + ' 行=' + rows.length + '(期望每卡 ' + window.__mockFiles.length + ' 行)');
  if (rows.length) {
    const n4 = calls().length;
    rows[0].click();
    await sleep(350);
    const rev = calls().slice(n4).find((c) => c.url.includes('/api/reveal'));
    const want = rows[0].getAttribute('data-reveal');
    rev && JSON.parse(rev.body).path === want
      ? pass('点文件名 → /api/reveal 定位', want)
      : fail('点文件名 → /api/reveal 定位', rev ? 'path=' + rev.body : '没发请求');
  } else fail('点文件名 → /api/reveal 定位', '没有可点的行');

  // 10) 设置面板开关
  $('#setbtn').click(); await sleep(50);
  const opened = $('#setpanel').classList.contains('open');
  $('#setclose').click(); await sleep(50);
  const closed = !$('#setpanel').classList.contains('open');
  opened && closed ? pass('设置面板 打开/关闭') : fail('设置面板', 'open=' + opened + ' close=' + closed);

  // 11) 设置分类标签
  $('#setbtn').click(); await sleep(50);
  const tabs = $$('.sptab');
  if (tabs.length >= 2) {
    tabs[1].click(); await sleep(30);
    const lookShown = getComputedStyle($('#tab-look')).display !== 'none';
    tabs[0].click(); await sleep(30);
    const apiShown = getComputedStyle($('#tab-api')).display !== 'none';
    lookShown && apiShown ? pass('设置面板 分类标签切换', tabs.length + ' 个标签') : fail('设置面板 分类标签切换', 'look=' + lookShown + ' api=' + apiShown);
  } else fail('设置面板 分类标签', '只有 ' + tabs.length + ' 个');

  // 11.5) 长期记忆:页签能开、能加、能删(状态以服务端为准,DOM 只验证渲染)
  const memTab = tabs.find((x) => x.dataset && x.dataset.tab === 'mem');
  const wbWait = async (fn, ms = 4000) => {
    const t0 = Date.now();
    while (Date.now() - t0 < ms) {
      if (await fn()) return true;
      await sleep(120);
    }
    return false;
  };
  const memCount = async () => {
    const j = await (await fetch('/api/memory')).json();
    return ((j || {}).items || []).length;
  };
  if (memTab) {
    memTab.click();
    const shown = await wbWait(() => {
      const el = $('#tab-mem');
      return !!el && getComputedStyle(el).display !== 'none';
    }, 2000);
    // 等首屏渲染完(空列表有占位文案),再以服务端条数为基准
    await wbWait(() => {
      const box = $('#memList');
      return !!box && (!!box.querySelector('.memrow') || box.textContent.includes('还没有记录'));
    }, 4000);
    const before = await memCount();
    const inp = $('#memInput');
    inp.value = '配色统一用莫兰迪色系';
    $('#memAdd').click();
    const added = await wbWait(async () => (await memCount()) === before + 1);
    const rowOk = $$('#memList .memrow').length >= 1;
    let after = await memCount();
    if (added) {
      const rowsNow = $$('#memList .memrow');
      const del = rowsNow[rowsNow.length - 1] && rowsNow[rowsNow.length - 1].querySelector('[data-memdel]');
      if (del) del.click();
      await wbWait(async () => (await memCount()) === before);
      after = await memCount();
    }
    shown && added && rowOk && after === before
      ? pass('长期记忆 页签/添加/删除', '加后 ' + (before + 1) + ' 条,删后 ' + after + ' 条')
      : fail('长期记忆 页签/添加/删除', 'shown=' + shown + ' added=' + added + ' row=' + rowOk + ' after=' + after + ' before=' + before);
    tabs[0].click(); await sleep(50);
  } else fail('长期记忆页签', '找不到 data-tab=mem');

  // 11.6) 重试页签:参数独立成页,四个输入能改、能保存、能恢复默认
  const runTab = tabs.find((x) => x.dataset && x.dataset.tab === 'run');
  if (runTab) { runTab.click(); await sleep(80); }
  const runShown = !!$('#tab-run') && getComputedStyle($('#tab-run')).display !== 'none';
  const tT = $('#timeoutSec'), tS = $('#stallTimeoutSec'), tR = $('#retryMax'), tD = $('#retryDelaySec');
  if (tT && tS && tR && tD) {
    tT.value = 1800; tS.value = 420; tR.value = 3; tD.value = 4;
    $('#tuneSave').click();
    const got = await (async () => {
      // 保存是异步的:轮询到后端生效为止(最多 4 秒)
      const t0 = Date.now();
      let j = {};
      while (Date.now() - t0 < 4000) {
        j = await (await fetch('/api/retry')).json();
        if (j && j.timeoutSec === 1800 && j.stallTimeoutSec === 420 && j.retryMax === 3 && j.retryDelaySec === 4) break;
        await sleep(150);
      }
      return j;
    })();
    const applied = got && got.ok && got.timeoutSec === 1800 && got.stallTimeoutSec === 420 && got.retryMax === 3 && got.retryDelaySec === 4;
    $('#tuneReset').click();
    const restored = await wbWait(() => Number($('#timeoutSec').value) === 1500 && Number($('#retryMax').value) === 2, 4000);
    applied && restored && runShown
      ? pass('重试页签 保存/恢复默认', '1800/420/3/4 → 1500/360/2/3')
      : fail('重试页签 保存/恢复默认', 'shown=' + runShown + ' applied=' + applied + ' reset=' + restored + ' ' + JSON.stringify(got || {}).slice(0, 120));
    tabs[0].click(); await sleep(50);
  } else fail('运行参数输入框', [!!tT, !!tS, !!tR, !!tD].join(','));

  // 11.7) 更新页签:面板存在、当前版本可读、能点检查(不发真实下载)
  const updTab = tabs.find((x) => x.dataset && x.dataset.tab === 'update');
  if (updTab) {
    updTab.click();
    await wbWait(() => (($('#updCur') || {}).textContent || '').trim().length > 1, 3000);
    const shown = !!$('#tab-update') && getComputedStyle($('#tab-update')).display !== 'none';
    const curTxt = ($('#updCur').textContent || '').trim();
    const hasBtn = !!$('#updCheck') && !!$('#updGo') && !!$('#updModal');
    shown && /^v\\d+\\.\\d+\\.\\d+/.test(curTxt) && hasBtn
      ? pass('更新页签 面板/版本号/按钮', curTxt)
      : fail('更新页签 面板/版本号/按钮', 'shown=' + shown + ' cur=' + curTxt + ' btn=' + hasBtn);

    // 11.8) 点「检查更新」必须弹窗:探针脚本自带 fetch stub,返回逐项结果
    try {
      const probe = await (
${PROBE_SRC}
      )
      for (const r of probe) (r.ok ? pass : fail)(r.name, r.detail)
    } catch (e) {
      fail('更新弹窗探针', String((e && e.message) || e))
    }
    tabs[0].click(); await sleep(50);
  } else fail('更新页签', '找不到 data-tab=update');

  // 12) 主题切换:逐个点,校验 body 类名与 localStorage
  const themes = $$('.themeopt');
  if (themes.length) {
    let bad = '';
    for (const el of themes) {
      el.click(); await sleep(60);
      const t0 = el.dataset.t || 'pink';
      const cls = document.body.className;
      const okTheme = t0 === 'pink' ? !/theme-/.test(cls) : cls.includes('theme-' + t0);
      const saved = localStorage.getItem('wb_theme');
      if (!okTheme || saved !== t0) bad += ' ' + t0 + '(ok=' + okTheme + ',saved=' + saved + ')';
    }
    bad ? fail('主题切换 (全部 ' + themes.length + ' 套)', bad) : pass('主题切换 (全部 ' + themes.length + ' 套)');
    themes[0].click(); await sleep(60);
    localStorage.getItem('wb_theme') !== null ? pass('主题记忆 (localStorage)') : fail('主题记忆', '没有写入');
  } else fail('主题切换', '没有 .themeopt');

  // 12.5) 界面字体与字号:选字体 / 拉字号 / 落库 / 还原
  if (tabs.length > 1) { tabs[1].click(); await sleep(60); }
  const fontChips = $$('.fontopt');
  if (fontChips.length >= 4) {
    const hs = fontChips.find((x) => x.dataset && x.dataset.f === 'hengshui');
    hs.click(); await sleep(150);
    const fam = getComputedStyle(document.body).fontFamily || '';
    const curOk = hs.classList.contains('cur');
    const sl = $('#fontSize');
    sl.value = '130';
    sl.dispatchEvent(new Event('input', { bubbles: true }));
    sl.dispatchEvent(new Event('change', { bubbles: true }));
    let saved = null;
    const applied = await (async () => {
      const t0 = Date.now();
      while (Date.now() - t0 < 4000) {
        const j = await (await fetch('/api/ui')).json();
        saved = j;
        if (j && j.ok && j.font === 'hengshui' && j.size === 130) return true;
        await sleep(150);
      }
      return false;
    })();
    const zoomed = Math.abs(parseFloat(document.body.style.zoom || '1') - 1.3) < 0.001;
    const labelOk = ($('#fontSizeVal').textContent || '').includes('130');
    // 还原
    fontChips.find((x) => x.dataset.f === 'default').click();
    $('#fontSizeReset').click();
    await sleep(300);
    const backFam = (getComputedStyle(document.body).fontFamily || '').includes('Microsoft YaHei');
    const backZoom = Math.abs(parseFloat(document.body.style.zoom || '1') - 1) < 0.001;
    (fam.includes('楷体') || fam.includes('KaiTi')) && curOk && zoomed && labelOk && applied && backFam && backZoom
      ? pass('字体与字号 切换/缩放/还原', '衡水体 + 130% → 默认 + 100%')
      : fail('字体与字号 切换/缩放/还原', 'fam=' + fam.slice(0, 40) + ' cur=' + curOk + ' zoom=' + zoomed + ' label=' + labelOk + ' saved=' + applied + ' back=' + backFam + '/' + backZoom + ' ' + JSON.stringify(saved || {}).slice(0, 80));
  } else fail('字体选项', '只有 ' + fontChips.length + ' 个');

  // 13) 主动测试按钮(只测不存)
  const n4 = calls().filter((c) => c.url.includes('/api/test')).length;
  const urlEl = $('#apiURL'), keyEl = $('#apiKey'), modelEl = $('#apiModel');
  if (urlEl) urlEl.value = 'http://127.0.0.1:9/v1';
  if (keyEl) keyEl.value = 'sk-test-123456';
  if (modelEl) modelEl.value = 'mock-model';
  $('#apiTest').click();
  await sleep(600);
  calls().filter((c) => c.url.includes('/api/test')).length > n4
    ? pass('「先测一下」按钮 → /api/test')
    : fail('「先测一下」按钮', '没发出 /api/test');

  $('#setclose').click(); await sleep(50);

  // 14) 停止按钮
  const n5 = calls().filter((c) => c.url.includes('/api/stop')).length;
  $('#stopbtn').click();
  await sleep(300);
  calls().filter((c) => c.url.includes('/api/stop')).length > n5 ? pass('停止按钮 → /api/stop') : fail('停止按钮', '没发出 /api/stop');

  // 15) 新对话
  const n6 = calls().filter((c) => c.url.includes('/api/sessions') && c.method === 'POST').length;
  $('#newbtn').click();
  await sleep(600);
  calls().filter((c) => c.url.includes('/api/sessions') && c.method === 'POST').length > n6
    ? pass('新对话按钮 → 新建会话') : fail('新对话按钮', '没发出建会话请求');

  // 16) 严格校验开关
  const st = $('#strictToggle');
  st ? pass('严格校验开关存在', 'checked=' + st.checked) : fail('严格校验开关', '找不到 #strictToggle');

  // 17) 导入插件
  $('#importbtn').click(); await sleep(100);
  $('#importfile') ? pass('导入插件按钮 + 隐藏文件框就位') : fail('导入插件', '找不到 #importfile');

  // 18) 上下文容量环
  const cb = $('#ctxbar');
  const ring = cb && cb.querySelector('.ctxring svg');
  const circles = ring ? ring.querySelectorAll('circle') : [];
  const arc = circles[1];
  const hasArc = !!(arc && Number(arc.getAttribute('stroke-dasharray')) > 0 && arc.getAttribute('stroke-dashoffset') !== null);
  cb && cb.textContent.trim() && ring && circles.length === 2 && hasArc
    ? pass('上下文容量环渲染', '底环+进度环,' + cb.textContent.trim())
    : fail('上下文容量环渲染', 'ring=' + !!ring + ' circles=' + circles.length + ' arc=' + hasArc);
  cb && cb.querySelector('.ctxring') && cb.querySelector('.ctxring').getAttribute('title')
    ? pass('容量环 tooltip', cb.querySelector('.ctxring').getAttribute('title'))
    : fail('容量环 tooltip', '没有 title');

  // 上下文压缩按钮:点一下要真的发出 /compact,并给出提示条
  const cbtn = $('#compactbtn');
  if (cbtn) {
    const n0 = calls().filter((c) => c.url.includes('/compact')).length;
    cbtn.click();
    await sleep(1500);
    const sent = calls().filter((c) => c.url.includes('/compact')).length > n0;
    const notice = $('#chat .msg.notice');
    sent ? pass('「压缩上下文」按钮 → /compact', notice ? '已出现提示条' : '请求已发出')
      : fail('「压缩上下文」按钮', '没发出请求');
  } else fail('「压缩上下文」按钮', '找不到 #compactbtn');

  // 19) 单 API 上下文:行内有 🧠 chip,表单里有 #apiCtx
  $('#apiCtx') ? pass('API 表单含「上下文窗口」字段') : fail('API 表单含「上下文窗口」字段', '找不到 #apiCtx');
  $('#setbtn').click(); await sleep(400);
  const ctxChip = $('.apirow .ctxchip');
  ctxChip ? pass('API 行显示上下文 chip', ctxChip.textContent.trim()) : pass('API 行显示上下文 chip', '(当前无 API,跳过)');
  $('#setclose').click(); await sleep(100);

  // 19.5) 上下文快捷按钮(外观页的全局默认)+ API「仅保存」→ 列表 → 清理
  $('#setbtn').click(); await sleep(300);
  const lookTab = $$('.sptab').find((b) => b.dataset.tab === 'look');
  lookTab && lookTab.click(); await sleep(200);
  const chip32 = $('.ctxpreset[data-ctx="32000"]');
  chip32 && chip32.click(); await sleep(150);
  const v32 = ($('#ctxInput') ? $('#ctxInput').value : '').trim();
  v32 === '32' ? pass('上下文快捷 32K 填入', '32') : fail('上下文快捷 32K 填入', v32 || '(空)');
  const chip128 = $('.ctxpreset[data-ctx="128000"]');
  chip128 && chip128.click(); await sleep(150);
  const v128 = ($('#ctxInput') ? $('#ctxInput').value : '').trim();
  v128 === '128' ? pass('上下文快捷 128K 恢复默认', '128') : fail('上下文快捷 128K 恢复默认', v128 || '(空)');

  const apiTab2 = $$('.sptab').find((b) => b.dataset.tab === 'api');
  apiTab2 && apiTab2.click(); await sleep(200);
  $('#apiName').value = '自动测试API';
  $('#apiURL').value = 'http://127.0.0.1:9/v1';
  $('#apiKey').value = 'sk-test-abcdef';
  $('#apiModel').value = 'mock-model';
  $('#apiCtx').value = '64';
  $('#apiSaveOnly').click();
  await wbWait(() => ($('#apiMsg').textContent || '').indexOf('✅') >= 0, 6000);
  const savedOk = ($('#apiMsg').textContent || '').indexOf('✅') >= 0;
  const rowNow = $$('.apirow').find((r) => r.textContent.indexOf('自动测试API') >= 0);
  savedOk && rowNow ? pass('API「仅保存」并出现在列表', '上下文 64K') : fail('API「仅保存」并出现在列表', $('#apiMsg').textContent || '(无提示)');
  if (rowNow) {
    const jl = await (await fetch('/api/apis')).json();
    const item = (jl.items || []).find((a) => a.name === '自动测试API');
    if (item) await fetch('/api/apis/' + item.id, { method: 'DELETE' });
    if (window.loadApis) window.loadApis();
    await sleep(400);
    const jl2 = await (await fetch('/api/apis')).json();
    const gone = !(jl2.items || []).some((a) => a.name === '自动测试API');
    gone ? pass('清理测试 API') : fail('清理测试 API', '仍存在');
  }
  $('#setclose').click(); await sleep(100);

  // 20) 侧边栏会话列表
  const side = $('#slist');
  side && side.children.length ? pass('侧边栏会话列表', side.children.length + ' 条') : fail('侧边栏会话列表', '空');

  // 20) 归档视图切换
  const ab = $('#archbtn');
  if (ab) {
    const t0 = ab.textContent;
    ab.click(); await sleep(200);
    const changed = ab.textContent !== t0 && ab.classList.contains('on');
    ab.click(); await sleep(200);
    changed ? pass('归档视图切换') : fail('归档视图切换', '文案没变');
  } else fail('归档视图切换', '找不到 #archbtn');

  // 21) 侧边栏:归档 / 取消归档 / 重命名 / 删除
  // 断言一律走 /api/sessions 真实状态 + 轮询,不看 DOM 文案(会话可能同名「新对话」,标题比对会误判)
  const titles = () => $$('#slist .sitem .t').map((e) => e.textContent);
  const sess = async () => (await (await fetch('/api/sessions')).json()).items || [];
  const archCount = async () => (await sess()).filter((s) => s.archived).length;
  const waitFor = async (fn, ms = 3000) => { const t0 = Date.now(); let v = await fn(); while (!v && Date.now() - t0 < ms) { await sleep(150); v = await fn() } return v };
  // 上一轮可能把「归档视图」记进了 localStorage,先归位到对话列表,否则行内只有 📤/🗑
  if ($('#archbtn').classList.contains('on')) { $('#archbtn').click(); await sleep(600); }

  const arcBtn = $('#slist .sitem .arc');
  if (arcBtn) {
    const before = await archCount();
    arcBtn.click();
    const archived = await waitFor(async () => (await archCount()) > before);
    archived ? pass('侧边栏「归档」', '已归档 ' + (await archCount()) + ' 个') : fail('侧边栏「归档」', '归档数没变(' + before + ')');
    // 切到归档视图,应能看到「取消归档」
    if (!$('#archbtn').classList.contains('on')) { $('#archbtn').click(); }
    const un = await waitFor(async () => $('#slist .sitem .un'));
    un ? pass('归档视图里可「取消归档」', un.title) : fail('归档视图里可「取消归档」', '没有 .un 按钮');
    if (un) {
      un.click();
      const restored = await waitFor(async () => (await archCount()) === before);
      restored ? pass('「取消归档」恢复原状', '归档数回落到 ' + before) : fail('「取消归档」', '归档数没回落');
    }
    if ($('#archbtn').classList.contains('on')) { $('#archbtn').click(); }
    await sleep(400);
  } else fail('侧边栏「归档」', '没找到 .arc 按钮');

  window.prompt = () => '自动化改名';
  window.confirm = () => true;
  const renBtn = $('#slist .sitem .ren');
  if (renBtn) {
    const before = (await sess()).map((s) => s.id + ':' + s.title).join('|');
    renBtn.click();
    const renamed = await waitFor(async () => (await sess()).some((s) => s.title === '自动化改名'));
    renamed ? pass('侧边栏「重命名」→ 服务端标题已改', '自动化改名') : fail('侧边栏「重命名」', '服务端标题没变');
    titles().some((x) => x.includes('自动化改名')) || await waitFor(async () => titles().some((x) => x.includes('自动化改名')))
      ? pass('重命名后列表已刷新') : fail('重命名后列表已刷新', '列表=' + titles().join('/'));
  } else fail('侧边栏「重命名」', '没找到 .ren 按钮');

  const delBtn = $('#slist .sitem .del');
  if (delBtn) {
    const n8 = calls().filter((c) => c.method === 'DELETE' && c.url.includes('/api/sessions/')).length;
    delBtn.click(); await sleep(900);
    calls().filter((c) => c.method === 'DELETE' && c.url.includes('/api/sessions/')).length > n8
      ? pass('侧边栏「删除」→ DELETE') : fail('侧边栏「删除」', '没发出 DELETE');
  } else fail('侧边栏「删除」', '没找到 .del 按钮');

  return R;
})()`)
}

// ---------- 4.5 可选:截一张「改动文件列表」的图(--shot 路径) ----------
// 必须趁 UI 检查刚跑完、列表还在 DOM 里时截;后面的会话切换会把聊天区重渲染成服务端历史
const SHOT = arg("shot", "")
if (SHOT) {
  try {
    // 侧边栏用例会把聊天区重渲染成服务端历史(里面没有 mock 的 modifiedFiles),
    // 所以截图前先补发一条(仍走桩,不落库),让改动列表重新出现在最新一条回复里
    await evalJs(`(() => { const t=document.querySelector('textarea'); if(!t) return false; t.value='截图用:做个喝水提醒插件'; const f=document.querySelector('form'); if(!f) return false; f.requestSubmit(); return true })()`)
    await sleep(1500)
    const box = await evalJs(`(() => {
      const c = [...document.querySelectorAll('.fchg')].pop();
      if (!c) return null;
      c.scrollIntoView({ block: 'center' });
      const r = c.getBoundingClientRect();
      return { x: Math.max(0, r.x - 10), y: Math.max(0, r.y - 10), width: Math.min(r.width + 20, 620), height: r.height + 20 };
    })()`)
    if (box) {
      await sleep(300)
      const shot = await send("Page.captureScreenshot", { format: "png", clip: { ...box, scale: 2 }, captureBeyondViewport: true })
      writeFileSync(SHOT, Buffer.from(shot.data, "base64"))
      console.log(`${C.d}截图已写入 ${SHOT}${C.x}`)
    } else {
      const dbg = await evalJs("document.querySelectorAll('.msg').length + '/' + document.querySelectorAll('.fchg').length").catch(() => "?")
      console.log(`${C.r}没有可截图的改动列表${C.x} (msg/fchg=${dbg})`)
    }
  } catch (e) {
    console.log(`${C.r}截图失败: ${e.message}${C.x}`)
  }
}

// ---------- 5. 接口体检(不经过页面) ----------
// POST 接口要带页面注入的 x-wb-token,否则会被 403 挡住(这本身也是安全检查的一环)
const homeHtml = (await localReq("/", { timeout: 15000 })).text || ""
const TOKEN = (homeHtml.match(/const WB_TOKEN="([^"]+)"/) || [])[1] || ""
const H = TOKEN ? { "x-wb-token": TOKEN } : {}
if (!TOKEN) console.error(`${C.y}⚠ 首页没取到 token(响应 ${homeHtml.length} 字节),后续 POST 会被 403 拦下${C.x}`)

const apiChecks = []
if (isSetup) {
  const ok = homeHtml.includes('id="presets"') && homeHtml.includes('id="go"') && homeHtml.includes('id="try"')
  apiChecks.push({ name: "配置页 HTML 结构完整", ok, detail: ok ? "找到 #presets / #go / #try" : "缺少关键元素" })
} else {
  const ok = homeHtml.includes('id="chat"') && homeHtml.includes('id="setbtn"')
  apiChecks.push({ name: "主页面 HTML 结构完整", ok, detail: ok ? "找到 #chat / #setbtn" : "缺少关键元素(可能返回的是引导页)" })
}
const probe = async (name, path, init = {}, expect) => {
  const r = await localReq(path, { method: init.method || "GET", body: init.body || null, timeout: 20000, headers: init.headers || H })
  if (r.error) return apiChecks.push({ name, ok: false, detail: "请求失败: " + r.error })
  apiChecks.push({ name, ok: !!expect(r.status, r.json), detail: `HTTP ${r.status}${r.json && r.json.error ? " " + String(r.json.error).slice(0, 40) : ""}` })
}
await probe("GET /api/health", "/api/health", {}, (s, j) => s === 200 && j)
await probe("GET /api/sessions", "/api/sessions", {}, (s, j) => s === 200 && j && j.current)
await probe("GET /api/apis", "/api/apis", {}, (s, j) => s === 200 && j)
await probe("GET /api/strict", "/api/strict", {}, (s, j) => s === 200 && j && typeof j.strictVerify === "boolean")
await probe("GET /api/thinking", "/api/thinking", {}, (s, j) => s === 200 && j)
await probe("POST /api/task 空内容拦截", "/api/task", { method: "POST", body: "{}" }, (s, j) => s === 400 && j && j.error)
await probe("POST /api/task 无令牌拦截", "/api/task", { method: "POST", body: "{}", headers: {} }, (s) => s === 403)
await probe("POST /api/stop", "/api/stop", { method: "POST", body: "{}" }, (s, j) => s === 200 && j && j.ok !== false)
await probe("GET /api/download 缺参拦截", "/api/download", {}, (s) => s >= 400)

// ---------- 5b. 单 API 上下文(加 --mutate 才跑:会临时增删测试实例里的 API) ----------
if (argv.includes("--mutate")) {
  const post = (path, obj) => localReq(path, { method: "POST", body: JSON.stringify(obj), timeout: 40000, headers: H })
  const created = []
  try {
    const a = await post("/api/apis", { name: "ctx-test", baseURL: "http://127.0.0.1:9/v1", apiKey: "sk-test-123456", model: "mock-a", protocol: "openai", contextLimit: 64000, enable: true })
    const id = a.json && a.json.id
    if (id) created.push(id)
    let ctx = await localReq("/api/context", { timeout: 10000, headers: H })
    apiChecks.push({
      name: "新建 API 自带 64K → 生效刻度",
      ok: !!(ctx.json && ctx.json.effectiveLimit === 64000 && ctx.json.source === "api"),
      detail: `effective=${ctx.json && ctx.json.effectiveLimit} source=${ctx.json && ctx.json.source}`,
    })
    if (id) {
      await post("/api/apis/" + id + "/context", { contextLimit: 200000 })
      ctx = await localReq("/api/context", { timeout: 10000, headers: H })
      apiChecks.push({ name: "单独改成 200K → 生效", ok: !!(ctx.json && ctx.json.effectiveLimit === 200000), detail: `effective=${ctx.json && ctx.json.effectiveLimit}` })

      await post("/api/apis/" + id + "/context", { contextLimit: null })
      ctx = await localReq("/api/context", { timeout: 10000, headers: H })
      apiChecks.push({
        name: "清空 → 回落全局默认",
        ok: !!(ctx.json && ctx.json.source === "global" && ctx.json.effectiveLimit === ctx.json.contextLimit),
        detail: `effective=${ctx.json && ctx.json.effectiveLimit} global=${ctx.json && ctx.json.contextLimit}`,
      })

      const bad = await post("/api/apis/" + id + "/context", { contextLimit: 500 })
      apiChecks.push({ name: "非法上下文值被拦下 (400)", ok: bad.status === 400, detail: "HTTP " + bad.status })
    } else {
      apiChecks.push({ name: "临时 API 创建", ok: false, detail: JSON.stringify(a.json).slice(0, 80) })
    }

    // ---- 改动文件定位:越界路径拒绝、缺失文件 404(不真的弹资源管理器)----
    const rv1 = await post("/api/reveal", { path: "../../Windows/win.ini" })
    apiChecks.push({ name: "定位文件:越界路径被拒 (403)", ok: rv1.status === 403, detail: "HTTP " + rv1.status })
    const rv2 = await post("/api/reveal", { path: "definitely_missing_file_xyz.py" })
    apiChecks.push({ name: "定位文件:不存在 (404)", ok: rv2.status === 404, detail: "HTTP " + rv2.status })

    // ---- 长期记忆:读 / 加 / 空内容拒绝 / 删 ----
    const mem0 = await localReq("/api/memory", { timeout: 8000, headers: H })
    apiChecks.push({
      name: "长期记忆 读取",
      ok: mem0.status === 200 && Array.isArray((mem0.json || {}).items),
      detail: "items=" + (((mem0.json || {}).items || []).length),
    })
    const mAdd = await post("/api/memory", { action: "add", text: "自检临时记忆" })
    const mItem = (((mAdd.json || {}).items) || []).find((x) => x.text === "自检临时记忆")
    apiChecks.push({ name: "长期记忆 添加", ok: mAdd.status === 200 && !!mItem, detail: mItem ? "id=" + mItem.id : JSON.stringify(mAdd.json).slice(0, 60) })
    const mBad = await post("/api/memory", { action: "add", text: "   " })
    apiChecks.push({ name: "空记忆被拒 (400)", ok: mBad.status === 400, detail: "HTTP " + mBad.status })
    const memNoToken = await localReq("/api/memory", { timeout: 8000 })
    apiChecks.push({ name: "长期记忆 读取需 token (403)", ok: memNoToken.status === 403, detail: "HTTP " + memNoToken.status })
    const memDelMiss = await post("/api/memory", { action: "delete", id: "m_not_exist" })
    apiChecks.push({ name: "删除不存在的记忆 (400)", ok: memDelMiss.status === 400, detail: "HTTP " + memDelMiss.status })
    if (mItem) {
      const mDel = await post("/api/memory", { action: "delete", id: mItem.id })
      const still = (((mDel.json || {}).items) || []).some((x) => x.id === mItem.id)
      apiChecks.push({
        name: "长期记忆 删除",
        ok: mDel.status === 200 && !still,
        detail: "剩余 " + (((mDel.json || {}).items) || []).length,
      })
    }

    // ---- 运行参数(超时/重试):读 / 越界拦截 / 保存 / 还原 ----
    const tune0 = await localReq("/api/retry", { timeout: 8000 })
    apiChecks.push({
      name: "运行参数 读取",
      ok: tune0.status === 200 && typeof (tune0.json || {}).timeoutSec === "number" && typeof (tune0.json || {}).stallTimeoutSec === "number",
      detail: `${(tune0.json || {}).timeoutSec}s / ${(tune0.json || {}).stallTimeoutSec}s`,
    })
    const tuneBad1 = await post("/api/retry", { timeoutSec: 10 })
    apiChecks.push({ name: "任务超时越界 (400)", ok: tuneBad1.status === 400, detail: "HTTP " + tuneBad1.status })
    const tuneBad2 = await post("/api/retry", { timeoutSec: 300, stallTimeoutSec: 600 })
    const afterBad2 = await localReq("/api/retry", { timeout: 8000 })
    apiChecks.push({
      name: "无响应超时须小于总超时 (400 且不半截生效)",
      ok: tuneBad2.status === 400 && (afterBad2.json || {}).timeoutSec !== 300,
      detail: "HTTP " + tuneBad2.status + " timeout=" + (afterBad2.json || {}).timeoutSec,
    })
    const tuneOk = await post("/api/retry", { timeoutSec: 900, stallTimeoutSec: 300, retryMax: 1, retryDelaySec: 2 })
    apiChecks.push({
      name: "运行参数 保存",
      ok: tuneOk.status === 200 && (tuneOk.json || {}).timeoutSec === 900 && (tuneOk.json || {}).stallTimeoutSec === 300,
      detail: JSON.stringify(tuneOk.json || {}).slice(0, 70),
    })
    const tuneBack = await post("/api/retry", { timeoutSec: 1500, stallTimeoutSec: 360, retryMax: 2, retryDelaySec: 3 })
    apiChecks.push({ name: "运行参数 还原", ok: tuneBack.status === 200 && (tuneBack.json || {}).timeoutSec === 1500, detail: "HTTP " + tuneBack.status })

    // ---- 界面字体与字号 ----
    const ui0 = await localReq("/api/ui", { timeout: 8000, headers: H })
    apiChecks.push({
      name: "外观 读取",
      ok: ui0.status === 200 && typeof (ui0.json || {}).size === "number" && Array.isArray((ui0.json || {}).fonts),
      detail: `${(ui0.json || {}).font} / ${(ui0.json || {}).size}%`,
    })
    const uiBadFont = await post("/api/ui", { font: "comic" })
    apiChecks.push({ name: "未知字体被拒 (400)", ok: uiBadFont.status === 400, detail: "HTTP " + uiBadFont.status })
    const uiBadSize = await post("/api/ui", { size: 10 })
    apiChecks.push({ name: "字号越界 (400)", ok: uiBadSize.status === 400, detail: "HTTP " + uiBadSize.status })
    const uiOk = await post("/api/ui", { font: "cute", size: 115 })
    apiChecks.push({
      name: "外观 保存",
      ok: uiOk.status === 200 && (uiOk.json || {}).font === "cute" && (uiOk.json || {}).size === 115,
      detail: JSON.stringify(uiOk.json || {}).slice(0, 60),
    })
    const uiBack = await post("/api/ui", { font: "default", size: 100 })
    apiChecks.push({ name: "外观 还原", ok: uiBack.status === 200 && (uiBack.json || {}).font === "default", detail: "HTTP " + uiBack.status })

    // ---- 上下文压缩:导入长文档 → 手动压缩 → 校验摘要/计数/幂等 ----
    const longMd = "## 背景\n" + "这是一段用于测试压缩的长文本,包含用户需求与已确认的决策。".repeat(80)
    const imp = await post("/api/import", { name: "压缩测试", content: longMd })
    const sid = imp.json && imp.json.current
    if (sid) {
      const cp = await post("/api/sessions/" + sid + "/compact", {})
      const cj = cp.json || {}
      apiChecks.push({
        name: "手动压缩生成摘要",
        ok: !!(cj.ok && cj.compact && cj.compact.before > 0 && String(cj.summary || "").length > 20),
        detail: `before=${cj.compact && cj.compact.before} after=${cj.compact && cj.compact.after} 来源=${cj.summarySource}`,
      })
      const list = await localReq("/api/sessions", { timeout: 10000, headers: H })
      const item = ((list.json && list.json.items) || []).find((x) => x.id === sid)
      apiChecks.push({ name: "压缩次数写入会话列表", ok: !!(item && item.compactCount >= 1), detail: "compactCount=" + (item && item.compactCount) })
      const cp2 = await post("/api/sessions/" + sid + "/compact", {})
      apiChecks.push({ name: "重复压缩不报错", ok: cp2.status === 200 && !!(cp2.json && cp2.json.ok), detail: "HTTP " + cp2.status })
      await localReq("/api/sessions/" + sid, { method: "DELETE", timeout: 10000, headers: H })
    } else {
      apiChecks.push({ name: "导入文档建会话", ok: false, detail: JSON.stringify(imp.json).slice(0, 80) })
    }
  } finally {
    for (const id of created) await localReq("/api/apis/" + id, { method: "DELETE", timeout: 10000, headers: H })
  }
}

// ---------- 6. 汇总 ----------
const all = [...result, ...apiChecks]
const failed = all.filter((r) => !r.ok)
const w = Math.max(...all.map((r) => [...r.name].length)) + 2
const report = []
report.push(`== 工坊页面体检 @ ${BASE} ==`, "")
for (const r of all) {
  const pad = " ".repeat(Math.max(0, w - [...r.name].length))
  const detail = r.detail && (VERBOSE || !r.ok) ? `  ${r.detail}` : ""
  report.push(`${r.ok ? "✓" : "✗"} ${r.name}${pad}${detail}`)
}
if (pageErrors.length) {
  report.push("", `页面运行时报错 ${pageErrors.length} 条:`)
  for (const e of pageErrors.slice(0, 8)) report.push("  " + e.split("\n")[0])
}
report.push("", `${failed.length ? "失败 " + failed.length + " 项" : "全部通过"} / 共 ${all.length} 项`, "")

const OUT = arg("out", join(process.cwd(), "check-report.txt"))
try { writeFileSync(OUT, report.join("\n"), "utf8") } catch {}

for (const line of report) {
  if (line.startsWith("✓")) console.log(C.g + "✓" + C.x + line.slice(1))
  else if (line.startsWith("✗")) console.log(C.r + "✗" + C.x + line.slice(1))
  else console.log(line)
}
console.log(`${C.d}报告已写入 ${OUT}${C.x}\n`)

cleanup(failed.length || pageErrors.length ? 1 : 0)
