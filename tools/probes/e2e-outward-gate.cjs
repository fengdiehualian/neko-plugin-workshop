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
  chk("1a 实例就绪(加载新代码)", await waitUp(NO_RESTART ? 120 : 150), BASE + (NO_RESTART ? " [--no-restart]" : ""))
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
  chk("2b 引擎配置里 bash 通配是 ask(一律先问工坊)", !!bash && typeof bash === "object" && askN > 10 && bash["*"] === "ask",
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

  // 7) 复合命令:先只读再推送。规则表里若存在只读回放行,这条会被整条放行(issue #1 的洞)
  //    用真引擎的匹配器验证,不靠我们对 glob 语义的猜测。
  //    先确认队列已经空了再发第二段:否则会把上一段任务残留的确认项读成「复合命令也被拦了」的假通过
  for (let i = 0; i < 30; i++) {
    const a = await j(BASE + "/api/approvals")
    if (!(a.b && a.b.items && a.b.items.length)) break
    await sleep(1000)
  }
  const pre = await j(BASE + "/api/approvals")
  chk("6d 发第二段前确认队列已清空", !(pre.b && pre.b.items && pre.b.items.length), JSON.stringify(pre.b && pre.b.items))
  await j(BASE + "/api/stop")
  const hBefore = await stats()
  let t2 = null
  for (let i = 0; i < 60; i++) {
    await sleep(2000)
    const nc2 = await post("/api/sessions", {})
    const sid2 = (nc2.b && nc2.b.session && nc2.b.session.id) || ""
    const tryRes = await j(BASE + "/api/task", {
      method: "POST", headers: { "content-type": "application/json", "x-wb-token": tok() },
      body: JSON.stringify({ text: "PUSHCOMBO 先 add 再推送到远端", sessionId: sid2 }),
    })
    // 429 = 上一段还在跑,继续等;status 0 = 客户端读超时但任务已经在服务端跑起来了,
    // 当「没受理」会误判(踩过:整段复合命令测试被读成 429)
    if (tryRes.status === 429) continue
    t2 = { status: tryRes.status, b: tryRes.b }
    break
  }
  chk("6c 第二段任务被受理", !!t2, t2 ? "HTTP " + t2.status : "一直是 429,busy 没释放")
  let item2 = null
  for (let i = 0; i < 90; i++) {
    const a = await j(BASE + "/api/approvals")
    const its = (a.b && a.b.items) || []
    // 必须是「这一轮新发出的工具调用」产生的询问:网关的工具调用计数要增加
    if (its.length) { item2 = its[0]; break }
    await sleep(1000)
  }
  const hMid = await stats()
  chk("6a 复合命令(git add && git push)也进确认队列", !!item2 && hMid.pushToolCalls > hBefore.pushToolCalls,
    item2 ? "reason=" + item2.reason + " 命令=" + String(item2.detail).replace(/\s+/g, " ").slice(0, 120) +
      " 工具调用 " + hBefore.pushToolCalls + "→" + hMid.pushToolCalls : "90s 队列为空 → 引擎把整条放行了")
  const h3 = await stats()
  // 用「真执行计数」判定,不用「带工具结果回来」:被拒绝时引擎也会回一条工具结果(拒绝回执),
  // 两者混在一起会把拒绝路径读成「拒绝后还是执行了」(实测踩过)
  chk("6b 复合命令在批准前没有新执行", (h3.executedReqs || 0) === (hBefore.executedReqs || 0),
    "真执行计数 " + (hBefore.executedReqs || 0) + "→" + (h3.executedReqs || 0) + "(应不变)")

  // 8) 点「不要」→ 命令必须始终不执行
  if (item2) {
    // 基线在点之前取,不做清零:上一段任务的工具结果可能还在路上,
    // 清零后重读会把那条在途记录算成「拒绝后还是执行了」(实测踩过)
    const base = await stats()
    const rj = await post("/api/approvals", { id: item2.id, reply: "reject" })
    chk("7a 「不要」被服务端接受", rj.status === 200 && rj.b && rj.b.found === true, JSON.stringify(rj.b))
    await sleep(10000)
    const h4 = await stats()
    chk("7b 拒绝后命令没有执行", (h4.executedReqs || 0) <= (base.executedReqs || 0),
      "真执行计数 " + (base.executedReqs || 0) + "→" + (h4.executedReqs || 0) + "(不应增加);拒绝回执 " + (h4.rejectedReqs || 0) + " 条")
  }
  console.log("   第二段任务返回:", t2 ? "HTTP " + t2.status + " ok=" + (t2.b && t2.b.ok) : "没被受理")
  console.log("== 端到端合计 PASS=" + pass + " FAIL=" + fail + " ==")
  process.exit(fail ? 1 : 0)
})()
