/**
 * bubbleshot - 验证成功气泡底色随主题变化 + 截图
 */
import { spawn } from "node:child_process"
import { existsSync, writeFileSync } from "node:fs"
import { setTimeout as sleep } from "node:timers/promises"

const CDP_PORT = 9339
const browser = ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"].find((p) => existsSync(p))
const proc = spawn(browser, [
  "--remote-debugging-port=" + CDP_PORT, "--no-proxy-server", "--headless=new", "--disable-gpu",
  "--window-size=1280,860", "--user-data-dir=" + process.env.TEMP + "\\wb-bubble", "--no-first-run", "about:blank",
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

const out = {}
for (const t of ["pink", "blue", "white", "black"]) {
  const r = await evalJs(`(async () => {
    document.getElementById('setbtn').click();
    document.querySelector('.sptab[data-tab="look"]').click();
    document.querySelector('.themeopt[data-t="${t}"]').click();
    await new Promise(r => setTimeout(r, 300));
    const ok = document.querySelector('#chat .msg.bot.ok');
    return getComputedStyle(ok).backgroundColor + ' / ' + getComputedStyle(ok).color;
  })()`)
  out[t] = r
  if (t === "black") {
    const s = await send("Page.captureScreenshot", { format: "png" })
    writeFileSync("C:/Users/Administrator/AppData/Local/Temp/wb_bubble_black.png", Buffer.from(s.result.data, "base64"))
  }
  if (t === "white") {
    const s = await send("Page.captureScreenshot", { format: "png" })
    writeFileSync("C:/Users/Administrator/AppData/Local/Temp/wb_bubble_white.png", Buffer.from(s.result.data, "base64"))
  }
}
console.log(JSON.stringify(out, null, 1))
ws.close()
proc.kill()
process.exit(0)
