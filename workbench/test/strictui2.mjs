/**
 * strictui2 - 用 Runtime.consoleAPICalled 拿页面 console 输出来验证开关
 */
import { spawn } from "node:child_process"
import { existsSync } from "node:fs"
import { setTimeout as sleep } from "node:timers/promises"

const browser = ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"].find((p) => existsSync(p))
const proc = spawn(browser, [
  "--remote-debugging-port=9352", "--no-proxy-server", "--headless=new", "--disable-gpu",
  "--window-size=1280,860", "--user-data-dir=" + process.env.TEMP + "\\wb-stricui2", "--no-first-run", "about:blank",
], { stdio: "ignore" })
let v = null
for (let i = 0; i < 40; i++) { try { v = await (await fetch("http://127.0.0.1:9352/json/version")).json(); break } catch { await sleep(500) } }
const created = await (await fetch("http://127.0.0.1:9352/json/new?" + encodeURIComponent("http://127.0.0.1:5099"), { method: "PUT" })).json()
const ws = new WebSocket(created.webSocketDebuggerUrl)
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
let id = 0
const pend = new Map()
const logs = []
ws.onmessage = (ev) => {
  const m = JSON.parse(ev.data)
  if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id) }
  if (m.method === "Runtime.consoleAPICalled" && m.params.args?.length) {
    logs.push(m.params.args.map((a) => a.value ?? a.description ?? "").join(" "))
  }
}
const send = (method, params = {}) => new Promise((res) => { const i = ++id; pend.set(i, res); ws.send(JSON.stringify({ id: i, method, params })) })
const evalJs = async (expr) => (await send("Runtime.evaluate", { expression: expr, awaitPromise: true })).result

await send("Runtime.enable")
await send("Page.enable")
await sleep(2500)

// 用 console.log 报告结果(绕开 returnByValue 序列化问题)
await evalJs(`(async () => {
  document.getElementById('setbtn').click();
  await new Promise(r => setTimeout(r, 500));
  document.querySelector('.sptab[data-tab="look"]').click();
  await new Promise(r => setTimeout(r, 400));
  const t = document.getElementById('strictToggle');
  console.log('STEP1 toggle exists=' + !!t + ' checked=' + (t ? t.checked : 'n/a'));
  if (t) {
    t.checked = false
    t.dispatchEvent(new Event('change'))
    await new Promise(r => setTimeout(r, 900))
    const j = await (await fetch('/api/strict')).json()
    console.log('STEP2 backend=' + j.strictVerify + ' ui=' + t.checked)
    t.checked = true
    t.dispatchEvent(new Event('change'))
    await new Promise(r => setTimeout(r, 900))
    const j2 = await (await fetch('/api/strict')).json()
    console.log('STEP3 backend=' + j2.strictVerify)
  }
})()`)
await sleep(1500)
console.log(logs.filter((l) => l.startsWith("STEP")).join("\n"))
ws.close()
proc.kill()
process.exit(0)
