// 真引擎端到端:外发写命令必须先进确认队列、批准前不得执行(issue #10 要的回归用例)
//
// 用法:node tools/probes/e2e-outward-gate.cjs [studio地址] [mock网关端口]
// 前置:隔离实例目录(WB_TEST_DIR,默认 _wb_test)与 mock 网关(4321,可回 tool_call)
// 步骤:重启实例加载新代码 → 写 API 配置(生成按模式细分的 permission)→ 再重启让引擎吃到配置
//       → 发一条会让模型要求 push 的任务 → 断言队列出现 / 未批准不执行 / 批准后执行
const { spawn } = require("child_process")
const fs = require("fs")
const path = require("path")

const BASE = process.argv[2] || "http://127.0.0.1:5199"
const PORT = Number(new URL(BASE).port || 5199)
const MOCK_PORT = Number(process.argv[3] || 4321)
const P = require("./paths.cjs")
const DIR = P.testDir()
const NODE = process.execPath
// 与 mock 网关同源:标记写在系统临时目录(绝对路径),
// 不依赖引擎 shell 工具的默认工作目录(它不等于工坊 workspace)
const os = require("os")
const MARKER = path.join(os.tmpdir(), "wb-probe-marker.txt")
const ENGINE_PORT = Number(process.env.WB_ENGINE_PORT || 4097)
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const tok = () => { try { return fs.readFileSync(path.join(DIR, "runtime", "studio-token.txt"), "utf8").trim() } catch { return "" } }

let pass = 0, fail = 0
function chk(name, ok, detail) {
  if (ok) pass++; else fail++
  console.log((ok ? "PASS " : "FAIL ") + name + "  |  " + String(detail).slice(0, 200))
}
async function j(url, opt) {
  try {
    const r = await fetch(url, Object.assign({ signal: AbortSignal.timeout(20000) }, opt || {}))
    let b = null
    try { b = await r.json() } catch {}
    return { status: r.status, b }
  } catch (e) {
    return { status: 0, b: null, err: String((e && e.message) || e) }
  }
}
const post = (p, obj) => j(BASE + p, { method: "POST", headers: { "content-type": "application/json", "x-wb-token": tok() }, body: JSON.stringify(obj) })

function relaunch() {
  const rs = spawn(NODE, [path.join(__dirname, "restart-instance.cjs"), DIR, String(PORT), String(ENGINE_PORT)],
    { stdio: "ignore", detached: true, windowsHide: true })
  rs.unref()
}
// studio 是「先把引擎拉起来才开始监听」的(实测冷启 19s+,杀软扫新复制的二进制更慢),
// 所以这里必须等够,别按「进程起了=端口通」猜
async function waitUp(sec) {
  let last = ""
  for (let i = 0; i < sec; i++) {
    await sleep(1000)
    const h = await j(BASE + "/api/health")
    last = "HTTP " + h.status + (h.err ? " " + h.err : "")
    if (h.status === 200) {
      if (i > 20) console.log("   (等了 " + (i + 1) + "s 才监听——引擎冷启比想象慢)")
      return true
    }
  }
  console.log("   等待超时,最后一次:" + last)
  return false
}

;(async () => {
  // 0) mock 网关在不在
  let m = await j("http://127.0.0.1:" + MOCK_PORT + "/v1/models")
  if (m.status !== 200) {
    const g = spawn(NODE, [path.join(__dirname, "mock-gateway.cjs"), String(MOCK_PORT), "--capture"], { stdio: "ignore", detached: true, windowsHide: true })
    g.unref()
    await sleep(2500)
    m = await j("http://127.0.0.1:" + MOCK_PORT + "/v1/models")
  }
  chk("0a mock 网关在线", m.status === 200, "HTTP " + m.status + (m.err ? " " + m.err : ""))

  // 1) 先重启,让 studio 加载「按模式细分」的新代码
  // --no-restart:由外部先把实例起好(从本脚本 detached 拉起 cmd start 在这台机器上不生效,
  //   日志里一行都没有;关键断言是「队列有没有收到 permission.asked」,不该被启动方式绑住)
  const NO_RESTART = process.argv.includes("--no-restart")
  if (!NO_RESTART) relaunch()
  chk("1a 实例就绪(加载新代码)", await waitUp(NO_RESTART ? 20 : 150), BASE + (NO_RESTART ? " [--no-restart]" : ""))
  await sleep(3000)

  // 2) 写 API 配置:writeProvider 会生成 permission 块
  const set = await post("/api/setup", { baseURL: "http://127.0.0.1:" + MOCK_PORT + "/v1", apiKey: "mock-key", model: "mock-model", protocol: "openai" })
  chk("2a 配置写入成功", set.status === 200 && set.b && set.b.ok === true, JSON.stringify(set.b))
  const cfgFile = path.join(DIR, "runtime", "config", "opencode", "opencode.json")
  let cfg = {}
  try { cfg = JSON.parse(fs.readFileSync(cfgFile, "utf8")) } catch {}
  const bash = (cfg.permission && cfg.permission.bash) || null
  const askN = bash && typeof bash === "object" ? Object.entries(bash).filter(([, v]) => v === "ask").length : 0
  const allowN = bash && typeof bash === "object" ? Object.entries(bash).filter(([, v]) => v === "allow").length : 0
  chk("2b 引擎配置里 bash 已按模式细分", !!bash && typeof bash === "object" && askN > 10 && bash["*"] === "allow",
    "ask=" + askN + " allow=" + allowN + " 通配=" + (bash && bash["*"]))

  // 3) 再重启,让引擎吃上新配置(配置只在引擎启动时读)
  if (!NO_RESTART) relaunch()
  chk("3a 引擎已按新配置就绪", await waitUp(NO_RESTART ? 20 : 150), BASE)
  for (let i = 0; i < 40; i++) { const h = await j(BASE + "/api/health"); if (h.b && h.b.engine) break; await sleep(1500) }

  // 4) 发任务:提示词触发 mock 返回「执行 git push」的工具调用
  //    断言不看标记文件,改看网关计数:引擎只有在真的执行完工具之后,
  //    才会带着工具结果再发一次请求 —— 这件事与 shell 语法、工作目录、退出码都无关
  const stats = async () => {
    const s = await j("http://127.0.0.1:" + MOCK_PORT + "/v1/stats")
    return (s.b && s.b.hits) || { reqs: -1, pushToolCalls: -1, toolResultReqs: -1 }
  }
  await j("http://127.0.0.1:" + MOCK_PORT + "/v1/stats?reset=1")
  // 每次新建会话:复用旧会话会把上一轮的工具结果带进历史,
  // 既骗过 mock(它看到 tool 结果就不再发 tool_call),也让断言失去意义
  const nc = await post("/api/sessions", {})
  const sid = (nc.b && nc.b.session && nc.b.session.id) || ""
  chk("4c 已新建独立会话", !!sid, "sessionId=" + sid)
  const t = post("/api/task", { text: "PUSHME 请把当前改动推送到远端", sessionId: sid })

  // 5) 等确认队列出现
  let item = null
  for (let i = 0; i < 90; i++) {
    const a = await j(BASE + "/api/approvals")
    const its = (a.b && a.b.items) || []
    if (its.length) { item = its[0]; break }
    await sleep(1000)
  }
  const h1 = await stats()
  chk("4a 外发写触发了确认队列(permission.asked 真的发出)", !!item,
    item ? "kind=" + item.kind + " reason=" + item.reason + " detail=" + String(item.detail).slice(0, 90) : "90s 内队列为空 → 门禁仍不会被触发")
  chk("4b 未批准前命令没有执行(引擎没带着工具结果回来)", h1.toolResultReqs === 0,
    "请求数=" + h1.reqs + " 发出的工具调用=" + h1.pushToolCalls + " 带结果回来=" + h1.toolResultReqs)

  // 6) 批准一次 → 引擎应当执行并继续
  if (item) {
    const r = await post("/api/approvals", { id: item.id, reply: "once" })
    chk("5a 「允许这一次」被服务端接受", r.status === 200 && r.b && r.b.found === true, JSON.stringify(r.b))
    let ran = false
    for (let i = 0; i < 90; i++) {
      await sleep(1000)
      const h = await stats()
      if (h.toolResultReqs > 0) { ran = true; break }
    }
    const h2 = await stats()
    chk("5b 批准后命令执行(工具结果回到模型)", ran, "带结果回来=" + h2.toolResultReqs + " 总请求=" + h2.reqs)
  }
  const res = await t
  console.log("   任务最终返回:", res.b ? "ok=" + res.b.ok + " err=" + String(res.b.error || "").slice(0, 90) : "HTTP " + res.status + " " + (res.err || ""))
  console.log("== 端到端合计 PASS=" + pass + " FAIL=" + fail + " ==")
  process.exit(fail ? 1 : 0)
})()
