/**
 * errprobe - 抓取页面加载时的运行时异常与关键符号状态
 */
import { spawn } from "node:child_process"
import { existsSync } from "node:fs"
import { setTimeout as sleep } from "node:timers/promises"

const browser = ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"].find((p) => existsSync(p))
const proc = spawn(browser, [
  "--remote-debugging-port=9346", "--no-proxy-server", "--headless=new", "--disable-gpu",
  "--window-size=1280,860", "--user-data-dir=" + process.env.TEMP + "\\wb-err", "--no-first-run", "about:blank",
], { stdio: "ignore" })
let v = null
for (let i = 0; i < 40; i++) { try { v = await (await fetch("http://127.0.0.1:9346/json/version")).json(); break } catch { await sleep(500) } }
const created = await (await fetch("http://127.0.0.1:9346/json/new?" + encodeURIComponent("http://127.0.0.1:5099"), { method: "PUT" })).json()
const ws = new WebSocket(created.webSocketDebuggerUrl)
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
let id = 0
const pend = new Map()
const errs = []
ws.onmessage = (ev) => {
  const m = JSON.parse(ev.data)
  if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id) }
  if (m.method === "Runtime.exceptionThrown") {
    const d = m.params.exceptionDetails
    errs.push((d.exception?.description || d.text || "unknown").slice(0, 400) + " @line " + (d.lineNumber ?? "?") + ":" + (d.columnNumber ?? "?"))
  }
  if (m.method === "Log.entryAdded") errs.push("[log] " + (m.params.entry.text || "").slice(0, 200))
}
const send = (method, params = {}) => new Promise((res) => { const i = ++id; pend.set(i, res); ws.send(JSON.stringify({ id: i, method, params })) })
await send("Runtime.enable")
await send("Log.enable")
await send("Page.enable")
await sleep(4000)
const probe = await (async () => {
  const r = await send("Runtime.evaluate", { expression: `JSON.stringify({fmtTok: typeof fmtTok, loadCurrent: typeof loadCurrent, esc: typeof esc, slistKids: document.getElementById('slist')?.children.length})`, returnByValue: true })
  return r.result?.result?.value
})()
console.log("errors:", JSON.stringify(errs.slice(0, 6), null, 1))
console.log("symbols:", probe)
ws.close()
proc.kill()
process.exit(0)
