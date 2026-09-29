/**
 * livethink-test - 端到端验证:回车发送 + 思考过程实时显示 + 完成后折叠
 * 用法: bun test/livethink-test.mjs
 * 流程: 页面里往输入框打字 → 模拟回车键发送 → 任务进行中检查 details.think.live 出现且有内容
 *       → 任务完成后检查 live 块被移除、最终折叠思考块存在
 */
import { spawn } from "node:child_process"
import { existsSync } from "node:fs"
import { setTimeout as sleep } from "node:timers/promises"

const CDP_PORT = 9340
const browser = ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"].find((p) => existsSync(p))
const proc = spawn(browser, [
  "--remote-debugging-port=" + CDP_PORT, "--no-proxy-server", "--headless=new", "--disable-gpu",
  "--window-size=1280,860", "--user-data-dir=" + process.env.TEMP + "\\wb-livethink", "--no-first-run", "about:blank",
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
await send("Page.enable")
await sleep(2500)

const report = {}

// 1. 往输入框打字 + 模拟真实回车键(keyCode 13, 非 isComposing)发送
report.enterSend = await evalJs(`(async () => {
  const t = document.getElementById('t');
  t.focus();
  t.value = '只用一句话回答:天空为什么是蓝色的?不要使用任何工具';
  t.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', keyCode: 13, bubbles: true }));
  await new Promise(r => setTimeout(r, 800));
  const last = [...document.querySelectorAll('#chat .msg.user')].pop();
  return JSON.stringify({ sent: !!last && last.textContent.includes('天空'), textareaCleared: t.value === '' });
})()`)

// 2. 任务进行中:轮询等 live 思考块出现(最多 90s;模型不一定输出 reasoning,没有也算通过但要记录)
let liveSeen = false
let liveLen = 0
for (let i = 0; i < 45; i++) {
  await sleep(2000)
  const st = await evalJs(`(() => {
    const d = document.querySelector('details.think.live');
    return { open: d ? d.open : null, len: d ? d.querySelector('.tbody').textContent.length : 0 };
  })()`)
  if (st.len > 0) { liveSeen = true; liveLen = st.len; break }
}
report.liveThinking = { appeared: liveSeen, textLen: liveLen }

// 3. 等任务完成(输入框按钮恢复),验证 live 块移除 + 最终折叠思考块
let final = null
for (let i = 0; i < 120; i++) {
  await sleep(2000)
  const st = await evalJs(`(() => {
    const btn = document.getElementById('b');
    return { done: !btn.disabled, liveGone: !document.querySelector('details.think.live'),
      finalThink: !!document.querySelector('#chat details.think:not(.live)'),
      lastMsg: [...document.querySelectorAll('#chat .msg')].pop()?.textContent.slice(0, 60) };
  })()`)
  if (st.done) { final = st; break }
}
report.final = final
report.jsErrors = jsErrors.slice(0, 3)
console.log(JSON.stringify(report, null, 2))
ws.close()
proc.kill()
process.exit(0)
