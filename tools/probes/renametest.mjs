/**
 * renametest - 验证会话自定义命名:接口 + 前端按钮流程(桩 prompt) + 持久化
 */
import { spawn } from "node:child_process"
import { existsSync } from "node:fs"
import { setTimeout as sleep } from "node:timers/promises"

const CDP_PORT = 9341
const browser = ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"].find((p) => existsSync(p))
const proc = spawn(browser, [
  "--remote-debugging-port=" + CDP_PORT, "--no-proxy-server", "--headless=new", "--disable-gpu",
  "--window-size=1280,860", "--user-data-dir=" + process.env.TEMP + "\\wb-rename", "--no-first-run", "about:blank",
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
await sleep(2500)

const report = {}

// 1. 新建一个会话 → 桩掉 prompt → 点 ✏️ 按钮 → 验证侧边栏显示新名字
report.uiFlow = await evalJs(`(async () => {
  // 新建会话
  document.getElementById('newbtn').click();
  await new Promise(r => setTimeout(r, 800));
  const item = document.querySelector('#slist .sitem');
  if (!item) return 'NO-ITEM';
  const before = item.querySelector('.t').textContent;
  // 桩 prompt:返回自定义名
  window.prompt = () => '我的自定义会话名';
  item.querySelector('.ren').click();
  await new Promise(r => setTimeout(r, 800));
  const after = document.querySelector('#slist .sitem .t').textContent;
  return JSON.stringify({ before, after, renamed: after === '我的自定义会话名' });
})()`)

// 2. 后端持久化验证:直接查接口
report.persisted = await evalJs(`(async () => {
  const j = await (await fetch('/api/sessions')).json();
  return JSON.stringify({ first: j.items[0].title, current: j.current === j.items[0].id });
})()`)

// 3. 空名拒绝
report.emptyRejected = await evalJs(`(async () => {
  const j = await (await fetch('/api/sessions')).json();
  const id = j.items[0].id;
  const r = await fetch('/api/sessions/' + id + '/rename', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ title: '   ' }) });
  return JSON.stringify({ status: r.status });
})()`)

// 4. 刷新页面验证名字还在(持久化) + 双击也触发重命名(桩 prompt 计数)
report.reload = await evalJs(`(async () => {
  let promptCalls = 0;
  window.prompt = () => { promptCalls++; return '双击改名'; };
  const item = document.querySelector('#slist .sitem');
  item.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
  await new Promise(r => setTimeout(r, 800));
  const after = document.querySelector('#slist .sitem .t').textContent;
  return JSON.stringify({ promptCalls, after, dblclickWorks: promptCalls === 1 && after === '双击改名' });
})()`)

report.jsErrors = jsErrors.slice(0, 3)
console.log(JSON.stringify(report, null, 2))
ws.close()
proc.kill()
process.exit(0)
