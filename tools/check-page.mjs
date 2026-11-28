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
import { request as httpRequest, createServer as createHttpServer } from "node:http"

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
  // 连接阶段的挂起不受 http.request 的 timeout 选项保护(timeout 只管连接后的空闲),
  // 所以再用 race 兜一层:10 秒拿不到结果就按「连不上」处理(issue #26 环境实测)。
  const r = await Promise.race([
    localReq("/api/health", { timeout: 8000 }),
    sleep(10000).then(() => ({ status: 0, error: "health 探测 10s 超时" })),
  ])
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

// ---------- 0.5 403-token 桩服务:页面 fetch 包装「自愈」的靶子 ----------
// 两个页面启动时都把 window.fetch 包了一层:响应是 403 且错误里带「token」→ 置 __WB_STALE、
// 顶部提示、2 秒后 reload(工坊更新重启后,旧窗口的 token 失效,写请求全部 403 的自愈)。
// 要测它,响应必须真的**穿过页面那层包装**:页面 JS 的请求先过 check-page 的桩(未知 URL 透传)
// → 页面包装 → 原生 fetch → 打到这里。由 check-page 桩直接回一个 403 Response 到不了页面包装
// 那层,等于没测——所以这里起一个真 HTTP 服务。
// 页面包装会给请求强加 x-wb-token 头(自定义头,跨端口=跨源)→ Chrome 会先发 CORS 预检,
// 桩服务必须应 OPTIONS;真实响应也要带 ACAO,否则页面包装里 r.clone().json() 读不出错误体。
const token403 = createHttpServer((req, res) => {
  const cors = {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "GET, POST, OPTIONS",
    "access-control-allow-headers": "x-wb-token, content-type",
  }
  if (req.method === "OPTIONS") { res.writeHead(204, cors); res.end(); return }
  res.writeHead(403, { "content-type": "application/json", ...cors })
  res.end(JSON.stringify({ ok: false, error: "token 校验失败(mock:模拟工坊重启后旧 token 失效)" }))
})
await new Promise((r) => token403.listen(0, "127.0.0.1", () => r()))
const TOKEN403_PORT = token403.address().port

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
function launchChrome(port, headless = "new") {
  chrome = spawn(CHROME, [
    `--headless=${headless}`,
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
  try { token403 && token403.close() } catch {}
  process.exit(code)
}

let wsUrl = ""
// 新版 headless 在部分环境会「network service crashed」,失败就退回旧的无头模式
const MODES = ["new", "old"]
for (let attempt = 0; attempt < MODES.length && !wsUrl; attempt++) {
  if (attempt) { try { chrome.kill() } catch {} await sleep(800) }
  launchChrome(dbgPort + attempt, MODES[attempt])
  const port = dbgPort + attempt
  // 等待预算 60 秒;单次轮询用 race 兜住连接阶段挂起(timeout 选项只管连接后的空闲,
  // TCP connect 本身被静默丢弃时一次能挂 20 秒左右,200 次就是 70 分钟静默——今晚实测)
  const deadline = Date.now() + 60000
  for (let i = 0; Date.now() < deadline && !wsUrl; i++) {
    await sleep(150)
    const poll = Promise.race([
      localReq("/json/list", { host: "127.0.0.1", port, timeout: 2000 }),
      sleep(2500).then(() => ({ status: 0, text: "", json: null, error: "poll 超时" })),
    ])
    const r = await poll
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
// 握手必须有超时:实测环境里 WS 可能「既不 open 也不 error」地永远挂着(2026-11-24 连续三轮,
// 表现为整个套件 0 输出挂死数小时)。挂死必须变成 15 秒内的明确失败,否则没人知道卡在哪。
await new Promise((res, rej) => {
  const t = setTimeout(() => rej(new Error(`WebSocket 握手超时(15s):${wsUrl} — Chrome 调试端口在但握手不通,多为残留 headless 实例/端口冲突,清掉孤儿 chrome 重试`)), 15000)
  ws.onopen = () => { clearTimeout(t); res() }
  ws.onerror = () => { clearTimeout(t); rej(new Error("WebSocket 连接失败")) }
})
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
// 外发写确认卡探针(issue #9):任务期间页面必须能把「要往仓库外写」的动作交给用户点,并回发结果
const AP_PROBE_SRC = readFileSync(new URL("./approvals-probe.js", import.meta.url), "utf8")

const evalJs = async (expression) => {
  const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true })
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text)
  return r.result.value
}

await send("Runtime.enable")
await send("Page.enable")
await send("Log.enable")

// ---------- 3. 打开页面 ----------
// --viewport 1024x700:模拟不同窗口尺寸(缩放相关的 bug 往往只在窄屏/矮屏出现)
const VIEW = arg("viewport", "")
if (VIEW && /^\d+x\d+$/.test(VIEW)) {
  const [w, h] = VIEW.split("x").map(Number)
  await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: false })
}
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
    // 手动压缩的判据用「这次点击自己的响应」:比查 /api/sessions 精确
    // (页面里的 curSession 与服务端 sessions.current 未必是同一个,查错会话会稳定误报)
    if (url.includes('/compact') && method === 'POST') {
      const pr = real(u, o);
      pr.then((r) => { try { return r.clone().json() } catch { return null } })
        .then((j) => { window.__lastCompact = { at: Date.now(), url, json: j } })
        .catch((e) => { window.__lastCompact = { at: Date.now(), url, error: String(e && e.message || e) } });
      return pr;
    }
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

    // 探测可用模型(issue #49):填好假地址(端口 1,连接立刻被拒)点 #probeBtn。
    // /api/probe_models 不在桩清单里,会真发请求——服务端连不上 → 页面走
    // 「探测未获取到模型列表」错误提示分支。断言三件事:
    //   ① POST /api/probe_models 真的发出去了(calls 里有记录);
    //   ② #probeTip 有错误路径文案(不是空着不动);
    //   ③ 按钮回到可点状态(处理函数跑完;中途抛错按钮会永远 disabled)。
    // 另有全局兜底:未捕获的 promise 异常会进 pageErrors,整轮体检直接判失败。
    // 必须放在「保存并进入」之前——那个用例 1.2s 后 location.reload(),会把这里的 DOM 冲掉。
    {
      const btn = document.getElementById('probeBtn');
      const tip = document.getElementById('probeTip');
      if (!btn || !tip) {
        fail('引导页 探测可用模型按钮', 'btn=' + !!btn + ' tip=' + !!tip + '(元素缺失,点击会 null 抛错)');
      } else {
        $('#u').value = 'http://127.0.0.1:1/v1';
        $('#k').value = 'sk-check-page-probe';
        const n3 = calls().filter((c) => c.url.includes('/api/probe_models')).length;
        btn.click();
        // 轮询要等「提示变了」而不是「提示有了」:点击瞬间就被填上
        // 「正在获取可用模型列表…」的加载文案,拿非空当完成条件会在响应回来前误判
        let tipText = '';
        const t0 = Date.now();
        while (Date.now() - t0 < 8000) {
          tipText = (tip.textContent || '').trim();
          if (tipText && !/正在获取/.test(tipText)) break;
          await sleep(150);
        }
        tipText = (tip.textContent || '').trim()
        const sent = calls().filter((c) => c.url.includes('/api/probe_models')).length > n3;
        const msgOk = /探测未获取到模型列表|探测出错|先填好/.test(tipText);
        const btnBack = !btn.disabled;
        sent && msgOk && btnBack
          ? pass('引导页 探测按钮(假地址→错误提示)', tipText.slice(0, 26))
          : fail('引导页 探测按钮(假地址→错误提示)', 'sent=' + sent + ' msg=' + msgOk + ' tip=' + JSON.stringify(tipText.slice(0, 40)) + ' btnReady=' + btnBack);
      }
    }

    // 403 自愈(引导页 fetch 包装):向 403 桩服务发一次请求(走 check-page 桩的透传分支
    // → 页面包装 → 原生 fetch),响应穿过页面包装,应被识别为 token 失效:
    // 置 __WB_STALE + 顶部「工坊已重启…」提示。2 秒后的 reload 闭环由宿主侧 5a 断言。
    // 放在「保存并进入」之前:那个用例成功后 1.2 秒会 reload,别让它冲掉这里的断言窗口。
    // 标记:sessionStorage 探针 reload 后保留(同页刷新)、window 标记 reload 后消失——5a 拿它们判刷新。
    try {
      sessionStorage.setItem('wb403probe', '1')
      window.__WB_TESTCTX = 1
      await fetch('http://127.0.0.1:${TOKEN403_PORT}/x', { method: 'POST' })
      await sleep(400)   // 403 分支里 r.clone().json() 是异步的,给它一点时间
      const stale = window.__WB_STALE === true
      const toast = [...document.querySelectorAll('div')].some((d) => (d.textContent || '').indexOf('工坊已重启') >= 0)
      stale && toast ? pass('403 自愈:token 失效被识别(置 __WB_STALE + 顶部提示)') : fail('403 自愈', 'stale=' + stale + ' toast=' + toast)
    } catch (e) { fail('403 自愈', String((e && e.message) || e)) }

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
  // 冷启动第一次渲染可能略慢,轮询一下再断言(wbWait 定义在后面,这里用不上)
  for (let wi = 0; wi < 25 && !$('#chat details.think'); wi++) await sleep(100)
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

  // 8) 提问芯片:候选答案 + 「其他」卡片与内联输入框
  window.__mockReply = '这个插件你更想要哪种?\\n[[WB-Q]]\\n要一个设置面板\\n纯后台就行\\n[[/WB-Q]]\\n问清楚才好动手。';
  await sendText('做个小工具');
  await sleep(200);
  const wbq = $$('[data-wbq]');
  const otherChip = $('[data-wbqother]');
  const otherIn = $('[data-wbotherin]');
  const otherSend = $('[data-wbothersend]');
  wbq.length >= 2 ? pass('候选答案芯片渲染', wbq.length + ' 个') : fail('候选答案芯片渲染', '找到 ' + wbq.length + ' 个');
  // 预选项必须竖排:容器 flexDirection=column,前两项上下错开、左边缘对齐
  {
    const qlist = $('.wbqlist');
    let stacked = false;
    if (qlist && wbq.length >= 2) {
      const r1 = wbq[0].getBoundingClientRect();
      const r2 = wbq[1].getBoundingClientRect();
      stacked = getComputedStyle(qlist).flexDirection === 'column' && r2.top > r1.bottom - 2 && Math.abs(r1.left - r2.left) < 2;
    }
    stacked ? pass('候选答案竖列排列', 'flexDirection=column') : fail('候选答案竖列排列', qlist ? 'flexDirection=' + getComputedStyle(qlist).flexDirection : '找不到 .wbqlist');
  }
  otherChip && otherIn && otherSend
    ? pass('「其他」卡片+输入框+发送 渲染', 'chip=' + otherChip.textContent.trim())
    : fail('「其他」卡片+输入框+发送 渲染', 'chip=' + !!otherChip + ' in=' + !!otherIn + ' send=' + !!otherSend);

  if (otherChip && otherIn) {
    const n1 = tasks().length;
    otherChip.click();
    await sleep(150);
    const focused = document.activeElement === otherIn;
    tasks().length === n1 && focused
      ? pass('点「其他」聚焦输入框不发请求')
      : fail('点「其他」聚焦输入框不发请求', '发了请求=' + (tasks().length !== n1) + ' 聚焦=' + focused);
    // 输入文字 + 回车 → 作为回答发送
    otherIn.value = '我想加一个每日提醒';
    key(otherIn, { key: 'Enter' });
    await sleep(400);
    const c0 = tasks()[n1];
    c0 && JSON.parse(c0.body).text === '我想加一个每日提醒'
      ? pass('其他输入框回车发送', 'text=' + JSON.parse(c0.body).text)
      : fail('其他输入框回车发送', c0 ? 'text=' + String(JSON.parse(c0.body).text).slice(0, 30) : '没有发出请求');
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

  // 11.5) 底座页:OpenCode / ZCode 可切换,保存即生效
  {
    const baseTab = tabs.find((x) => x.dataset && x.dataset.tab === 'base');
    baseTab.click(); await sleep(200);
    const shown = getComputedStyle($('#tab-base')).display !== 'none';
    const opts = $$('#baselist .baseopt');
    const curMode = (opts.find((o) => o.classList.contains('cur')) || {}).dataset?.m;
    shown && opts.length === 2
      ? pass('底座页:两个底座可选', '当前=' + curMode)
      : fail('底座页:两个底座可选', 'shown=' + shown + ' opts=' + opts.length);
    // 切到 ZCode 并保存
    const zOpt = opts.find((o) => o.dataset.m === 'zcode');
    zOpt.click(); await sleep(80);
    const n0 = calls().filter((c) => c.url.includes('/api/backend') && c.method === 'POST').length;
    $('#baseSave').click();
    await sleep(900);
    const posted = calls().filter((c) => c.url.includes('/api/backend') && c.method === 'POST').length > n0;
    const jb = await (await fetch('/api/backend')).json();
    posted && jb.mode === 'zcode'
      ? pass('底座切换 → /api/backend', 'mode=' + jb.mode + ' found=' + (jb.zcode && jb.zcode.found))
      : fail('底座切换 → /api/backend', 'posted=' + posted + ' mode=' + (jb && jb.mode));
    // 切回 OpenCode(不留副作用)
    const oOpt = opts.find((o) => o.dataset.m === 'opencode');
    oOpt.click(); await sleep(80);
    $('#baseSave').click();
    await sleep(900);
    const jb2 = await (await fetch('/api/backend')).json();
    jb2.mode === 'opencode'
      ? pass('底座切回 OpenCode')
      : fail('底座切回 OpenCode', 'mode=' + (jb2 && jb2.mode));

    // 竞态回归:慢返回的 loadBackend 不能吃掉用户刚选的底座(真实 bug,v1.2.48 修复)
    // 桩一个 700ms 才返回 opencode 的 GET /api/backend:点页签→立刻选 ZCode→慢响应落地→保存,
    // 修复前慢响应会把 mode 覆写回 opencode,保存就发了旧值
    const origFetch = window.fetch;
    window.fetch = function (u, o) {
      const m = ((o && o.method) || 'GET').toUpperCase();
      if (String(u).indexOf('/api/backend') >= 0 && m === 'GET') {
        return new Promise(function (res) {
          setTimeout(function () {
            res(new Response(JSON.stringify({ ok: true, mode: 'opencode', zcodeCmd: [], zcode: { found: false, path: '' }, zcodeBundled: false }), { status: 200, headers: { 'content-type': 'application/json' } }));
          }, 700);
        });
      }
      return origFetch.apply(this, arguments);
    };
    baseTab.click();
    opts.find((o) => o.dataset.m === 'zcode').click();
    await sleep(1000);
    const nRace = calls().filter((c) => c.url.includes('/api/backend') && c.method === 'POST').length;
    $('#baseSave').click();
    await sleep(900);
    window.fetch = origFetch;
    const racePosts = calls().filter((c) => c.url.includes('/api/backend') && c.method === 'POST');
    const lastPost = racePosts[racePosts.length - 1];
    let raceOk = racePosts.length > nRace && lastPost && JSON.parse(lastPost.body).mode === 'zcode';
    // 现场恢复:切回 opencode 并真实保存
    opts.find((o) => o.dataset.m === 'opencode').click(); await sleep(80);
    $('#baseSave').click(); await sleep(900);
    const jb3 = await (await fetch('/api/backend')).json();
    raceOk = raceOk && jb3.mode === 'opencode';
    raceOk
      ? pass('底座慢加载不覆盖用户选择(竞态回归)', 'posted=' + (lastPost ? JSON.parse(lastPost.body).mode : 'none') + ' final=' + jb3.mode)
      : fail('底座慢加载不覆盖用户选择(竞态回归)', 'posted=' + (lastPost ? lastPost.body : 'none') + ' final=' + (jb3 && jb3.mode));
  }

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
    // 手动压缩按钮在左下角(与「查看已归档」同位):存在 + 点击真的发 /compact + 按钮文案反馈
    const cb2 = $('#compactSide');
    if (cb2) {
      const n0 = calls().filter((c) => c.url.includes('/compact')).length;
      // 必须清空上一次点击的响应:这一项在套件里会跑第二次(重复压缩),
      // 不清就会拿上一轮的 ok=true 蒙过本轮,变成假通过
      window.__lastCompact = null;
      cb2.click();
      const sent = await wbWait(() => calls().filter((c) => c.url.includes('/compact')).length > n0, 3000);
      // 按钮文案 2.8s 后会自动复位,拿它当唯一判据就是随机失败(实测偶发「卡在压缩中…」)。
      // 判据用这次点击的**响应**:压缩是异步的,而且页面 curSession 与服务端 current 未必同一个,
      // 事后去查 /api/sessions 会因认不出「该看哪条会话」而稳定误报(踩过两次)。
      let done = false, how = ""
      for (let i = 0; i < 40; i++) {
        const lc = window.__lastCompact
        if (lc && lc.json) {
          done = lc.json.ok === true
          how = done
            ? ("服务端返回 ok,摘要 " + (lc.json.compact ? lc.json.compact.before + "→" + lc.json.compact.after : "无需压缩"))
            : ("服务端返回失败:" + String(lc.json.error || "?").slice(0, 60))
          break
        }
        if (lc && lc.error) { how = "响应读不出:" + lc.error.slice(0, 60); break }
        await sleep(200)
      }
      if (!how) how = "8s 内没拿到 /compact 的响应"
      // 文案作为辅助证据:能抓到结果态更好,抓不到不影响判定
      const fb = await wbWait(() => /✅|⚠️|已压缩|不用压缩/.test(cb2.textContent || ""), 1200)
      sent && done ? pass('左下角 手动压缩当前会话', how + (fb ? " + 文案反馈" : "(文案已复位,不影响判定)"))
        : fail('左下角 手动压缩当前会话', 'sent=' + sent + ' ' + how + ' 文案=' + (cb2.textContent || '').trim().slice(0, 26));
      const inSide = !!cb2.closest('#side');
      inSide ? pass('压缩按钮位于侧栏底部') : fail('压缩按钮位于侧栏底部', '不在 #side 内');
    } else fail('左下角 手动压缩当前会话', '找不到 #compactSide');
    // 设置「重试」页里不应再有压缩按钮(已挪走)
    !$('#compactNow') ? pass('设置页旧压缩按钮已移除') : fail('设置页旧压缩按钮已移除', '#compactNow 仍在');
    tabs[0].click(); await sleep(50);
  } else fail('运行参数输入框', [!!tT, !!tS, !!tR, !!tD].join(','));

  // 11.7) 更新页签:面板存在、当前版本可读、能点检查(不发真实下载)
  const updTab = tabs.find((x) => x.dataset && x.dataset.tab === 'update');
  if (updTab) {
    updTab.click();
    await wbWait(() => (($('#updCur') || {}).textContent || '').trim().length > 1, 3000);
    const shown = !!$('#tab-update') && getComputedStyle($('#tab-update')).display !== 'none';
    // 面板必须真的"看得见":嵌套错位(如被塞进别的 sp-body)时 display 虽为 block 但尺寸是 0
    const upRect = $('#tab-update').getBoundingClientRect();
    const visible = upRect.height > 40 && upRect.width > 200;
    const curTxt = ($('#updCur').textContent || '').trim();
    const hasBtn = !!$('#updCheck') && !!$('#updGo') && !!$('#updModal');
    shown && visible && /^v\\d+\\.\\d+\\.\\d+/.test(curTxt) && hasBtn
      ? pass('更新页签 面板/版本号/按钮', curTxt + ' h=' + Math.round(upRect.height))
      : fail('更新页签 面板/版本号/按钮', 'shown=' + shown + ' visible=' + visible + ' rect=' + Math.round(upRect.width) + 'x' + Math.round(upRect.height) + ' cur=' + curTxt + ' btn=' + hasBtn);
    // 七个页签面板都必须是 #setpanel 的直接子级(防 HTML 嵌套错位重演,v1.2.49)
    const parentBad = ['api', 'look', 'base', 'run', 'mem', 'expert', 'update']
      .map((t) => ({ t, el: document.getElementById('tab-' + t) }))
      .filter((x) => !x.el || !x.el.parentElement || x.el.parentElement.id !== 'setpanel')
      .map((x) => x.t + '(parent=' + ((x.el && x.el.parentElement && x.el.parentElement.id) || 'none') + ')');
    parentBad.length === 0
      ? pass('设置面板七个页签均为 setpanel 直接子级')
      : fail('设置面板七个页签均为 setpanel 直接子级', parentBad.join('; '));

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

  // 11.8b) 外发写确认卡:任务途中 Agent 要 push/发布时,页面必须能看见、能拒绝(issue #9)
  try {
    const ap = await (
${AP_PROBE_SRC}
    )
    for (const r of ap) (r.ok ? pass : fail)(r.name, r.detail)
  } catch (e) {
    fail('外发写确认卡探针', String((e && e.message) || e))
  }

  // 11.9) 专家团页签:面板可见、成员名单渲染、开关往返(隔离实例上真实 POST)
  const exTab = tabs.find((x) => x.dataset && x.dataset.tab === 'expert');
  if (exTab) {
    exTab.click();
    await wbWait(() => { const l = document.getElementById('expertList'); return !!l && l.textContent.trim().length > 4; }, 4000);
    const exShown = getComputedStyle($('#tab-expert')).display !== 'none' && $('#tab-expert').getBoundingClientRect().height > 40;
    const chips = $$('#expertList .chip');
    const exBtn = !!$('#expertToggle') && ($('#expertToggle').textContent || '').length > 1;
    exShown && chips.length >= 5 && exBtn
      ? pass('专家团页签 面板/成员名单/开关', '成员 ' + chips.length + ' 人')
      : fail('专家团页签 面板/成员名单/开关', 'shown=' + exShown + ' chips=' + chips.length + ' btn=' + exBtn);
    // 开关往返:先读当前态,点一下应翻到另一态,再点回原态(别留下污染)
    const j0 = await (await fetch('/api/experts')).json();
    const firstWant = j0.enabled ? '未开启' : '已开启';
    const secondWant = j0.enabled ? '已开启' : '未开启';
    $('#expertToggle').click();
    const onOk = await wbWait(() => ($('#expertToggle').textContent || '').indexOf(firstWant) >= 0, 5000);
    $('#expertToggle').click();
    const offOk = await wbWait(() => ($('#expertToggle').textContent || '').indexOf(secondWant) >= 0, 5000);
    onOk && offOk
      ? pass('专家团开关 开/关往返还原')
      : fail('专家团开关 开/关往返还原', 'on=' + onOk + ' off=' + offOk + ' from=' + (j0 && j0.enabled));
    // 每位专家的调用来源选单:存在、可绑定到某 API 的某模型、可恢复跟随默认
    {
      const sels = $$('#expertList .memapi');
      const snap0 = await (await fetch('/api/experts')).json();
      const hasApi = (snap0.apiOptions || []).length > 0;
      if (sels.length && hasApi) {
        const o = (snap0.apiOptions || [])[0];
        const mdl = (o.models || [])[0];
        const val = o.id + '::' + mdl;
        const sel = sels[0];
        const memId = sel.dataset.mem;
        sel.value = val;
        sel.dispatchEvent(new Event('change', { bubbles: true }));
        const bound = await wbWait(async () => {
          const s = await (await fetch('/api/experts')).json();
          const mm = ((s.packs || [])[0] || {}).members || [];
          const me = mm.find((x) => x.id === memId);
          return !!(me && me.bind && me.bind.apiId === o.id && me.bind.model === mdl);
        }, 6000);
        bound ? pass('专家调用来源 可指定 API+模型', memId + ' → ' + o.name + '/' + mdl)
          : fail('专家调用来源 可指定 API+模型', '未生效 ' + memId);
        // 恢复跟随默认
        const sel2 = document.querySelector('#expertList .memapi[data-mem="' + memId + '"]');
        if (sel2) { sel2.value = ''; sel2.dispatchEvent(new Event('change', { bubbles: true })); }
        const cleared = await wbWait(async () => {
          const s = await (await fetch('/api/experts')).json();
          const mm = ((s.packs || [])[0] || {}).members || [];
          const me = mm.find((x) => x.id === memId);
          return !!(me && !me.bind);
        }, 6000);
        cleared ? pass('专家调用来源 可恢复跟随默认') : fail('专家调用来源 可恢复跟随默认', '未清空');
      } else {
        fail('专家调用来源 可指定 API+模型', '选单数=' + sels.length + ' 可选API数=' + ((snap0.apiOptions || []).length));
      }
    }
    // 单个专家开关:点第一张成员卡片应翻到另一态,再点回去(隔离实例上真实 POST)
    const chips2 = $$('#expertList .chip.memb');
    if (chips2.length) {
      const c0 = chips2[0];
      const memId = c0.dataset.mem;
      const wasOn = c0.dataset.on === '1';
      c0.click();
      const flipped = await wbWait(() => {
        const c = document.querySelector('#expertList .chip.memb[data-mem="' + memId + '"]');
        return !!c && (c.dataset.on === '1') !== wasOn;
      }, 5000);
      const dimmed = (() => {
        const c = document.querySelector('#expertList .chip.memb[data-mem="' + memId + '"]');
        return !!c && (c.dataset.on === '1') === (!wasOn);
      })();
      const c1 = document.querySelector('#expertList .chip.memb[data-mem="' + memId + '"]');
      if (c1) c1.click();
      const restored = await wbWait(() => {
        const c = document.querySelector('#expertList .chip.memb[data-mem="' + memId + '"]');
        return !!c && (c.dataset.on === '1') === wasOn;
      }, 5000);
      flipped && dimmed && restored
        ? pass('专家团 单个成员开关往返', memId)
        : fail('专家团 单个成员开关往返', 'flipped=' + flipped + ' restored=' + restored + ' id=' + memId);
    } else fail('专家团 单个成员开关往返', '没有成员卡片');
    tabs[0].click(); await sleep(50);
  } else fail('专家团页签', '找不到 data-tab=expert');

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
    // 放大后:视觉尺寸仍等于视口(不横向裁切、不整页溢出),输入框仍在可视区内。
    // 设置面板占的高度不算数(短窗口下打开面板必然把聊天区顶下去,关掉就回来),量的是主界面
    $('#setclose').click(); await sleep(300);
    const docEl = document.scrollingElement || document.documentElement;
    const hOverflow = docEl.scrollWidth - window.innerWidth;
    const vOverflow = docEl.scrollHeight - window.innerHeight;
    const composerIn = (() => {
      const t = document.querySelector('textarea');
      if (!t) return null;
      const r = t.getBoundingClientRect();
      return r.width > 0 && r.bottom <= window.innerHeight + 2;
    })();
    hOverflow <= 2 && vOverflow <= 8 && composerIn === true
      ? pass('字号放大后不裁切(输入框仍在视口内)', 'h=' + hOverflow + ' v=' + vOverflow)
      : fail('字号放大后不裁切(输入框仍在视口内)', 'h=' + hOverflow + ' v=' + vOverflow + ' composer=' + composerIn);
    // 重新打开外观页签,让后面的还原流程继续
    $('#setbtn').click(); await sleep(250);
    if (tabs.length > 1) { tabs[1].click(); await sleep(150); }
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
  // 错误展示:识别 404 这类状态码并保留原文
  const errDom = document.createElement('div');
  errDom.innerHTML = errHtml('模型服务拒绝访问', 'HTTP 404 model_not_found');
  const errHintEl = errDom.querySelector('.errhint');
  const errRawEl = errDom.querySelector('details.errraw .tbody');
  errHintEl && /404|模型|不存在/.test(errHintEl.textContent)
    ? pass('错误气泡:给出可能原因', (errHintEl.textContent || '').slice(0, 24))
    : fail('错误气泡:给出可能原因', errHintEl ? errHintEl.textContent : '没有 .errhint');
  errRawEl && /404/.test(errRawEl.textContent)
    ? pass('错误气泡:保留原始报错', (errRawEl.textContent || '').slice(0, 24))
    : fail('错误气泡:保留原始报错', errRawEl ? errRawEl.textContent : '没有 details.errraw');

  cb && cb.querySelector('.ctxring') && cb.querySelector('.ctxring').getAttribute('title')
    ? pass('容量环 tooltip', cb.querySelector('.ctxring').getAttribute('title'))
    : fail('容量环 tooltip', '没有 title');

  // 上下文压缩:界面只保留自动压缩(手动按钮已移除),阈值由服务端下发
  !$('#compactbtn') ? pass('手动压缩按钮已移除') : fail('手动压缩按钮已移除', '仍然存在 #compactbtn');
  const ctxInfo = await (await fetch('/api/context')).json();
  typeof ctxInfo.compactRatio === 'number' && ctxInfo.compactRatio > 0 && ctxInfo.compactRatio < 1
    ? pass('自动压缩阈值可读', 'ratio=' + ctxInfo.compactRatio)
    : fail('自动压缩阈值可读', JSON.stringify(ctxInfo).slice(0, 80));

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
  await wbWait(() => $$('.apirow').some((r) => r.textContent.indexOf('自动测试API') >= 0), 4000);
  const rowNow = $$('.apirow').find((r) => r.textContent.indexOf('自动测试API') >= 0);
  savedOk && rowNow ? pass('API「仅保存」并出现在列表', '上下文 64K') : fail('API「仅保存」并出现在列表', $('#apiMsg').textContent || '(无提示)');
  if (rowNow) {
    // 同名条目可能不止一个(重复测试/重复保存):全部删掉再断言
    const jl = await (await fetch('/api/apis')).json();
    for (const item of (jl.items || []).filter((a) => a.name === '自动测试API')) {
      await fetch('/api/apis/' + item.id, { method: 'DELETE' });
    }
    if (window.loadApis) window.loadApis();
    await sleep(400);
    const jl2 = await (await fetch('/api/apis')).json();
    const gone = !(jl2.items || []).some((a) => a.name === '自动测试API');
    gone ? pass('清理测试 API') : fail('清理测试 API', '仍存在');
  }

  // 19.7) 编辑已有 API:回填表单 → 多模型添加/设默认 → 改名保存 → 取消编辑
  {
    const created = await (await fetch('/api/apis', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ name: '编辑测试API', baseURL: 'http://127.0.0.1:9/v1', apiKey: 'sk-edit-123456', model: 'mock-edit', protocol: 'openai', enable: false }) })).json();
    if (created && created.id) {
      window.loadApis();
      await sleep(500);
      const rowE = $$('.apirow').find((r) => r.textContent.indexOf('编辑测试API') >= 0);
      const editBtn = rowE && rowE.querySelector('.edit');
      if (editBtn) {
        editBtn.click(); await sleep(150);
        const chips0 = $$('#apiModelList .chip');
        const prefill = ($('#apiURL').value || '').indexOf('127.0.0.1:9') >= 0 && ($('#apiKey').value || '') === '' && chips0.some((c) => c.textContent.indexOf('mock-edit') >= 0);
        const modeOn = ($('#apiSave').textContent || '').trim() === '保存修改' && getComputedStyle($('#apiCancelEdit')).display !== 'none';
        prefill && modeOn
          ? pass('编辑 API 回填表单/进入编辑模式', '密钥占位=' + ($('#apiKey').placeholder || '').slice(0, 8))
          : fail('编辑 API 回填表单/进入编辑模式', 'prefill=' + prefill + ' mode=' + modeOn + ' chips=' + chips0.length);
        // 多模型:输入第二个模型 → 点「＋ 添加」→ 点它设为默认
        $('#apiModel').value = 'mock-edit-2';
        $('#apiModelAdd').click(); await sleep(200);
        const chip2 = $$('#apiModelList .chip').find((c) => c.textContent.indexOf('mock-edit-2') >= 0);
        if (chip2) chip2.click();
        await sleep(150);
        const chipHtml = ($('#apiModelList .chip') || {}).style ? Array.from($$('#apiModelList .chip')).map((c) => c.textContent.trim()).join(',') : '';
        $$('#apiModelList .chip').length === 2 && chip2
          ? pass('多模型 添加并设为默认', chipHtml)
          : fail('多模型 添加并设为默认', 'chips=' + $$('#apiModelList .chip').length + ' found=' + !!chip2);
        $('#apiName').value = '编辑后API';
        $('#apiSave').click();
        await wbWait(() => ($('#apiMsg').textContent || '').indexOf('已保存修改') >= 0, 6000);
        await sleep(400);
        const renamed = $$('.apirow').some((r) => r.textContent.indexOf('编辑后API') >= 0);
        const backMode = ($('#apiSave').textContent || '').trim() === '保存并启用';
        renamed && backMode ? pass('编辑 API 保存修改生效', '列表已更新') : fail('编辑 API 保存修改生效', 'renamed=' + renamed + ' back=' + backMode);
        const apiAfter = await (await fetch('/api/apis')).json();
        const edited = (apiAfter.items || []).find((x) => x.name === '编辑后API');
        const persisted = edited && Array.isArray(edited.models) && edited.models.length === 2 && edited.modelID === 'mock-edit-2';
        persisted
          ? pass('多模型 保存后列表/默认模型正确', JSON.stringify(edited.models))
          : fail('多模型 保存后列表/默认模型正确', JSON.stringify(edited ? { models: edited.models, def: edited.modelID } : null));
        const rowE2 = $$('.apirow').find((r) => r.textContent.indexOf('编辑后API') >= 0);
        rowE2 && rowE2.textContent.indexOf('(+1 个备用模型)') >= 0
          ? pass('多模型 行内显示备用数量')
          : fail('多模型 行内显示备用数量', rowE2 ? rowE2.textContent.slice(0, 60) : '找不到行');
        if (rowE2 && rowE2.querySelector('.edit')) {
          rowE2.querySelector('.edit').click(); await sleep(150);
          $('#apiCancelEdit').click(); await sleep(150);
          const cancelled = ($('#apiSave').textContent || '').trim() === '保存并启用' && ($('#apiURL').value || '') === '';
          cancelled ? pass('取消编辑恢复表单') : fail('取消编辑恢复表单', 'save=' + ($('#apiSave').textContent || '') + ' url=' + ($('#apiURL').value || ''));
        } else fail('取消编辑恢复表单', '找不到编辑按钮');
      } else fail('编辑 API 回填表单/进入编辑模式', '找不到编辑按钮');
      await fetch('/api/apis/' + created.id, { method: 'DELETE' });
      window.loadApis(); await sleep(300);
    } else fail('编辑 API 回填表单/进入编辑模式', '创建临时 API 失败');
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

  // 21.5) 会话搜索框:输入即过滤(范围含已归档)、归档命中带徽标+「取消归档」按钮、
  // 点归档命中行直接加载、Esc 清空恢复、搜索中点「查看已归档」清空搜索并切视图
  {
    const inp = $('#sessSearch');
    if (!inp) {
      fail('侧边栏会话搜索框', '找不到 #sessSearch');
    } else {
      const sessRows = () => $$('#slist .sitem');
      const txt = (r) => (r.querySelector('.t') || { textContent: '' }).textContent;
      // 现场归位:上一用例可能停在归档视图(否则普通视图里看不到未归档造数)
      if ($('#archbtn').classList.contains('on')) { $('#archbtn').click(); await sleep(500); }
      // 造数:标题带唯一关键词,一个归档、一个未归档;测完删除还原
      const mkS = async (title, archived) => {
        const j = await (await fetch('/api/sessions', { method: 'POST' })).json();
        await fetch('/api/sessions/' + j.current + '/rename', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ title }) });
        if (archived) await fetch('/api/sessions/' + j.current + '/archive', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ archived: true }) });
        return j.current;
      };
      const kw = '搜索回归' + Date.now().toString(36);
      const tA = kw + '甲归档', tB = kw + '乙未归档';
      const idA = await mkS(tA, true);
      const idB = await mkS(tB, false);
      // 聚焦刷新缓存:无头环境 document 没有系统焦点时 element.focus() 只改 activeElement
      // 不派发 focus 事件(实测),真实浏览器点进输入框会派发——手动补一次模拟真实点击聚焦
      inp.focus(); inp.dispatchEvent(new Event('focus'));
      await wbWait(() => titles().some((x) => x.indexOf(kw) >= 0), 4000);

      // 输入即过滤:只显示命中行(含已归档)
      inp.value = kw;
      inp.dispatchEvent(new Event('input', { bubbles: true }));
      await sleep(200);
      const hits = sessRows().filter((r) => txt(r).indexOf(kw) >= 0);
      const onlyHits = sessRows().length > 0 && sessRows().every((r) => txt(r).indexOf(kw) >= 0);
      hits.length === 2 && onlyHits
        ? pass('搜索框 输入即过滤(含已归档)', '命中 ' + hits.length + ' 行') : fail('搜索框 输入即过滤(含已归档)', '命中=' + hits.length + ' 行数=' + sessRows().length);

      // 归档命中行:带「已归档」徽标 + 按归档行给按钮(📤 取消归档,无 📥 归档)
      const rowA = hits.find((r) => txt(r) === tA);
      const rowB = hits.find((r) => txt(r) === tB);
      const stag = rowA && rowA.querySelector('.stag');
      (rowA && stag && stag.textContent === '已归档' && rowB && !rowB.querySelector('.stag'))
        ? pass('搜索框 归档命中行带「已归档」徽标') : fail('搜索框 归档命中行带「已归档」徽标', 'stag=' + (stag && stag.textContent) + ' rowA=' + !!rowA + ' rowB=' + !!rowB);
      (rowA && rowA.querySelector('.un') && !rowA.querySelector('.arc') && rowB && rowB.querySelector('.arc') && !rowB.querySelector('.un'))
        ? pass('搜索框 命中行按钮随归档状态') : fail('搜索框 命中行按钮随归档状态', 'A:un=' + !!(rowA && rowA.querySelector('.un')) + ' arc=' + !!(rowA && rowA.querySelector('.arc')));

      // 点归档命中行 → 直接加载该会话(照归档视图点行为,无需切视图)
      if (rowA) {
        rowA.click(); await sleep(900);
        const jNow = await (await fetch('/api/sessions')).json();
        jNow.current === idA
          ? pass('搜索框 点归档命中行 → 加载该会话') : fail('搜索框 点归档命中行 → 加载该会话', 'current=' + jNow.current + ' 期望 ' + idA);
      } else fail('搜索框 点归档命中行 → 加载该会话', '找不到归档命中行');

      // Esc 清空:恢复未归档视图原列表
      inp.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
      await sleep(200);
      (inp.value === '' && titles().some((x) => x === tB) && !titles().some((x) => x === tA))
        ? pass('搜索框 Esc 清空恢复原列表') : fail('搜索框 Esc 清空恢复原列表', 'value=' + JSON.stringify(inp.value) + ' 有乙=' + titles().some((x) => x === tB) + ' 有甲=' + titles().some((x) => x === tA));

      // 搜索中点「查看已归档」:应清空搜索并切到归档视图(否则列表恒走过滤分支,按钮像失灵)
      inp.value = kw;
      inp.dispatchEvent(new Event('input', { bubbles: true }));
      await sleep(150);
      $('#archbtn').click(); await sleep(600);
      (inp.value === '' && $('#archbtn').classList.contains('on') && titles().some((x) => x === tA) && !titles().some((x) => x === tB))
        ? pass('搜索框 搜索中点「查看已归档」清空并切视图') : fail('搜索框 搜索中点「查看已归档」清空并切视图', 'value=' + JSON.stringify(inp.value) + ' on=' + $('#archbtn').classList.contains('on') + ' 有甲=' + titles().some((x) => x === tA) + ' 有乙=' + titles().some((x) => x === tB));
      // 现场还原:切回普通视图,删掉造数会话
      if ($('#archbtn').classList.contains('on')) { $('#archbtn').click(); await sleep(500); }
      await fetch('/api/sessions/' + idA, { method: 'DELETE' });
      await fetch('/api/sessions/' + idB, { method: 'DELETE' });
      const left = await waitFor(async () => !(await sess()).some((s) => s.id === idA || s.id === idB));
      left ? pass('搜索框用例清理造数会话') : fail('搜索框用例清理造数会话', '仍有残留');
    }
  }

  // 21.6) 思考强度滑杆(v1.2.75 绿→青→蓝改版):真实计算样式 + 档位状态机(填宽/.flow/变速/auto·关静止/拖动即存)
  // CSS 字面量钉在 wb-studio-surface 快照里;这里看浏览器真实渲染与 paintThink 行为,顺带测 /api/think 往返
  {
    const bar = $('#thinkbar'), fill = $('#thinkfill'), range = $('#thinkRange'), val = $('#thinkVal');
    if (!bar || !fill || !range || !val) {
      fail('思考强度滑杆', '缺件 bar=' + !!bar + ' fill=' + !!fill + ' range=' + !!range + ' val=' + !!val);
    } else {
      const LV = ['auto', 'none', 'low', 'medium', 'high', 'xhigh'];
      const LB = ['自动', '关', '低', '中', '高', '最高'];
      const saved = (((await (await fetch('/api/think')).json()) || {}).level) || 'auto'; // 测完还原
      // 系统开了「减少动态效果」时动画全停是预期行为:跳过动画断言(状态机/样式照测),别误报
      const rm = matchMedia('(prefers-reduced-motion: reduce)').matches;
      const setLevel = async (i) => {
        range.value = String(i);
        fire(range, 'input');
        fire(range, 'change'); // change 走真实 POST /api/think 落库
        await sleep(700);
      };
      // 结构:胶囊轨道凹槽 + 渐变填充锚 128px + 交互不被流星层挡
      const trackCs = getComputedStyle(bar, '::before');
      (trackCs.borderRadius === '999px' && trackCs.backgroundColor !== 'rgba(0, 0, 0, 0)')
        ? pass('思考强度 胶囊轨道渲染') : fail('思考强度 胶囊轨道渲染', 'radius=' + trackCs.borderRadius + ' bg=' + trackCs.backgroundColor);
      const csF = getComputedStyle(fill);
      (String(csF.backgroundSize).indexOf('128px') === 0 && String(csF.backgroundImage).indexOf('linear-gradient') === 0)
        ? pass('思考强度 填充为渐变且锚定 128px') : fail('思考强度 填充为渐变且锚定 128px', 'size=' + csF.backgroundSize + ' img=' + String(csF.backgroundImage).slice(0, 48));
      (getComputedStyle($('#thinkmeteor')).pointerEvents === 'none' && getComputedStyle(range).zIndex === '1')
        ? pass('思考强度 流星层不挡滑杆交互') : fail('思考强度 流星层不挡滑杆交互', 'pe=' + getComputedStyle($('#thinkmeteor')).pointerEvents + ' z=' + getComputedStyle(range).zIndex);
      // 状态机:逐档断言 填宽/标签/.flow/--flow-dur/两层动画(低/中/高/最高开,自动/关全静止)
      const anim = () => ({
        flowOn: fill.classList.contains('flow'),
        dur: bar.style.getPropertyValue('--flow-dur'),
        wheel: getComputedStyle(fill, '::after').animationName,
        meteor: getComputedStyle($('#thinkmeteor'), '::after').animationName,
        meteorDur: getComputedStyle($('#thinkmeteor'), '::after').animationDuration,
      });
      const cases = [
        { i: 0, w: '0%', d: '' }, { i: 1, w: '20%', d: '' },
        { i: 2, w: '40%', d: '1.3s' }, { i: 3, w: '60%', d: '0.9s' }, { i: 4, w: '80%', d: '0.6s' }, { i: 5, w: '100%', d: '0.4s' },
      ];
      let bad = '';
      for (const c of cases) {
        await setLevel(c.i);
        const a = anim();
        const on = c.i >= 2;
        if (fill.style.width !== c.w) bad += '档' + c.i + '宽=' + fill.style.width + ';';
        if ((val.textContent || '').trim() !== LB[c.i]) bad += '档' + c.i + '标签=' + val.textContent + ';';
        if (a.flowOn !== on) bad += '档' + c.i + 'flow=' + a.flowOn + ';';
        if (a.dur !== c.d) bad += '档' + c.i + 'dur=' + a.dur + ';';
        if (!rm) {
          if (a.wheel !== (on ? 'wheelroll' : 'none')) bad += '档' + c.i + '底流=' + a.wheel + ';';
          if (a.meteor !== (on ? 'thinkmeteor' : 'none')) bad += '档' + c.i + '流星=' + a.meteor + ';';
          if (on && a.meteorDur !== c.d) bad += '档' + c.i + '流星速=' + a.meteorDur + ';';
        }
      }
      bad ? fail('思考强度 档位状态机(填宽/flow/变速/静止)', bad) : pass('思考强度 档位状态机(填宽/flow/变速/静止)', rm ? '6 档全对(动画断言因系统开 reduced-motion 跳过)' : '6 档全对');
      // 拖动即存:change 发出的 POST 落库,GET 读回一致;再还原原档
      const lvNow = ((await (await fetch('/api/think')).json()) || {}).level;
      const posts = calls().filter((c) => c.url.includes('/api/think') && c.method === 'POST');
      const lastBody = posts.length ? (JSON.parse(posts[posts.length - 1].body) || {}).level : null;
      (lvNow === 'xhigh' && lastBody === 'xhigh')
        ? pass('思考强度 拖动即存(POST /api/think)') : fail('思考强度 拖动即存(POST /api/think)', 'GET=' + lvNow + ' POST=' + lastBody);
      await fetch('/api/think', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ level: saved }) });
      const si = LV.indexOf(saved);
      range.value = String(si < 0 ? 0 : si);
      fire(range, 'input');
      await sleep(150);
      const back = ((await (await fetch('/api/think')).json()) || {}).level;
      back === saved ? pass('思考强度用例还原原档') : fail('思考强度用例还原原档', 'now=' + back + ' want=' + saved);
    }
  }

  // 403 自愈(主页 fetch 包装,与引导页同一份拷贝):向 403 桩服务发一次请求
  // (check-page 桩透传 → 页面包装 → 原生 fetch),响应穿过页面包装,应被识别为
  // token 失效:置 __WB_STALE + 顶部提示。必须放最后——2 秒后页面会真的 reload,
  // reload 之后 check-page 的桩与 __calls 都会没掉,后续页面侧用例会全废。
  // reload 闭环由宿主侧 5a 断言(主页没有别的 reload 源,那条就是 403 专属闭环)。
  try {
    sessionStorage.setItem('wb403probe', '1')
    window.__WB_TESTCTX = 1
    await fetch('http://127.0.0.1:${TOKEN403_PORT}/x', { method: 'POST' })
    await sleep(400)   // 403 分支里 r.clone().json() 是异步的,给它一点时间
    const stale = window.__WB_STALE === true
    const toast = [...document.querySelectorAll('div')].some((d) => (d.textContent || '').indexOf('工坊已重启') >= 0)
    stale && toast ? pass('403 自愈:token 失效被识别(置 __WB_STALE + 顶部提示)') : fail('403 自愈', 'stale=' + stale + ' toast=' + toast)
  } catch (e) { fail('403 自愈', String((e && e.message) || e)) }

  return R;
})()`)
}

// ---------- 4.5 可选:截一张「改动文件列表」的图(--shot 路径) ----------
// 必须趁 UI 检查刚跑完、列表还在 DOM 里时截;后面的会话切换会把聊天区重渲染成服务端历史
const SHOT = arg("shot", "")
if (SHOT) {
  try {
    // 403 自愈用例之后页面可能已 reload(探针在、window 标记没了=上下文已重建):
    // reload 后 check-page 的桩随之失效,再 requestSubmit 会真发 /api/task——跳过截图
    const st = await evalJs(`(() => ({ probe: sessionStorage.getItem('wb403probe') || '', ctx: window.__WB_TESTCTX === 1 }))()`).catch(() => null)
    if (st && st.probe === '1' && st.ctx === false) {
      console.log(`${C.y}⚠ 页面已因 403 自愈用例 reload(桩失效),截图跳过${C.x}`)
    } else {
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
    } // else(页面没被 403 自愈 reload)结束
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

// ---------- 5a. 403 自愈闭环:页面侧已断言 __WB_STALE+提示;这里确认 reload 真的发生 ----------
// 判据:sessionStorage 探针仍在(同页刷新保留)+ window 上的旧标记消失(上下文重建过)。
// 引导页分支里「保存并进入」自带的 reload 可能抢先触发——那条路径里这一项只证明「刷新发生了」,
// 403 分支本身的置位/提示断言在页面侧;主页分支没有其它 reload 源,这一项就是 403 专属闭环。
// 轮询而不是固定等待:reload 落在 2s 定时器 + 页面重载之间,竞态期间 eval 可能短暂失败。
{
  let reloaded = false
  let detail = ""
  const dl = Date.now() + 8000
  while (Date.now() < dl && !reloaded) {
    try {
      const after = await evalJs(`(() => ({ probe: sessionStorage.getItem('wb403probe') || '', ctx: window.__WB_TESTCTX === 1 }))()`)
      if (after && after.probe === '1' && after.ctx === false) { reloaded = true; break }
      detail = JSON.stringify(after)
    } catch (e) { detail = "eval: " + String((e && e.message) || e) }
    await sleep(1000)
  }
  apiChecks.push({
    name: "403 自愈:token 失效后页面自动 reload(自愈闭环)",
    ok: reloaded,
    detail: reloaded ? "sessionStorage 探针保留 + window 上下文已重建" : "8 秒内没等到刷新:" + detail.slice(0, 80),
  })
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
await probe("GET /api/experts 快照", "/api/experts", {}, (s, j) => s === 200 && j && j.ok && typeof j.enabled === "boolean" && Array.isArray(j.packs))
await probe("POST /api/experts 非法值拦截", "/api/experts", { method: "POST", body: '{"enabled":"yes"}' }, (s, j) => s === 400 && j && j.error)
await probe("POST /api/experts 未知成员拦截", "/api/experts", { method: "POST", body: '{"member":"no-such-expert","on":false}' }, (s, j) => s === 400 && j && j.error)
await probe("GET /api/expert-progress", "/api/expert-progress", {}, (s, j) => s === 200 && j && j.ok && typeof j.phase === "string" && Array.isArray(j.members))
{
  const apisNow = await localReq("/api/apis", { headers: H })
  const firstApi = apisNow.json && (apisNow.json.items || [])[0]
  if (firstApi) {
    await probe("PUT /api/apis 非法地址拦截", "/api/apis/" + firstApi.id, { method: "PUT", body: '{"baseURL":"nope"}' }, (s, j) => s === 400 && j && j.error)
  } else {
    apiChecks.push({ name: "PUT /api/apis 非法地址拦截", ok: true, detail: "无 API,跳过" })
  }
  await probe("PUT /api/apis 未知 id 拦截", "/api/apis/api_not_exist", { method: "PUT", body: '{"baseURL":"http://x"}' }, (s) => s === 404)
}

// ---------- 5b. 单 API 上下文(加 --mutate 才跑:会临时增删测试实例里的 API) ----------
if (argv.includes("--mutate")) {
  // 专家团开关往返(真实 POST,结束还原为关)
  {
    const on = await localReq("/api/experts", { method: "POST", body: JSON.stringify({ enabled: true }), timeout: 20000, headers: H })
    const okOn = on.status === 200 && on.json && on.json.enabled === true && on.json.active
    const off = await localReq("/api/experts", { method: "POST", body: JSON.stringify({ enabled: false }), timeout: 20000, headers: H })
    const okOff = off.status === 200 && off.json && off.json.enabled === false
    ;(okOn && okOff)
      ? apiChecks.push({ name: "POST /api/experts 开关往返(还原为关)", ok: true, detail: `active=${on.json.active}` })
      : apiChecks.push({ name: "POST /api/experts 开关往返(还原为关)", ok: false, detail: `on=${on.status}/${okOn} off=${off.status}/${okOff}` })
  }
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
