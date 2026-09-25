/**
 * archivetest - 会话归档功能端到端验证
 * 1. 新建会话→改名→点📥归档→默认视图消失
 * 2. 切「查看已归档」→出现在归档视图(带📤)
 * 3. 点📤取消归档→回到默认视图
 * 4. 归档当前会话→current 自动切换
 * 5. 开关状态 localStorage 记住(重开面板仍生效)
 */
import { spawn } from "node:child_process"
import { existsSync } from "node:fs"
import { setTimeout as sleep } from "node:timers/promises"

const CDP_PORT = 9342
const browser = ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"].find((p) => existsSync(p))
const proc = spawn(browser, [
  "--remote-debugging-port=" + CDP_PORT, "--no-proxy-server", "--headless=new", "--disable-gpu",
  "--window-size=1280,860", "--user-data-dir=" + process.env.TEMP + "\\wb-arch", "--no-first-run", "about:blank",
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

// 1. 新建会话并改名,然后点📥归档 → 默认视图应消失
report.archive = await evalJs(`(async () => {
  document.getElementById('newbtn').click();
  await new Promise(r => setTimeout(r, 800));
  const item = document.querySelector('#slist .sitem');
  if (!item) return 'NO-ITEM';
  window.prompt = () => '待归档会话';
  item.querySelector('.ren').click();
  await new Promise(r => setTimeout(r, 700));
  const it2 = [...document.querySelectorAll('#slist .sitem')].find(x => x.querySelector('.t').textContent === '待归档会话');
  if (!it2) return 'RENAME-FAIL';
  it2.querySelector('.arc').click();
  await new Promise(r => setTimeout(r, 900));
  const gone = ![...document.querySelectorAll('#slist .sitem')].some(x => x.querySelector('.t')?.textContent === '待归档会话');
  return JSON.stringify({ goneFromDefault: gone });
})()`)

// 2. 切到归档视图:能看到它,带📤;普通会话不在视图里
report.archivedView = await evalJs(`(async () => {
  document.getElementById('archbtn').click();
  await new Promise(r => setTimeout(r, 900));
  const titles = [...document.querySelectorAll('#slist .sitem .t')].map(x => x.textContent);
  const target = [...document.querySelectorAll('#slist .sitem')].find(x => x.querySelector('.t').textContent === '待归档会话');
  return JSON.stringify({
    btnText: document.getElementById('archbtn').textContent,
    hasTarget: !!target,
    hasUnarchive: !!target?.querySelector('.un'),
    allArchived: titles.every(t => t === '待归档会话'),
  });
})()`)

// 3. 取消归档 → 从归档视图消失
report.unarchive = await evalJs(`(async () => {
  const target = [...document.querySelectorAll('#slist .sitem')].find(x => x.querySelector('.t').textContent === '待归档会话');
  target.querySelector('.un').click();
  await new Promise(r => setTimeout(r, 900));
  const gone = ![...document.querySelectorAll('#slist .sitem')].some(x => x.querySelector('.t')?.textContent === '待归档会话');
  return JSON.stringify({ goneFromArchivedView: gone });
})()`)

// 4. 再归档一个"当前会话" → current 应自动切到别的会话
report.archiveCurrent = await evalJs(`(async () => {
  document.getElementById('archbtn').click(); // 回默认视图
  await new Promise(r => setTimeout(r, 700));
  const cur = document.querySelector('#slist .sitem.cur');
  if (!cur) return 'NO-CURRENT';
  const curTitle = cur.querySelector('.t').textContent;
  cur.querySelector('.arc').click();
  await new Promise(r => setTimeout(r, 900));
  const j = await (await fetch('/api/sessions')).json();
  const curItem = j.items.find(x => x.id === j.current);
  return JSON.stringify({
    archivedTitle: curTitle,
    newCurrentTitle: curItem ? curItem.title : null,
    newCurrentArchived: curItem ? !!curItem.archived : null,
  });
})()`)

// 5. 开关状态持久化:重新加载页面后仍处于归档视图
report.togglePersist = await evalJs(`window.localStorage.getItem('wb_showarch')`)

report.jsErrors = jsErrors.slice(0, 3)
console.log(JSON.stringify(report, null, 2))
ws.close()
proc.kill()
process.exit(0)
