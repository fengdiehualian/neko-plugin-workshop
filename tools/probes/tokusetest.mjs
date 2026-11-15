/**
 * tokusetest - 验证气泡用量行渲染 + 侧边栏累计 tooltip
 */
import { spawn } from "node:child_process"
import { existsSync } from "node:fs"
import { setTimeout as sleep } from "node:timers/promises"

const CDP_PORT = 9344
const browser = ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"].find((p) => existsSync(p))
const proc = spawn(browser, [
  "--remote-debugging-port=" + CDP_PORT, "--no-proxy-server", "--headless=new", "--disable-gpu",
  "--window-size=1280,860", "--user-data-dir=" + process.env.TEMP + "\\wb-tokuse", "--no-first-run", "about:blank",
], { stdio: "ignore" })

let version = null
for (let i = 0; i < 40; i++) {
  try { version = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/version`)).json(); break } catch { await sleep(500) }
}
const created = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/new?${encodeURIComponent("http://127.0.0.1:5099")}`, { method: "PUT" })).json()
const ws = new WebSocket(created.webSocketDebuggerUrl)
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
let msgId = 0
const pending = new Map()
ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) } }
const send = (method, params = {}) => new Promise((res) => { const id = ++msgId; pending.set(id, res); ws.send(JSON.stringify({ id, method, params })) })
const evalJs = async (expr) => (await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true })).result?.result?.value

await send("Page.enable")
await sleep(2500)

const r = await evalJs(`(() => {
  const us = [...document.querySelectorAll('#chat .tusage')].map(x => x.textContent);
  const tip = document.querySelector('#slist .sitem')?.title || '';
  return JSON.stringify({ usageLines: us, sidebarTip: tip });
})()`)
console.log(r)
ws.close()
proc.kill()
process.exit(0)
