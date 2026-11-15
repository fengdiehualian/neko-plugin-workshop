/**
 * archivetest2 - 单点重验:归档后默认视图消失(2.5s 等待 + DOM/API 双重确认)
 */
import { spawn } from "node:child_process"
import { existsSync } from "node:fs"
import { setTimeout as sleep } from "node:timers/promises"

const CDP_PORT = 9343
const browser = ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"].find((p) => existsSync(p))
const proc = spawn(browser, [
  "--remote-debugging-port=" + CDP_PORT, "--no-proxy-server", "--headless=new", "--disable-gpu",
  "--window-size=1280,860", "--user-data-dir=" + process.env.TEMP + "\\wb-arch2", "--no-first-run", "about:blank",
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

// 新建会话 → 改名「归档消失测试」→ 点归档 → 2.5s 后双确认
const r = await evalJs(`(async () => {
  document.getElementById('newbtn').click();
  await new Promise(r => setTimeout(r, 900));
  window.prompt = () => '归档消失测试';
  document.querySelector('#slist .sitem .ren').click();
  await new Promise(r => setTimeout(r, 900));
  const it = [...document.querySelectorAll('#slist .sitem')].find(x => x.querySelector('.t').textContent === '归档消失测试');
  if (!it) return 'NOT-FOUND-AFTER-RENAME';
  it.querySelector('.arc').click();
  await new Promise(r => setTimeout(r, 2500));
  const inDom = [...document.querySelectorAll('#slist .sitem .t')].some(x => x.textContent === '归档消失测试');
  const j = await (await fetch('/api/sessions')).json();
  const srv = j.items.find(x => x.title === '归档消失测试');
  return JSON.stringify({ inDefaultDom: inDom, serverArchived: srv ? !!srv.archived : 'missing' });
})()`)
console.log(r)
ws.close()
proc.kill()
process.exit(0)
