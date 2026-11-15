// 直接打引擎 API,看清「模型无响应」到底是什么错(是连不上 mock 网关,还是工具/权限配置被拒)
// 用法:node tools/probes/engine-direct-probe.cjs [实例目录] [引擎端口] [网关端口]
const fs = require("fs")
const path = require("path")
const { spawn } = require("child_process")

const P = require("./paths.cjs")
const DIR = process.argv[2] || P.testDir()
const EPORT = Number(process.argv[3] || 4097)
const MOCK = Number(process.argv[4] || 4321)
const E = "http://127.0.0.1:" + EPORT
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const auth = () => {
  try {
    const j = JSON.parse(fs.readFileSync(path.join(DIR, "runtime", "engine-auth.json"), "utf8"))
    return "Basic " + Buffer.from("opencode:" + j.password).toString("base64")
  } catch { return "" }
}
const H = () => ({ authorization: auth(), "content-type": "application/json" })

async function call(p, opt) {
  const r = await fetch(E + p, Object.assign({ signal: AbortSignal.timeout(20000) }, opt))
  let t = ""
  try { t = await r.text() } catch {}
  return { status: r.status, text: t }
}

;(async () => {
  console.log("引擎健康:", (await call("/api/health")).status, "| 密码来自 engine-auth.json:", auth().slice(0, 12) + "…")
  const cfgTxt = fs.readFileSync(path.join(DIR, "runtime", "config", "opencode", "opencode.json"), "utf8")
  const cfg = JSON.parse(cfgTxt)
  console.log("配置 provider.custom.options.baseURL:", cfg.provider && cfg.provider.custom && cfg.provider.custom.options && cfg.provider.custom.options.baseURL)
  console.log("配置 permission.bash 规则数:", Object.keys((cfg.permission || {}).bash || {}).length)

  const s = await call("/api/session", { method: "POST", headers: H(), body: "{}" })
  console.log("建会话:", s.status, s.text.slice(0, 160))
  const sid = (JSON.parse(s.text || "{}").id) || ""
  if (!sid) process.exit(1)

  const body = JSON.stringify({ model: { providerID: "custom", modelID: "mock-model" }, parts: [{ type: "text", text: "PUSHME 请把当前改动推送到远端" }] })
  const p = await call("/api/session/" + sid + "/prompt_async", { method: "POST", headers: H(), body })
  console.log("发任务:", p.status, p.text.slice(0, 200))

  // 拉事件流 25 秒,看有没有 permission.asked / error
  const events = []
  const ctrl = new AbortController()
  setTimeout(() => ctrl.abort(), 25000)
  try {
    const r = await fetch(E + "/api/event?directory=" + encodeURIComponent(path.join(DIR, "workspace")), {
      headers: { authorization: auth() }, signal: ctrl.signal,
    })
    const dec = new TextDecoder()
    const reader = r.body.getReader()
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      const chunk = dec.decode(value, { stream: true })
      for (const line of chunk.split(/\r?\n/)) {
        if (!line.startsWith("data:")) continue
        try {
          const e = JSON.parse(line.slice(5))
          const t = e.type || ""
          if (t === "permission.asked" || t.includes("error") || t.includes("message.part")) {
            const brief = JSON.stringify(e).slice(0, 300)
            if (events[events.length - 1] !== brief) events.push(brief)
          }
        } catch {}
      }
    }
  } catch (e) { console.log("事件流断开:", String(e.message || e).slice(0, 80)) }
  console.log("\n== 关键事件 " + events.length + " 条 ==")
  for (const e of events.slice(0, 14)) console.log("  " + e)
  const msgs = await call("/api/session/" + sid + "/message", { headers: H() })
  console.log("\n== 会话消息(截断) ==\n" + msgs.text.slice(0, 1200))
})()
