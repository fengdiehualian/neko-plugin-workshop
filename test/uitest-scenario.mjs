/**
 * wb-uitest-scenario - 端到端验证「任务进行中切换会话」不串台
 * 用法: bun test/uitest-scenario.mjs <页面URL>
 * 场景: 会话A发任务(立即返回不等待) → 切到会话B → 等任务完成 →
 *       断言: B 的聊天区没被串台渲染、侧边栏高亮仍在 B;切回 A 能看到任务结果
 */
import { spawn } from "node:child_process"
import { existsSync } from "node:fs"
import { setTimeout as sleep } from "node:timers/promises"

const URL_TARGET = process.argv[2] || "http://127.0.0.1:5099"
const CDP_PORT = 9334

const browsers = [
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
]
const browser = browsers.find((p) => existsSync(p))
if (!browser) { console.error("no chrome/edge found"); process.exit(2) }

const proc = spawn(browser, [
  `--remote-debugging-port=${CDP_PORT}`,
  "--no-proxy-server", "--headless=new", "--disable-gpu",
  "--user-data-dir=" + process.env.TEMP + "\\wb-uitest-profile2",
  "--no-first-run", "about:blank",
], { stdio: "ignore" })

let version = null
for (let i = 0; i < 40; i++) {
  try {
    const r = await fetch(`http://127.0.0.1:${CDP_PORT}/json/version`)
    version = await r.json()
    break
  } catch { await sleep(500) }
}
if (!version) { console.error("CDP not ready"); proc.kill(); process.exit(2) }

const created = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/new?${encodeURIComponent(URL_TARGET)}`, { method: "PUT" })).json()
const ws = new WebSocket(created.webSocketDebuggerUrl)
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })

let msgId = 0
const pending = new Map()
const jsErrors = []
ws.onmessage = (ev) => {
  const m = JSON.parse(ev.data)
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) }
  if (m.method === "Runtime.exceptionThrown")
    jsErrors.push(m.params.exceptionDetails?.exception?.description || m.params.exceptionDetails?.text || "unknown")
}
const send = (method, params = {}) =>
  new Promise((res) => { const id = ++msgId; pending.set(id, res); ws.send(JSON.stringify({ id, method, params })) })
const evalJs = async (expr) => {
  const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true })
  if (r.result?.exceptionDetails) return { __err: r.result.exceptionDetails.exception?.description || r.result.exceptionDetails.text }
  return r.result?.result?.value
}

await send("Runtime.enable")
await send("Page.enable")
await sleep(2500)

const report = {}

// 1. 记录两个会话初始消息数
report.step1 = await evalJs(`(async () => {
  const items = [...document.querySelectorAll('.sitem')];
  if (items.length < 2) return 'NEED-2-SESSIONS';
  // 先切到会话1(任务归属地)
  items[1].click();
  await new Promise(r => setTimeout(r, 1000));
  const countA = document.querySelectorAll('#chat .msg').length;
  window.__t = (text) => fetch('/api/task', { method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ text, mode: 'plan' }) })
    .then(r => r.json()).then(j => { window.__taskResult = j; return j });
  return { countA };
})()`)

if (report.step1 === 'NEED-2-SESSIONS') { console.log(JSON.stringify(report)); ws.close(); proc.kill(); process.exit(1) }
const countA = report.step1.countA

// 2. 发任务(不等待) + 立即切到会话2
await evalJs(`(async () => {
  window.__t('只用一句话回答:1+1等于几?不要使用任何工具');
  await new Promise(r => setTimeout(r, 300)); // 让 typing 出现
  const items = [...document.querySelectorAll('.sitem')];
  items[0].click(); // 切到会话2(不是任务归属地)
  await new Promise(r => setTimeout(r, 800));
  return { countB: document.querySelectorAll('#chat .msg').length };
})()`)

// 3. 轮询等任务完成(最多 240 秒)
let done = false
for (let i = 0; i < 120; i++) {
  await sleep(2000)
  const st = await evalJs(`({ done: !!window.__taskResult })`)
  if (st.done !== undefined && st.done) { done = true; break }
}
report.taskCompleted = done
if (!done) { report.jsErrors = jsErrors.slice(0, 3); console.log(JSON.stringify(report, null, 2)); ws.close(); proc.kill(); process.exit(1) }

// 4. 断言: 仍在会话2视图 → 高亮在会话2、聊天区没被追加任务结果
report.step4_stillOnB = await evalJs(`(() => {
  const items = [...document.querySelectorAll('.sitem')];
  return JSON.stringify({
    curIndex: items.findIndex(d => d.classList.contains('cur')),
    countB_after: document.querySelectorAll('#chat .msg').length,
    lastMsgIsTypingOrGreeting: document.querySelector('#chat .msg:last-child')?.className || '',
  });
})()`)

// 5. 切回会话1 → 应看到任务结果(用户消息+回复,消息数比初始多)
report.step5_backToA = await evalJs(`(async () => {
  const items = [...document.querySelectorAll('.sitem')];
  items[1].click();
  await new Promise(r => setTimeout(r, 1000));
  const msgs = [...document.querySelectorAll('#chat .msg')];
  return JSON.stringify({
    countA_after: msgs.length,
    grewFrom: ${countA},
    lastTwo: msgs.slice(-2).map(m => m.textContent.slice(0, 50)),
  });
})()`)

report.jsErrors = jsErrors.slice(0, 3)
console.log(JSON.stringify(report, null, 2))
ws.close()
proc.kill()
process.exit(0)
