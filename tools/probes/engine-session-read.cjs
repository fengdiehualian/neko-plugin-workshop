// 读引擎会话消息:看 shell 工具在「批准后」到底输出了什么、工作目录是哪(标记文件没出现的真正原因)
// 用法:node tools/probes/engine-session-read.cjs [实例目录] [引擎端口] [会话id]
const fs = require("fs")
const path = require("path")
const P = require("./paths.cjs")
const DIR = process.argv[2] || P.testDir()
const EPORT = Number(process.argv[3] || 4097)
const SID = process.argv[4] || ""
const auth = () => {
  try {
    const j = JSON.parse(fs.readFileSync(path.join(DIR, "runtime", "engine-auth.json"), "utf8"))
    return "Basic " + Buffer.from("opencode:" + j.password).toString("base64")
  } catch { return "" }
}
;(async () => {
  if (!SID) { console.log("用法:需要会话 id"); process.exit(1) }
  const r = await fetch("http://127.0.0.1:" + EPORT + "/api/session/" + SID + "/message", {
    headers: { authorization: auth() }, signal: AbortSignal.timeout(20000),
  })
  const txt = await r.text()
  console.log("HTTP", r.status, "长度", txt.length)
  let j = null
  try { j = JSON.parse(txt) } catch { console.log(txt.slice(0, 600)); process.exit(1) }
  const list = j.data || j
  for (const m of (Array.isArray(list) ? list : [])) {
    const parts = m.parts || m.content || []
    for (const p of (Array.isArray(parts) ? parts : [])) {
      const t = p.type || ""
      if (t === "tool" || /tool/i.test(t) || p.tool) {
        console.log("  工具:", p.tool || p.name, "| 状态:", (p.state && p.state.status) || p.status || "")
        const st = p.state || {}
        if (st.input) console.log("    input:", JSON.stringify(st.input).slice(0, 260))
        if (st.output !== undefined) console.log("    output:", JSON.stringify(st.output).slice(0, 500))
        if (st.error) console.log("    error:", JSON.stringify(st.error).slice(0, 400))
        if (st.title) console.log("    title:", String(st.title).slice(0, 120))
      } else if (t === "text") {
        console.log("  文本:", String(p.text || "").replace(/\s+/g, " ").slice(0, 160))
      } else if (t === "step-start" || t === "step-finish") {
        console.log("  " + t + ":", JSON.stringify(p).slice(0, 160))
      } else {
        console.log("  其他:", t, JSON.stringify(p).slice(0, 200))
      }
    }
  }
})().catch((e) => console.log("读取出错:" + String((e && e.message) || e).slice(0, 200)))
