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
import { mkdtempSync, rmSync, existsSync, readFileSync, writeFileSync } from "node:fs"
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

// ---------- 1. 起无头浏览器(启动偶发失败时自动重试一次) ----------
const profile = mkdtempSync(join(tmpdir(), "wb-check-"))
let chrome = null
function launchChrome() {
  chrome = spawn(CHROME, [
    "--headless=new",
    "--remote-debugging-port=0",
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
  if (attempt) { try { chrome.kill() } catch {} await sleep(500) }
  launchChrome()
  for (let i = 0; i < 150 && !wsUrl; i++) {
    await sleep(100)
    const f = join(profile, "DevToolsActivePort")
    if (!existsSync(f)) continue
    // 文件首行形如 "57520,/devtools/browser/<id>",端口在逗号前
    const port = Number(String(readFileSync(f, "utf8")).split(/\r?\n/)[0].split(",")[0])
    if (!port) continue
    const r = await localReq("/json/list", { host: "127.0.0.1", port, timeout: 3000 })
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
  window.fetch = (u, o = {}) => {
    const url = typeof u === 'string' ? u : (u && u.url) || '';
    const method = (o.method || 'GET').toUpperCase();
    window.__calls.push({ url, method, body: o.body });   // 全量记录,便于断言"点一下有没有发请求"
    if (url.includes('/api/task') && method === 'POST') {
      return Promise.resolve(new Response(JSON.stringify({
        ok: true, reply: window.__mockReply, thinking: '想一下', modifiedFiles: [], artifacts: [],
      }), { status: 200, headers: { 'content-type': 'application/json' } }));
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

  // 18) 上下文容量条
  const cb = $('#ctxbar');
  cb && cb.textContent.trim() ? pass('上下文容量条', cb.textContent.trim().slice(0, 24)) : fail('上下文容量条', '空的');

  // 19) 侧边栏会话列表
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

  // 21) 侧边栏:归档 / 取消归档 / 重命名 / 删除(对话框打桩,避免无头浏览器把弹窗吞掉)
  const itemCount = () => $$('#slist .sitem').length;
  const beforeArc = itemCount();
  const arcBtn = $('#slist .sitem .arc');
  if (arcBtn) {
    arcBtn.click(); await sleep(900);
    const afterArc = itemCount();
    afterArc === beforeArc - 1 ? pass('侧边栏「归档」', beforeArc + ' → ' + afterArc) : fail('侧边栏「归档」', afterArc + ' 条,期望 ' + (beforeArc - 1));
    if (!$('#archbtn').classList.contains('on')) { $('#archbtn').click(); await sleep(500); }
    const un = $('#slist .sitem .un');
    un ? pass('归档视图里可「取消归档」', un.title) : fail('归档视图里可「取消归档」', '没有 .un 按钮');
    if (un) { un.click(); await sleep(900); }
    if ($('#archbtn').classList.contains('on')) { $('#archbtn').click(); await sleep(500); }
  } else fail('侧边栏「归档」', '没找到 .arc 按钮');

  window.prompt = () => '自动化改名';
  window.confirm = () => true;
  const renBtn = $('#slist .sitem .ren');
  if (renBtn) {
    const n7 = calls().filter((c) => c.url.includes('/rename')).length;
    renBtn.click(); await sleep(800);
    calls().filter((c) => c.url.includes('/rename')).length > n7 ? pass('侧边栏「重命名」→ /rename') : fail('侧边栏「重命名」', '没发出请求');
    const shown = $('#slist .sitem .t') && $('#slist .sitem .t').textContent;
    shown && shown.includes('自动化改名') ? pass('重命名后列表已刷新', shown) : fail('重命名后列表已刷新', '标题=' + shown);
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

// ---------- 5. 接口体检(不经过页面) ----------
// POST 接口要带页面注入的 x-wb-token,否则会被 403 挡住(这本身也是安全检查的一环)
const homeHtml = (await localReq("/", { timeout: 10000 })).text || ""
const TOKEN = (homeHtml.match(/const WB_TOKEN="([^"]+)"/) || [])[1] || ""
const H = TOKEN ? { "x-wb-token": TOKEN } : {}

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
