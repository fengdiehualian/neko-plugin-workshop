/**
 * wb-uitest-cdp - 用 Edge/Chrome headless + CDP 驱动工坊网页,复现/验证会话切换
 * 用法: bun test/uitest-cdp.mjs <页面URL>
 * 流程: 打开页面 → 等渲染 → 读侧边栏 → 点第二个会话 → 读聊天区与高亮 → 汇报 JS 错误
 */
import { spawn } from "node:child_process"
import { existsSync } from "node:fs"
import { setTimeout as sleep } from "node:timers/promises"

const URL_TARGET = process.argv[2] || "http://127.0.0.1:5099"
const CDP_PORT = 9333

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
  "--user-data-dir=" + (process.env.WB_PROFILE || process.env.TEMP + "\\wb-uitest-profile"),
  "--no-first-run", "about:blank",
], { stdio: "ignore" })

// 等 CDP 就绪
let version = null
for (let i = 0; i < 40; i++) {
  try {
    const r = await fetch(`http://127.0.0.1:${CDP_PORT}/json/version`)
    version = await r.json()
    break
  } catch { await sleep(500) }
}
if (!version) { console.error("CDP not ready"); proc.kill(); process.exit(2) }

// 建新 tab
const created = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/new?${encodeURIComponent(URL_TARGET)}`, { method: "PUT" })).json()
const ws = new WebSocket(created.webSocketDebuggerUrl)
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })

let msgId = 0
const pending = new Map()
const consoleLogs = []
const jsErrors = []
ws.onmessage = (ev) => {
  const m = JSON.parse(ev.data)
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) }
  if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error")
    consoleLogs.push(m.params.args.map((a) => a.value ?? a.description ?? "").join(" "))
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
await sleep(2500) // 等页面渲染 + loadCurrent 完成

const report = {}
report.step1_list = await evalJs(`JSON.stringify({
  sessions: [...document.querySelectorAll('.sitem')].map(d => ({
    title: d.querySelector('.t')?.textContent,
    cur: d.classList.contains('cur'),
  })),
  chatCount: document.querySelectorAll('#chat .msg').length,
})`)

// 点第二个会话(若存在)
report.clickResult = await evalJs(`(async () => {
  const items = [...document.querySelectorAll('.sitem')];
  if (items.length < 2) return 'ONLY-ONE-SESSION';
  const target = items[1];
  target.click();
  await new Promise(r => setTimeout(r, 1200));
  return JSON.stringify({
    afterClick_sessions: [...document.querySelectorAll('.sitem')].map(d => ({
      title: d.querySelector('.t')?.textContent, cur: d.classList.contains('cur'),
    })),
    chatTitles: [...document.querySelectorAll('#chat .msg')].slice(0, 3).map(m => m.textContent.slice(0, 40)),
    chatCount: document.querySelectorAll('#chat .msg').length,
  });
})()`)

report.consoleErrors = consoleLogs.slice(0, 5)
report.jsErrors = jsErrors.slice(0, 5)
console.log(JSON.stringify(report, null, 2))

ws.close()
proc.kill()
process.exit(0)
