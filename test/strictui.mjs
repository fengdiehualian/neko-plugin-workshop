/**
 * strictui - 验证设置面板外观 tab 的严格校验开关渲染与交互
 */
import { spawn } from "node:child_process"
import { existsSync } from "node:fs"
import { setTimeout as sleep } from "node:timers/promises"

const browser = ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"].find((p) => existsSync(p))
const proc = spawn(browser, [
  "--remote-debugging-port=9351", "--no-proxy-server", "--headless=new", "--disable-gpu",
  "--window-size=1280,860", "--user-data-dir=" + process.env.TEMP + "\\wb-stricui", "--no-first-run", "about:blank",
], { stdio: "ignore" })
let v = null
for (let i = 0; i < 40; i++) { try { v = await (await fetch("http://127.0.0.1:9351/json/version")).json(); break } catch { await sleep(500) } }
const created = await (await fetch("http://127.0.0.1:9351/json/new?" + encodeURIComponent("http://127.0.0.1:5099"), { method: "PUT" })).json()
const ws = new WebSocket(created.webSocketDebuggerUrl)
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
let id = 0
const pend = new Map()
const errs = []
ws.onmessage = (ev) => {
  const m = JSON.parse(ev.data)
  if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id) }
  if (m.method === "Runtime.exceptionThrown") errs.push(m.params.exceptionDetails?.text || "err")
}
const send = (method, params = {}) => new Promise((res) => { const i = ++id; pend.set(i, res); ws.send(JSON.stringify({ id: i, method, params })) })
const evalJs = async (expr) => (await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true })).result?.result?.value

await send("Page.enable")
await sleep(2500)

// 打开设置 → 外观 tab → 检查开关存在且状态与后端一致(应为 true,上次 POST 过)
const r = await evalJs(`(async () => {
  document.getElementById('setbtn').click();
  await new Promise(r => setTimeout(r, 500));
  document.querySelector('.sptab[data-tab="look"]').click();
  await new Promise(r => setTimeout(r, 300));
  const t = document.getElementById('strictToggle');
  return JSON.stringify({ exists: !!t, checked: t ? t.checked : null });
})()`)
console.log("toggle:", r)

// 切到 false 再读后端
const r2 = await evalJs(`(async () => {
  const t = document.getElementById('strictToggle');
  t.checked = false;
  t.dispatchEvent(new Event('change'));
  await new Promise(r => setTimeout(r, 800));
  const j = await (await fetch('/api/strict')).json();
  return JSON.stringify({ backendNow: j.strictVerify, uiNow: document.getElementById('strictToggle').checked });
})()`)
console.log("after toggle off:", r2)

// 切回 true
const r3 = await evalJs(`(async () => {
  const t = document.getElementById('strictToggle');
  t.checked = true;
  t.dispatchEvent(new Event('change'));
  await new Promise(r => setTimeout(r, 800));
  const j = await (await fetch('/api/strict')).json();
  return JSON.stringify({ backendNow: j.strictVerify });
})()`)
console.log("after toggle on:", r3)
console.log("jsErrors:", errs.slice(0, 3))
ws.close()
proc.kill()
process.exit(0)
