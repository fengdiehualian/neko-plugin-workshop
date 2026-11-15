// 本地 OpenAI 兼容 mock 网关:给隔离实例当「已配置的 API」用,让工坊进入主页好跑 UI 自检。
// 用法:node tools/probes/mock-gateway.cjs [端口]   (默认 4321)
// 只做三件事:列模型、回一段固定文本、记一次请求计数。绝不联网、绝不落敏感数据。
const http = require("http")

const PORT = Number(process.argv[2] || 4321)
let hits = 0
const srv = http.createServer((req, res) => {
  let body = ""
  req.on("data", (c) => (body += c))
  req.on("end", () => {
    const u = String(req.url || "")
    res.setHeader("content-type", "application/json")
    if (u.indexOf("/models") >= 0) {
      res.end(JSON.stringify({ data: [{ id: "mock-model" }, { id: "mock-model-2" }] }))
      return
    }
    if (u.indexOf("/chat/completions") >= 0) {
      hits++
      let model = "mock-model"
      try { model = JSON.parse(body || "{}").model || model } catch {}
      res.end(JSON.stringify({ choices: [{ message: { role: "assistant", content: "mock 网关回复:" + model } }], usage: { prompt_tokens: 5, completion_tokens: 7 } }))
      return
    }
    if (u.indexOf("/stats") >= 0) { res.end(JSON.stringify({ ok: true, hits })); return }
    res.writeHead(404)
    res.end(JSON.stringify({ error: "no route" }))
  })
})
srv.listen(PORT, "127.0.0.1", () => console.log("mock gateway on http://127.0.0.1:" + PORT + "/v1"))
