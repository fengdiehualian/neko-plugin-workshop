/**
 * wb-uitest-themes4 - 验证四主题切换 + 顶栏滚动可见性 + 思考过程展示
 * 用法: bun test/uitest-themes4.mjs <页面URL>
 */
import { spawn } from "node:child_process"
import { existsSync, writeFileSync } from "node:fs"
import { setTimeout as sleep } from "node:timers/promises"

const URL_TARGET = process.argv[2] || "http://127.0.0.1:5099"
const OUT = "C:/Users/Administrator/AppData/Local/Temp"
const CDP_PORT = 9337

const browser = ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"].find((p) => existsSync(p))
const proc = spawn(browser, [
  `--remote-debugging-port=${CDP_PORT}`, "--no-proxy-server", "--headless=new", "--disable-gpu",
  "--window-size=1280,860", "--user-data-dir=" + process.env.TEMP + "\\wb-uitest-t4", "--no-first-run", "about:blank",
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
const jsErrors = []
ws.onmessage = (ev) => {
  const m = JSON.parse(ev.data)
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) }
  if (m.method === "Runtime.exceptionThrown")
    jsErrors.push(m.params.exceptionDetails?.exception?.description || m.params.exceptionDetails?.text || "unknown")
}
const send = (method, params = {}) =>
  new Promise((res) => { const id = ++msgId; pending.set(id, res); ws.send(JSON.stringify({ id, method, params })) })
const evalJs = async (expr) => {
  const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true })
  if (r.result?.exceptionDetails) return { __err: r.result.exceptionDetails.exception?.description || r.result.exceptionDetails.text }
  return r.result?.result?.value
}
const shot = async (name) => {
  const r = await send("Page.captureScreenshot", { format: "png" })
  writeFileSync(`${OUT}/${name}`, Buffer.from(r.result.data, "base64"))
}

await send("Page.enable")
await sleep(2500)

const report = {}

// 1. 布局:页面级滚动应消失(chat 内部滚动,顶栏 sticky)
report.layout = await evalJs(`(() => {
  const chat = document.getElementById('chat');
  // 往聊天里塞 60 条消息制造内部滚动
  for (let i = 0; i < 60; i++) { const d = document.createElement('div'); d.className = 'msg bot'; d.textContent = '填充消息' + i; chat.appendChild(d); }
  window.scrollTo(0, 500);
  return JSON.stringify({
    bodyScrollable: document.body.scrollHeight > window.innerHeight + 5,
    chatScrollHeight: chat.scrollHeight > chat.clientHeight,
    headerSticky: getComputedStyle(document.querySelector('header')).position,
  });
})()`)
await sleep(300)
report.headerVisibleAfterScroll = await evalJs(`(() => {
  const r = document.querySelector('header').getBoundingClientRect();
  return JSON.stringify({ top: r.top, visible: r.top >= 0 && r.bottom > 0 });
})()`)

// 2. 四主题逐个切换验证 + 黑白截图
report.themes = await evalJs(`(async () => {
  document.getElementById('setbtn').click();
  await new Promise(r => setTimeout(r, 400));
  document.querySelector('.sptab[data-tab="look"]').click();
  await new Promise(r => setTimeout(r, 300));
  const out = {};
  for (const t of ['pink', 'blue', 'white', 'black']) {
    document.querySelector('.themeopt[data-t="' + t + '"]').click();
    await new Promise(r => setTimeout(r, 250));
    out[t] = {
      cls: t === 'pink' ? !document.body.classList.contains('theme-blue') && !document.body.classList.contains('theme-white') && !document.body.classList.contains('theme-black') : document.body.classList.contains('theme-' + t),
      stored: localStorage.getItem('wb_theme'),
      headerBg: getComputedStyle(document.querySelector('header')).backgroundColor,
      bodyBg: getComputedStyle(document.body).backgroundColor,
      cur: document.querySelector('.themeopt[data-t="' + t + '"]').classList.contains('cur'),
    };
  }
  return JSON.stringify(out);
})()`)

// 黑色主题截图(含打开的思考折叠样式? 此刻聊天里没有思考块,仅看整体配色)
await shot("wb_theme_black.png")
await evalJs(`document.querySelector('.themeopt[data-t="white"]').click()`)
await sleep(300)
await shot("wb_theme_white.png")

report.jsErrors = jsErrors.slice(0, 3)
console.log(JSON.stringify(report, null, 2))
ws.close()
proc.kill()
process.exit(0)
