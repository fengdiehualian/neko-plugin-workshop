/**
 * wb-uitest-theme - 验证设置面板分类(API/外观)与蓝色主题切换
 * 用法: bun test/uitest-theme.mjs <页面URL>
 */
import { spawn } from "node:child_process"
import { existsSync } from "node:fs"
import { setTimeout as sleep } from "node:timers/promises"

const URL_TARGET = process.argv[2] || "http://127.0.0.1:5099"
const CDP_PORT = 9335

const browsers = [
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
]
const browser = browsers.find((p) => existsSync(p))
if (!browser) { console.error("no chrome found"); process.exit(2) }

const proc = spawn(browser, [
  `--remote-debugging-port=${CDP_PORT}`,
  "--no-proxy-server", "--headless=new", "--disable-gpu",
  "--user-data-dir=" + process.env.TEMP + "\\wb-uitest-profile3",
  "--no-first-run", "about:blank",
], { stdio: "ignore" })

let version = null
for (let i = 0; i < 40; i++) {
  try {
    const r = await fetch(`http://127.0.0.1:${CDP_PORT}/json/version`)
    version = await r.json()
    break
  } catch { await sleep(500) }
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

await send("Runtime.enable")
await send("Page.enable")
await sleep(2500)

const report = {}

// 1. 打开设置面板,验证两个分类标签
report.step1_open = await evalJs(`(async () => {
  document.getElementById('setbtn').click();
  await new Promise(r => setTimeout(r, 600));
  return JSON.stringify({
    tabs: [...document.querySelectorAll('.sptab')].map(t => t.textContent + (t.classList.contains('on') ? '(on)' : '')),
    apiVisible: document.getElementById('tab-api').style.display !== 'none',
    lookHidden: document.getElementById('tab-look').style.display === 'none',
    apiRows: document.querySelectorAll('.apirow').length,
  });
})()`)

// 2. 切到外观标签,选天空蓝
report.step2_blue = await evalJs(`(async () => {
  document.querySelector('.sptab[data-tab="look"]').click();
  await new Promise(r => setTimeout(r, 300));
  const opts = [...document.querySelectorAll('.themeopt')].map(o => o.textContent + (o.classList.contains('cur') ? '(cur)' : ''));
  document.querySelector('.themeopt[data-t="blue"]').click();
  await new Promise(r => setTimeout(r, 300));
  const grad = getComputedStyle(document.querySelector('header')).backgroundImage;
  return JSON.stringify({
    options: opts,
    bodyHasBlue: document.body.classList.contains('theme-blue'),
    stored: localStorage.getItem('wb_theme'),
    headerGradient: grad.slice(0, 90),
    blueOptionCur: document.querySelector('.themeopt[data-t="blue"]').classList.contains('cur'),
  });
})()`)

// 3. 关闭面板重开,验证主题记住 + 标签回到 API 时列表正常
report.step3_persist = await evalJs(`(async () => {
  document.getElementById('setclose').click();
  await new Promise(r => setTimeout(r, 300));
  document.getElementById('setbtn').click();
  await new Promise(r => setTimeout(r, 600));
  return JSON.stringify({
    bodyStillBlue: document.body.classList.contains('theme-blue'),
    storedStill: localStorage.getItem('wb_theme'),
    tabsOnOpen: document.querySelector('.sptab.on')?.dataset.tab,
  });
})()`)

// 4. 切回粉色
report.step4_backPink = await evalJs(`(async () => {
  document.querySelector('.sptab[data-tab="look"]').click();
  await new Promise(r => setTimeout(r, 200));
  document.querySelector('.themeopt[data-t="pink"]').click();
  await new Promise(r => setTimeout(r, 300));
  return JSON.stringify({
    bodyBlue: document.body.classList.contains('theme-blue'),
    stored: localStorage.getItem('wb_theme'),
  });
})()`)

report.jsErrors = jsErrors.slice(0, 3)
console.log(JSON.stringify(report, null, 2))
ws.close()
proc.kill()
process.exit(0)
