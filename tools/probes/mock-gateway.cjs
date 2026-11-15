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
// 改成「网关计数」:引擎只有在真的执行完工具之后才会带着工具结果再发一次请求,
// 这件事与命令内容、退出码、工作目录都无关 —— 批准前后各计一次就是铁证。
const PROBE_CMD = "git push origin main"
// 复合命令:前面是只读动作。用来验「规则表里的只读形态会不会把整条命令放行」(issue #1)
const PROBE_CMD_COMBO = "git add . && git push origin main"
const hits = { reqs: 0, pushToolCalls: 0, toolResultReqs: 0, executedReqs: 0, rejectedReqs: 0, lastAt: "" }

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
      if (hasToolResult) {
        hits.toolResultReqs++
        // 关键区分:被拒绝时引擎同样会「带着工具结果回来」(那是一条权限被拒的回执),
        // 所以「有工具结果」不等于「命令执行了」。用结果内容把两者分开,
        // 否则拒绝路径会被读成「拒绝后还是执行了」的假阳性(实测踩过)
        if (/permission|denied|rejected|用户拒绝|已拒绝|not approved|blocked/i.test(msg)) hits.rejectedReqs++
        else hits.executedReqs++
      }
      if (!hasToolResult && /PUSHME|PUSHCOMBO/.test(msg) && toolList.some((n) => /^(shell|bash)$/i.test(n))) {
        const name = toolList.find((n) => /^(shell|bash)$/i.test(n))
        // PUSHCOMBO → 「先只读再推送」的复合命令,专门验只读形态会不会把整条命令放行
        const cmdText = /PUSHCOMBO/.test(msg) ? PROBE_CMD_COMBO : PROBE_CMD
        const args = JSON.stringify({ command: cmdText })
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
