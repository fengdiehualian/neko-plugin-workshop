/**
 * thinkshot - 验证思考过程折叠块渲染:切黑主题 → 展开思考 → 截图
 */
import { spawn } from "node:child_process"
import { existsSync, writeFileSync } from "node:fs"
import { setTimeout as sleep } from "node:timers/promises"

const URL_TARGET = "http://127.0.0.1:5099"
const CDP_PORT = 9338
const browser = ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"].find((p) => existsSync(p))
const proc = spawn(browser, [
  "--remote-debugging-port=" + CDP_PORT, "--no-proxy-server", "--headless=new", "--disable-gpu",
  "--window-size=1280,860", "--user-data-dir=" + process.env.TEMP + "\\wb-think", "--no-first-run", "about:blank",
], { stdio: "ignore" })

let version = null
for (let i = 0; i < 40; i++) {
  try { version = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/version`)).json(); break } catch { await sleep(500) }
}
const created = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/new?${encodeURIComponent(URL_TARGET)}`, { method: "PUT" })).json()
const ws = new WebSocket(created.webSocketDebuggerUrl)
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
let msgId = 0
const pending = new Map()
ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) } }
const send = (method, params = {}) => new Promise((res) => { const id = ++msgId; pending.set(id, res); ws.send(JSON.stringify({ id, method, params })) })
const evalJs = async (expr) => (await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true })).result?.result?.value

await send("Page.enable")
await sleep(2500)

const check = await evalJs(`(() => {
  const th = document.querySelectorAll('details.think');
  return JSON.stringify({ thinkBlocks: th.length, firstSummary: th[0]?.querySelector('summary')?.textContent });
})()`)
console.log("check:", check)

await evalJs(`(async () => {
  document.querySelector('.themeopt[data-t="black"]').click();
  await new Promise(r => setTimeout(r, 300));
  const d = document.querySelector('details.think');
  if (d) d.open = true;
  const chat = document.getElementById('chat');
  chat.scrollTop = chat.scrollHeight;
})()`)
await sleep(500)
const r = await send("Page.captureScreenshot", { format: "png" })
writeFileSync("C:/Users/Administrator/AppData/Local/Temp/wb_think_black.png", Buffer.from(r.result.data, "base64"))
console.log("saved wb_think_black.png")
ws.close()
proc.kill()
process.exit(0)
