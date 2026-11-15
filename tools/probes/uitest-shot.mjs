/**
 * wb-uitest-shot - 打开工坊设置面板,两种主题各截一张图
 * 用法: bun test/uitest-shot.mjs <页面URL>
 */
import { spawn } from "node:child_process"
import { existsSync, writeFileSync } from "node:fs"
import { setTimeout as sleep } from "node:timers/promises"

const URL_TARGET = process.argv[2] || "http://127.0.0.1:5099"
const OUT = "C:/Users/Administrator/AppData/Local/Temp"
const CDP_PORT = 9336

const browser = ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"].find((p) => existsSync(p))
if (!browser) { console.error("no chrome"); process.exit(2) }

const proc = spawn(browser, [
  `--remote-debugging-port=${CDP_PORT}`, "--no-proxy-server", "--headless=new", "--disable-gpu",
  `--window-size=1280,860`, "--user-data-dir=" + process.env.TEMP + "\\wb-uitest-shot", "--no-first-run", "about:blank",
], { stdio: "ignore" })

let version = null
for (let i = 0; i < 40; i++) {
  try { version = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/version`)).json(); break } catch { await sleep(500) }
}
if (!version) { console.error("CDP not ready"); proc.kill(); process.exit(2) }

const created = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/new?${encodeURIComponent(URL_TARGET)}`, { method: "PUT" })).json()
const ws = new WebSocket(created.webSocketDebuggerUrl)
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })

let msgId = 0
const pending = new Map()
ws.onmessage = (ev) => {
  const m = JSON.parse(ev.data)
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) }
}
const send = (method, params = {}) =>
  new Promise((res) => { const id = ++msgId; pending.set(id, res); ws.send(JSON.stringify({ id, method, params })) })
const evalJs = async (expr) => {
  const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true })
  return r.result?.result?.value
}
const shot = async (name) => {
  const r = await send("Page.captureScreenshot", { format: "png" })
  writeFileSync(`${OUT}/${name}`, Buffer.from(r.result.data, "base64"))
  console.log(`saved ${name}`)
}

await send("Page.enable")
await sleep(2500)

// 打开设置面板 → 截粉主题 → 切蓝 → 截蓝主题
await evalJs(`(async () => {
  document.getElementById('setbtn').click();
  await new Promise(r => setTimeout(r, 500));
  document.querySelector('.sptab[data-tab="look"]').click();
  await new Promise(r => setTimeout(r, 300));
})()`)
await sleep(300)
await shot("wb_theme_pink.png")
await evalJs(`(async () => { document.querySelector('.themeopt[data-t="blue"]').click(); await new Promise(r => setTimeout(r, 400)) })()`)
await shot("wb_theme_blue.png")

ws.close()
proc.kill()
process.exit(0)
