/**
 * dbgpage - 调试页面实际渲染状态
 */
import { spawn } from "node:child_process"
import { existsSync } from "node:fs"
import { setTimeout as sleep } from "node:timers/promises"

const browser = ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"].find((p) => existsSync(p))
const proc = spawn(browser, [
  "--remote-debugging-port=9345", "--no-proxy-server", "--headless=new", "--disable-gpu",
  "--window-size=1280,860", "--user-data-dir=" + process.env.TEMP + "\\wb-dbg", "--no-first-run", "about:blank",
], { stdio: "ignore" })
let v = null
for (let i = 0; i < 40; i++) { try { v = await (await fetch("http://127.0.0.1:9345/json/version")).json(); break } catch { await sleep(500) } }
const created = await (await fetch("http://127.0.0.1:9345/json/new?" + encodeURIComponent("http://127.0.0.1:5099"), { method: "PUT" })).json()
const ws = new WebSocket(created.webSocketDebuggerUrl)
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
let id = 0
const pend = new Map()
ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id) } }
const send = (method, params = {}) => new Promise((res) => { const i = ++id; pend.set(i, res); ws.send(JSON.stringify({ id: i, method, params })) })
const ev = async (expr) => (await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true })).result?.result?.value
await send("Page.enable")
await sleep(3000)
const r = await ev(`(() => JSON.stringify({
  title: document.title,
  hasSetup: !!document.getElementById('u'),
  chatMsgs: document.querySelectorAll('#chat .msg').length,
  sitemCount: document.querySelectorAll('#slist .sitem').length,
  sitemTip: document.querySelector('#slist .sitem')?.getAttribute('title') || null,
  lastBubbleHead: [...document.querySelectorAll('#chat .msg.bot')].pop()?.outerHTML.slice(0, 200),
  fmtTokType: typeof fmtTok,
}))()`)
console.log(r)
ws.close()
proc.kill()
process.exit(0)
