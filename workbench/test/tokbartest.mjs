/**
 * tokbartest - 验证底部 token 状态栏:显示当前会话累计 + 随会话切换变化 + 记忆文案移除
 */
import { spawn } from "node:child_process"
import { existsSync } from "node:fs"
import { setTimeout as sleep } from "node:timers/promises"

const CDP_PORT = 9347
const browser = ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"].find((p) => existsSync(p))
const proc = spawn(browser, [
  "--remote-debugging-port=" + CDP_PORT, "--no-proxy-server", "--headless=new", "--disable-gpu",
  "--window-size=1280,860", "--user-data-dir=" + process.env.TEMP + "\\wb-tokbar", "--no-first-run", "about:blank",
], { stdio: "ignore" })
let v = null
for (let i = 0; i < 40; i++) { try { v = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/version`)).json(); break } catch { await sleep(500) } }
const created = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/new?${encodeURIComponent("http://127.0.0.1:5099")}`, { method: "PUT" })).json()
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
const send = (method, params = {}) => new Promise((res) => { const id = ++msgId; pending.set(id, res); ws.send(JSON.stringify({ id, method, params })) })
const evalJs = async (expr) => {
  const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true })
  if (r.result?.exceptionDetails) return { __err: r.result.exceptionDetails.exception?.description || r.result.exceptionDetails.text }
  return r.result?.result?.value
}

await send("Runtime.enable")
await sleep(2500)

const report = {}
// 1. 状态栏存在且显示当前会话的 token
report.bar1 = await evalJs(`(() => JSON.stringify({
  bar: document.getElementById('tokbar')?.textContent,
  hasMemoryText: document.querySelector('.hint').textContent.includes('记忆'),
}))()`)

// 2. 切到另一个会话 → 状态栏跟着变(两个会话 tokens 不同)
report.switch = await evalJs(`(async () => {
  const before = document.getElementById('tokbar').textContent;
  const items = [...document.querySelectorAll('#slist .sitem')];
  if (items.length < 2) return JSON.stringify({ before, skipped: 'only-one-session' });
  items[items.length - 1].click();
  await new Promise(r => setTimeout(r, 900));
  const after = document.getElementById('tokbar').textContent;
  return JSON.stringify({ before, after, changed: before !== after });
})()`)

// 3. 切回来恢复
report.switchBack = await evalJs(`(async () => {
  const items = [...document.querySelectorAll('#slist .sitem')];
  items[0].click();
  await new Promise(r => setTimeout(r, 900));
  return document.getElementById('tokbar').textContent;
})()`)

report.jsErrors = jsErrors.slice(0, 3)
console.log(JSON.stringify(report, null, 2))
ws.close()
proc.kill()
process.exit(0)
