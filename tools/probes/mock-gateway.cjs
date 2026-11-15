// 可返回 tool_call 的 mock 网关:用于「真引擎端到端」验证外发写确实会触发 permission.asked。
// 用法:node tools/probes/mock-gateway.cjs [端口] [--capture]
//   --capture 会把每次请求(含 shell 工具的参数 schema)记到系统临时目录,便于看清引擎发了什么
const http = require("http")
const fs = require("fs")
const os = require("os")
const path = require("path")

const PORT = Number(process.argv[2] || 4321)
const CAP = process.argv.includes("--capture")
// 抓包日志写在系统临时目录:原来写到仓库 runtime/,目录不存在时 appendFileSync 抛错被吞,
// 结果「有没有请求打到网关」这件事完全没有证据,排查时最容易误判成「请求来了但没回」。
const LOGDIR = path.join(os.tmpdir(), "wb-mock")
try { fs.mkdirSync(LOGDIR, { recursive: true }) } catch {}
const LOG = path.join(LOGDIR, "mock-requests-" + PORT + ".log")

// 端到端断言的凭据不写标记文件:那种做法依赖 shell 的语句分隔符(cmd 用 &,bash 用 ;,
// PowerShell 只认 ;)和引擎 shell 工具的默认 workdir,任一不符就是假阴性(实测连踩两次)。
// 改成「网关计数」:引擎只有在**真的执行完工具**之后才会带着工具结果再发一次请求,
// 这件事与命令内容、退出码、工作目录都无关 —— 批准前后各计一次就是铁证。
const PROBE_CMD = "git push origin main"
const hits = { reqs: 0, pushToolCalls: 0, toolResultReqs: 0, lastAt: "" }

function reply(res, obj) {
  res.writeHead(200, { "content-type": "application/json" })
  res.end(JSON.stringify(obj))
}
// 引擎一律带 stream:true(抓包实测),这时回普通 JSON 会被当成协议错误 →
// 工坊只看到「模型无响应(error)」。必须按 SSE 分块回,最后一块 data:[DONE]。
function replySSE(res, chunks) {
  res.writeHead(200, {
    "content-type": "text/event-stream",
    "cache-control": "no-cache",
    connection: "keep-alive",
  })
  for (const c of chunks) res.write("data: " + JSON.stringify(c) + "\n\n")
  res.write("data: [DONE]\n\n")
  res.end()
}
function chunk(model, delta, finish) {
  return {
    id: "chatcmpl-mock-" + Date.now(),
    object: "chat.completion.chunk",
    created: Math.floor(Date.now() / 1000),
    model,
    choices: [{ index: 0, delta, finish_reason: finish || null }],
  }
}
// OpenAI 补全响应要成样子:id/object/created/model/index 都齐,
// 少字段时引擎会直接报错,看起来就像「模型无响应」(踩过)
function completion(model, message, finish, usage) {
  return {
    id: "chatcmpl-mock-" + Date.now(),
    object: "chat.completion",
    created: Math.floor(Date.now() / 1000),
    model,
    choices: [{ index: 0, message, finish_reason: finish }],
    usage: usage || { prompt_tokens: 8, completion_tokens: 8, total_tokens: 16 },
  }
}

const srv = http.createServer((req, res) => {
  let body = ""
  req.on("data", (c) => (body += c))
  req.on("end", () => {
    const u = String(req.url || "")
    let parsed = {}
    try { parsed = JSON.parse(body || "{}") } catch {}
    if (CAP) {
      const tools = (parsed.tools || []).map((t) => t.function && t.function.name).filter(Boolean)
      const shell = (parsed.tools || []).find((t) => t.function && /^(shell|bash)$/i.test(t.function.name))
      try {
        fs.appendFileSync(LOG, JSON.stringify({
          at: new Date().toISOString(),
          url: u,
          stream: !!parsed.stream,
          tools,
          shellParams: shell && shell.function && shell.function.parameters ? JSON.stringify(shell.function.parameters).slice(0, 600) : null,
          msgs: String(JSON.stringify(parsed.messages || []).slice(0, 500)),
        }) + "\n", "utf8")
      } catch {}
    }
    res.setHeader("content-type", "application/json")
    if (u.indexOf("/models") >= 0) { reply(res, { data: [{ id: "mock-model" }, { id: "mock-model-2" }] }); return }
    if (u.indexOf("/stats") >= 0) {
      if (u.indexOf("reset=1") >= 0) { hits.reqs = 0; hits.pushToolCalls = 0; hits.toolResultReqs = 0; hits.lastAt = "" }
      reply(res, { ok: true, hits })
      return
    }
    if (u.indexOf("/chat/completions") >= 0) {
      const msg = JSON.stringify(parsed.messages || [])
      const model = parsed.model || "mock-model"
      const toolList = (parsed.tools || []).map((t) => t.function && t.function.name).filter(Boolean)
      hits.reqs++
      hits.lastAt = new Date().toISOString()
      // 「本轮是否带着工具结果回来」只看结构,不看正文关键词:
      // 用关键词判断时,同一会话跑第二次会因为历史里残留 PUSHED 而误判成「已执行」(实测踩到)
      const hasToolResult = /"role"\s*:\s*"tool"|tool_call_id|"type"\s*:\s*"tool-result"/i.test(msg)
      if (hasToolResult) hits.toolResultReqs++
      if (!hasToolResult && msg.indexOf("PUSHME") >= 0 && toolList.some((n) => /^(shell|bash)$/i.test(n))) {
        const name = toolList.find((n) => /^(shell|bash)$/i.test(n))
        const args = JSON.stringify({ command: PROBE_CMD })
        hits.pushToolCalls++
        if (parsed.stream) {
          replySSE(res, [
            chunk(model, { role: "assistant", content: "我需要先把改动推到远端" }),
            chunk(model, { tool_calls: [{ index: 0, id: "call_push_1", type: "function", function: { name, arguments: args } }] }),
            chunk(model, {}, "tool_calls"),
          ])
          return
        }
        reply(res, completion(model, {
          role: "assistant",
          content: "我需要先把改动推到远端",
          tool_calls: [{ id: "call_push_1", type: "function", index: 0, function: { name, arguments: args } }],
        }, "tool_calls"))
        return
      }
      if (parsed.stream) {
        replySSE(res, [chunk(model, { role: "assistant" }), chunk(model, { content: "mock 回复:已完成(无需工具)。" }), chunk(model, {}, "stop")])
        return
      }
      reply(res, completion(model, { role: "assistant", content: "mock 回复:已完成(无需工具)。" }, "stop"))
      return
    }
    res.writeHead(404)
    res.end(JSON.stringify({ error: "no route" }))
  })
})
srv.listen(PORT, "127.0.0.1", () => console.log("mock 网关 on http://127.0.0.1:" + PORT + "/v1" + (CAP ? " · 抓请求日志 → " + LOG : "")))
