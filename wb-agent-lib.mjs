/**
 * wb-agent-lib - 无人值守 Agent 驱动核心(N.E.K.O. 附属工作台)
 *
 * 支持两种引擎协议:
 *  - v1(bun dev serve): 端点无前缀,每请求 ?directory= 路由,无鉴权,prompt 为 {model,parts}
 *  - v2(opencode-cli.exe 编译版): 端点带 /api 前缀,Basic 鉴权,prompt 为 {text},按进程 cwd 路由
 *
 * driveAgent({text, dir, model, server, timeout, auth}):
 *   1. 创建会话 2. 切模型 3. 发任务 4. 订阅事件流自动应答权限/提问 5. 等空闲,返回结构化结果
 *
 * server 传 "http://host:port" → v1;传 {server, auth} 对象 → v2。
 */

import { join, dirname } from "node:path"
import { readdirSync, statSync, existsSync, readFileSync, writeFileSync, unlinkSync } from "node:fs"
import { spawn } from "node:child_process"

/**
 * @typedef {{text: string, dir: string, model?: string, server?: string, auth?: string, timeout?: number, noProgressSec?: number, heartbeat?: boolean, sessionID?: string, mode?: "build"|"plan", signal?: AbortSignal}} DriveOptions
 * @typedef {{ok: boolean, sessionId: string, idle: boolean, error: string|null, stats: {permissions: number, questions: number}, reply: string|null, artifacts: string[]}} DriveResult
 */

function basicAuth(auth) {
  return "Basic " + Buffer.from(auth).toString("base64")
}

function makeClient(server, auth) {
  const v2 = Boolean(auth)
  const base = v2 ? `${server}/api` : server
  const headers = { "content-type": "application/json" }
  if (auth) headers.authorization = basicAuth(auth)
  const call = async (path, init = {}) => {
    const res = await fetch(`${base}${path}`, { ...init, headers: { ...headers, ...(init.headers || {}) } })
    const body = await res.text()
    let json = null
    try { json = JSON.parse(body) } catch {}
    return { status: res.status, json, body }
  }
  return { v2, call }
}

/** 扫描工作区根目录,返回任务开始后新产出的 .neko-plugin / .neko-bundle 文件 */
function scanArtifacts(dir, sinceMs) {
  try {
    return readdirSync(dir)
      .filter((f) => f.endsWith(".neko-plugin") || f.endsWith(".neko-bundle"))
      .map((f) => {
        const full = join(dir, f)
        return { full, mtime: statSync(full).mtimeMs }
      })
      .filter((x) => x.mtime >= sinceMs - 2000)
      .sort((a, b) => b.mtime - a.mtime)
      .map((x) => x.full)
  } catch {
    return []
  }
}

/**
 * @param {DriveOptions} opts
 * @returns {Promise<DriveResult>}
 */
export async function driveAgent(opts) {
  const SERVER = opts.server || "http://127.0.0.1:4096"
  const client = makeClient(SERVER, opts.auth)
  const TIMEOUT = opts.timeout ?? 1500
  // 无进展看门狗:引擎侧连续 N 秒没有任何事件(模型卡死/接口吊住)就自动中止,
  // 不再傻等到总超时(实测踩过:引擎请求吊死,用户白等 24 分钟才看到「等待超时」)
  const STALL_MS = (opts.noProgressSec ?? 240) * 1000

  // 1. 复用或创建会话(opts.sessionID 传引擎会话 id → 同一会话连续记忆;失效则自动新建)
  let sessionID = opts.sessionID || null
  if (sessionID) {
    try {
      const check = await client.call(`/session/${sessionID}`)
      if (check.status !== 200) sessionID = null
    } catch {
      sessionID = null
    }
  }
  if (!sessionID) {
    const created = await client.call("/session", { method: "POST", body: "{}" })
    const createdData = client.v2 ? created.json?.data : created.json
    if (created.status !== 200 || !createdData?.id) {
      throw new Error(`创建会话失败: ${created.status} ${created.body?.slice(0, 200)}`)
    }
    sessionID = createdData.id
  }

  // 2. 切模型(v1: prompt 带 model;v2: 独立 /model 端点)
  const [providerID, modelID] = (opts.model || "sensenova/kimi-k3").split("/")

  const startedAt = Date.now()
  const controller = new AbortController()
  const status = { idle: false, idleAt: 0, permissions: 0, questions: 0, autoRetries: 0, lastActivityAt: Date.now() }
  const errors = []
  const finalTexts = []

  // 3. 事件流(断线重连):自动应答权限/提问,收集最终文本与空闲状态
  let streamDead = false
  const connectEvents = async () => {
    while (!status.idle && !streamDead) {
      try {
        const res = await fetch(client.v2 ? `${SERVER}/api/event` : `${SERVER}/event`, {
          signal: controller.signal,
          headers: client.v2 ? { authorization: basicAuth(opts.auth) } : {},
        })
        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let buf = ""
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buf += decoder.decode(value, { stream: true })
          let idx
          while ((idx = buf.indexOf("\n")) >= 0) {
            const line = buf.slice(0, idx).trim()
            buf = buf.slice(idx + 1)
            if (!line.startsWith("data:")) continue
            let evt
            try { evt = JSON.parse(line.slice(5).trim()) } catch { continue }
            const t = evt.type
            const p = client.v2 ? evt.data || {} : evt.properties || {}

            // 会话相关的任何事件都算「有进展」(模型流/工具/权限/错误),供无进展看门狗判断
            if (p.sessionID === sessionID || p.part?.sessionID === sessionID || p.info?.sessionID === sessionID) {
              status.lastActivityAt = Date.now()
            }

            if (t === "permission.asked" && p.sessionID === sessionID) {
              status.permissions++
              // v2 权限应答是全局路由 /permission/{requestID}/reply(不再挂在 session 下);
              // 失败时降级到旧的 session 级兼容路由
              const url = client.v2
                ? `/permission/${p.id}/reply`
                : `/session/${sessionID}/permissions/${p.id}`
              const body = client.v2 ? { reply: "once" } : { response: "once" }
              client.call(url, { method: "POST", body: JSON.stringify(body) }).catch(() => {
                if (client.v2) {
                  client.call(`/session/${sessionID}/permissions/${p.id}`, {
                    method: "POST", body: JSON.stringify({ response: "once" }),
                  }).catch(() => {})
                }
              })
            }
            if (t === "question.asked" && p.sessionID === sessionID) {
              status.questions++
              // v2 提问应答也是全局路由 /question/{requestID}/reply,answers: string[][](每题选第一项)
              const url = client.v2
                ? `/question/${p.id}/reply`
                : `/session/${sessionID}/question/${p.id || ""}/reply`
              const answers = Array.isArray(p.questions)
                ? p.questions.map((q) => (client.v2 ? (q.options || q.choices || [])[0] : { questionID: q.id, choice: q.choices?.[0]?.id }))
                : undefined
              const answerPayload = client.v2
                ? { answers: Array.isArray(answers) ? answers.map((a) => [a?.label || a]) : [] }
                : { answers }
              client.call(url, { method: "POST", body: JSON.stringify(answerPayload) }).catch(() => {})
            }
            if (t === "session.error" && p.sessionID === sessionID) {
              const msg = client.v2
                ? (p.error?.message || p.error?.data?.message || p.error?.name || JSON.stringify(p.error))
                : (p.error?.data?.message || p.error?.name || String(p.error))
              errors.push(msg)
              // 限流/网关类瞬时错误:自动重试让 Agent 接着干(平台侧 429,最多 3 次,防配额耗尽时空转)
              if (/rate.?limit|429|tpm|rpm|overloaded|503|timeout/i.test(msg) && status.autoRetries < 3) {
                status.autoRetries++
                client.call(`/session/${sessionID}/prompt`, {
                  method: "POST",
                  body: JSON.stringify({ text: "继续", delivery: "queue" }),
                }).catch(() => {})
              }
            }
            // 文本增量(v1);v2 的 part 结构不同,最终回复直接拉历史消息
            if (!client.v2 && t === "message.part.updated" && p.part?.type === "text" && p.part.sessionID === sessionID) {
              const msgID = p.part.messageID
              if (msgID && !finalTexts.some((x) => x.msgID === msgID)) finalTexts.push({ msgID, text: "" })
              const slot = finalTexts.find((x) => x.msgID === msgID)
              if (slot) slot.text = p.part.text || slot.text
            }
            if (client.v2) {
              if (t === "session.idle" && p.sessionID === sessionID && !status.idle) {
                status.idle = true
                status.idleAt = Date.now()
              }
            } else if (t === "session.status" && p.sessionID === sessionID) {
              if (p.status?.type === "idle" && !status.idle) {
                status.idle = true
                status.idleAt = Date.now()
              } else if (p.status?.type !== "idle") {
                status.idle = false
              }
            }
          }
        }
      } catch (e) {
        if (controller.signal.aborted) return
        errors.push(`event stream: ${e.message}`)
      }
      if (!status.idle && !controller.signal.aborted) {
        await new Promise((r) => setTimeout(r, 2000))
      }
    }
  }
  const eventLoop = connectEvents()

  // 4. 发任务(agent 字段:build=可改文件;plan=只规划不改,引擎原生支持)
  // 发送前先兜底中断一次:上一任务若还占着会话(刚被停止/异常残留),
  // 新 prompt 会被引擎当 steer 排队永不执行(实测踩过)。abort 对空闲会话无害,404 也不影响
  if (client.v2) {
    await Promise.race([
      client.call(`/session/${sessionID}/abort`, { method: "POST" }).catch(() => {}),
      new Promise((r) => setTimeout(r, 4000)),
    ])
    await new Promise((r) => setTimeout(r, 800))
  }
  const agentName = opts.mode === "plan" ? "plan" : "build"
  const startedAtMs = Date.now() // 本轮起点:抓回复时只认这之后的新消息
  let sent
  if (client.v2) {
    // 会话级切模型,失败静默(保持引擎默认模型也能跑)
    await client
      .call(`/session/${sessionID}/model`, {
        method: "POST",
        body: JSON.stringify({ model: { id: modelID, providerID } }),
      })
      .catch(() => {})
    sent = await client.call(`/session/${sessionID}/prompt`, {
      method: "POST",
      body: JSON.stringify({ text: opts.text, agent: agentName }),
    })
  } else {
    const prompt = {
      ...(opts.model ? { model: { providerID, modelID } } : {}),
      agent: agentName,
      parts: [{ type: "text", text: opts.text }],
    }
    sent = await client.call(`/session/${sessionID}/prompt_async`, {
      method: "POST",
      body: JSON.stringify(prompt),
    })
  }
  if (!(sent.status === 200 || sent.status === 204)) {
    controller.abort()
    throw new Error(`发送任务失败: ${sent.status} ${sent.body?.slice(0, 300)}`)
  }

  // 5. 等空闲:事件流 idle + HTTP 复核(防幻影),或 /wait 阻塞端点兜底
  // opts.signal 中止支持:停止按钮触发,abort 引擎会话并结束等待
  const deadline = Date.now() + TIMEOUT * 1000
  let lastBeat = Date.now()
  let aborted = false
  let stalled = false
  const abortEngine = () => client.call(`/session/${sessionID}/abort`, { method: "POST" }).catch(() => {})
  const onAbort = () => {
    if (aborted) return
    aborted = true
    abortEngine()
  }
  if (opts.signal) {
    if (opts.signal.aborted) onAbort()
    else opts.signal.addEventListener("abort", onAbort)
  }
  try {
  // 可中断 sleep:abort 时立即醒来,不再等完 5 秒
  const sleepCancellable = (ms) => new Promise((r) => {
    const t = setTimeout(r, ms)
    if (opts.signal) opts.signal.addEventListener("abort", () => { clearTimeout(t); r() }, { once: true })
  })
  while (Date.now() < deadline) {
    if (aborted) break
    await sleepCancellable(5000)
    if (aborted) break
    // 无进展看门狗:连续 STALL_MS 没有任何会话事件 → 引擎侧卡死(模型请求吊住等),主动中止
    if (!status.idle && Date.now() - status.lastActivityAt > STALL_MS) {
      stalled = true
      abortEngine()
      break
    }
    if (client.v2) {
      try {
        // /wait 是引擎长轮询,会阻塞到会话空闲;给单次请求加超时,避免 abort 后挂住整个循环
        const w = await Promise.race([
          client.call(`/session/${sessionID}/wait`, { method: "POST" }),
          new Promise((r) => setTimeout(() => r(null), 10000)),
        ])
        if (w && w.status === 204) {
          // /wait 返回即空闲;再给事件流一点时间把最终文本送进来
          if (!status.idle) {
            status.idle = true
            status.idleAt = Date.now()
          }
          if (Date.now() - status.idleAt > 3000) break
        }
      } catch {}
      continue
    }
    if (status.idle && Date.now() - status.idleAt > 6000) {
      try {
        const st = await client.call("/session/status")
        const cur = st.json?.[sessionID]?.type
        if (cur && cur !== "idle") {
          status.idle = false
          continue
        }
      } catch {}
      break
    }
    if (opts.heartbeat && Date.now() - lastBeat > 60000) {
      lastBeat = Date.now()
      console.error(`[heartbeat] waiting... permissions=${status.permissions} errors=${errors.length}`)
    }
  }
  } finally {
    if (opts.signal) opts.signal.removeEventListener("abort", onAbort)
  }
  streamDead = true
  controller.abort()
  await eventLoop.catch(() => {})

  // v2: 拉历史消息,只认「本轮任务开始后」的新 assistant 消息:
  // ①不能拿上一轮的旧回复充数(否则本轮任务失败时,用户看到的是旧话,像"Agent 只会说一句话")
  // ②本轮 finish=error 的消息要提取真实原因(限流/密钥错等)反馈给用户
  // (/wait 确认空闲时最后一轮回复可能尚未落库,所以这里允许短轮询)
  let finalReply = null
  let lastAssistantError = null
  let thinking = null
  let turnUsage = null
  if (client.v2 && !aborted && !stalled) {
    const replyDeadline = Date.now() + 30_000
    while (Date.now() < replyDeadline) {
      try {
        const ms = await client.call(`/session/${sessionID}/message`)
        const list = (ms.json?.data || []).filter((m) => m.type === "assistant" && (m.time?.created || 0) >= Math.max(startedAt, startedAtMs || 0))
        list.sort((a, b) => (a.time?.created || 0) - (b.time?.created || 0))
        // 单次全量扫描:思考过程(reasoning)散布在多步任务的多条消息里,必须全扫;
        // 每轮覆盖式重算,避免轮询重复拼接
        const thinkAll = []
        let fr = null
        let lae = null
        const usage = { input: 0, output: 0, reasoning: 0 }
        for (const m of list) {
          const tp = (m.content || []).filter((c) => (c.type === "reasoning" || c.type === "thinking") && c.text).map((c) => c.text)
          if (tp.length) thinkAll.push(tp.join("\n\n"))
          // token 用量:聚合本轮全部 assistant 消息(多步任务每步都有消耗)
          const t = m.tokens || {}
          usage.input += t.input || 0
          usage.output += t.output || 0
          usage.reasoning += t.reasoning || 0
          const textPart = (m.content || []).find((c) => c.type === "text" && c.text)
          if (textPart && m.finish === "stop") fr = textPart.text
          if (m.finish === "error" && m.error) {
            const em = m.error?.message || m.error?.data?.message
            lae = em ? `${m.error?.type || "model error"}: ${String(em).slice(0, 300)}` : JSON.stringify(m.error).slice(0, 300)
          }
        }
        thinking = thinkAll.length ? thinkAll.join("\n\n") : null
        turnUsage = usage
        if (fr) { finalReply = fr; break }
        lastAssistantError = lae
        // 本轮已明确失败且会话空闲:不用傻等 30 秒
        if (lastAssistantError && status.idle) break
      } catch {}
      await new Promise((r) => setTimeout(r, 2000))
    }
  }

  const fatal = !status.idle
  // 本轮失败(finish=error)且无新回复:优先展示模型侧真实原因(限流/密钥错等),
  // 其次是错误事件,绝不静默成功
  const reason = aborted
    ? "已被用户停止"
    : stalled
      ? `模型服务已 ${Math.round(STALL_MS / 1000)} 秒没有任何响应,任务被自动停止(常见原因:接口卡住或服务不稳定)。可重试一次,或到 ⚙ 设置里换一个更稳定的服务商`
      : !finalReply && lastAssistantError
      ? lastAssistantError
      : fatal
        ? errors[errors.length - 1] || "等待超时,会话未完成"
        : !finalReply
          ? "本轮没有生成新回复(模型未返回内容,可重试)"
          : null
  const okFinal = !aborted && !stalled && !fatal && Boolean(finalReply)
  return {
    ok: okFinal,
    sessionId: sessionID,
    idle: status.idle,
    stopped: aborted,
    error: reason,
    warnings: fatal ? [] : errors.slice(-5),
    stats: { permissions: status.permissions, questions: status.questions },
    reply: finalReply,
    thinking,
    usage: turnUsage,
    artifacts: scanArtifacts(opts.dir, startedAt),
  }
}

/**
 * CLI 底座驱动(engineMode:"cli",任意 Agent CLI 可插拔引擎)
 *
 * 与 driveAgent 返回形状完全兼容,但不依赖 opencode 服务:
 * 把整段任务文本交给外部 Agent CLI(claude/codex/omp/opencode/…)非交互执行,
 * stdout 即回复;产物沿用工作区扫描(scanArtifacts);权限/提问自动应答不适用(CLI 自理)。
 *
 * opts: {text, dir, cmd:[…,{text}], timeoutSec, signal, sessionID}
 *   cmd 数组中含 {text} 的元素替换为任务文本;spawn 不经 shell,无注入面。
 *   进程环境自动注入 WB_PROJECTS_DIR/WB_NEKO_REPO(供底座里的 wb-plugin CLI 使用)。
 */
;// ---------- CLI 底座(engineMode:"cli")的解析与驱动 ----------

/** 按 PATHEXT 在 PATH(或显式路径)上定位命令;返回 {path, isScript} 或 null */
function findOnPath(name) {
  const exts = (process.env.PATHEXT || ".COM;.EXE;.BAT;.CMD").split(";").map((e) => e.toLowerCase())
  const hasDir = /[\\/]/.test(name)
  const rawExts = [""].concat(/\.(exe|com|cmd|bat)$/i.test(name) ? [] : exts)
  const bases = hasDir ? [name] : (process.env.PATH || "").split(";").filter(Boolean).map((d) => join(d, name))
  for (const base of bases) {
    for (const ext of rawExts) {
      const p = base + ext
      if (existsSync(p)) {
        const e = p.slice(p.lastIndexOf(".")).toLowerCase()
        return { path: p, isScript: e === ".cmd" || e === ".bat" }
      }
    }
  }
  return null
}

/**
 * 容错解析 npm 式 .cmd shim(移植自 neko-agent-bridge parse_cmd_shim 的策略):
 * 收集 SET 变量并展开值里的 %VAR%/%dp0% → 取最后一条含 %* 的转发行 → 整行展开
 * (SET 变量 + 进程 env)→ 在 %* 之前的 token 里找脚本(.js/.mjs/.cjs,取最后一个),
 * 解释器 = 脚本前一个 token;找不到脚本 token 时按「末 token 兜底」把最后一个
 * 引号/路径形 token 当程序。裸名解释器(node/bun…)再走 PATH 定位。
 * 规范 npm 转发行形如:
 *   endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  "%dp0%\...\cli.js" %*
 */
function parseCmdShim(file) {
  try {
    const text = readFileSync(file, "utf8")
    const dir = dirname(file)
    const vars = {}
    for (const line of text.split(/\r?\n/)) {
      const m = line.match(/^\s*SET\s+"?([A-Za-z_][A-Za-z0-9_]*)=(.*)"?$/i)
      if (!m) continue
      vars[m[1].toUpperCase()] = m[2].replace(/^"|"$/g, "")
    }
    const expand = (v) => v
      .replace(/%~dp0|%dp0%/gi, dir + "\\")
      .replace(/%([A-Za-z_][A-Za-z0-9_]*)%/g, (x, n) => {
        const k = n.toUpperCase()
        if (vars[k] !== undefined) return vars[k]
        if (process.env[n] !== undefined) return process.env[n]
        return x
      })
    for (const k of Object.keys(vars)) vars[k] = expand(vars[k])
    const fwdLines = text.split(/\r?\n/).filter((l) => l.includes("%*"))
    if (!fwdLines.length) return null
    const fwd = expand(fwdLines[fwdLines.length - 1]).replace(/%\*\s*$/, "").trim()
    const tokens = []
    const re = /"([^"]*)"|(\S+)/g
    let m
    while ((m = re.exec(fwd))) tokens.push(m[1] !== undefined ? m[1] : m[2])
    if (!tokens.length) return null
    const isScript = (t) => /\.(js|mjs|cjs)$/i.test(t)
    let si = -1
    for (let i = tokens.length - 1; i >= 0; i--) if (isScript(tokens[i])) { si = i; break }
    let interp
    let prefix
    if (si > 0) {
      interp = tokens[si - 1]
      prefix = [tokens[si]]
    } else {
      interp = tokens[tokens.length - 1]
      prefix = []
      for (let i = tokens.length - 1; i >= 0; i--) {
        if (/[\\/]/.test(tokens[i]) || /\.exe$/i.test(tokens[i])) { interp = tokens[i]; break }
      }
    }
    interp = String(interp).replace(/^"|"$/g, "")
    if (!/[\\/]/.test(interp) && !/\.exe$/i.test(interp)) {
      const found = findOnPath(interp)
      if (!found || found.isScript) return null
      interp = found.path
    }
    return { exe: interp, prefix }
  } catch {}
  return null
}

/**
 * 解析底座命令:PATH 上的 .exe 直接用;.cmd/.bat shim 容错解出真实解释器(避免
 * shell:false 下的 ENOENT,以及 cmd.exe /c 对中文/引号/&/|% 的转义雷区)。失败返回 null。
 */
function resolveCliBase(name) {
  const found = findOnPath(name)
  if (!found) return null
  if (!found.isScript) return { exe: found.path, prefix: [] }
  return parseCmdShim(found.path)
}

/**
 * CLI 底座驱动(engineMode:"cli",任意 Agent CLI 可插拔引擎)
 *
 * 与 driveAgent 返回形状完全兼容,但不依赖 opencode 服务:整段任务文本交给外部
 * Agent CLI(claude/codex/omp/opencode/…)非交互执行,stdout 即回复;产物沿用
 * scanArtifacts;权限/提问自动应答不适用(CLI 自理)。
 *
 * opts: {text, dir, nekoRepo, cmd:[…,{text}|{textFile}|{stdin}], timeoutSec, signal, sessionID}
 *   {text}     → 替换为任务文本(spawn 不经 shell,无注入面)
 *   {textFile} → 替换为 UTF-8 任务文本临时文件路径(工作区内,用后即删)
 *   {stdin}    → 不占 argv,任务文本经 stdin 管道写入(claude/codex print 模式原生支持)
 *   环境自动注入 WB_PROJECTS_DIR/WB_NEKO_REPO(供底座里的 wb-plugin CLI 使用)。
 */
export async function driveAgentCli(opts) {
  const template = (opts.cmd || []).map(String)
  const hasText = template.some((a) => a.includes("{text}"))
  const hasTextFile = template.some((a) => a.includes("{textFile}"))
  const hasStdin = template.some((a) => a.includes("{stdin}"))
  if (!hasText && !hasTextFile && !hasStdin) {
    return { ok: false, sessionId: opts.sessionID || `cli_${Date.now()}`, idle: true, error: "engineCmd 缺少 {text}/{textFile}/{stdin} 占位符", stats: { permissions: 0, questions: 0 }, reply: null, artifacts: [] }
  }
  const resolved = resolveCliBase(template[0])
  if (!resolved && hasText) {
    return { ok: false, sessionId: opts.sessionID || `cli_${Date.now()}`, idle: true, error: `无法把底座 "${template[0]}" 解析为可执行文件(.cmd shim 解析失败)。请改用真实 exe/node 脚本形式,或改用 {textFile}/{stdin} 占位符`, stats: { permissions: 0, questions: 0 }, reply: null, artifacts: [] }
  }
  const startedAt = Date.now()
  const timeoutSec = opts.timeoutSec ?? 1500
  let textFile = null
  if (hasTextFile) {
    textFile = join(opts.dir || process.cwd(), `.wb-cli-task-${startedAt}.txt`)
    writeFileSync(textFile, opts.text ?? "", "utf8")
  }
  const spawnPath = resolved ? resolved.exe : template[0]
  const finalArgs = [...(resolved ? resolved.prefix : []), ...template.slice(1)]
    .map((a) => String(a).replace("{text}", opts.text ?? "").replace("{textFile}", textFile ?? ""))
    // {stdin} 只是通道标记(文本经管道写入),不留在 argv 里
    .filter((a) => !a.includes("{stdin}"))
  return await new Promise((resolve) => {
    let settled = false
    let child
    try {
      child = spawn(spawnPath, finalArgs, {
        cwd: opts.dir || process.cwd(),
        env: {
          ...process.env,
          WB_PROJECTS_DIR: opts.dir || "",
          WB_NEKO_REPO: opts.nekoRepo || process.env.WB_NEKO_REPO || "",
        },
        stdio: [hasStdin ? "pipe" : "ignore", "pipe", "pipe"],
        windowsHide: true,
      })
    } catch (e) {
      if (textFile) try { unlinkSync(textFile) } catch {}
      resolve({ ok: false, sessionId: opts.sessionID || `cli_${startedAt}`, idle: true, error: `无法启动 CLI 底座:${e?.message || e}`, stats: { permissions: 0, questions: 0 }, reply: null, artifacts: [] })
      return
    }
    if (hasStdin) {
      child.stdin.write(opts.text ?? "")
      child.stdin.end()
    }
    const CAP = 200000
    let out = ""
    let errText = ""
    child.stdout.on("data", (c) => { if (out.length < CAP) out += c })
    child.stderr.on("data", (c) => { if (errText.length < CAP) errText += c })
    const cleanup = () => { if (textFile) try { unlinkSync(textFile) } catch {} }
    const killTree = () => { try { spawn("taskkill", ["/F", "/PID", String(child.pid), "/T"], { stdio: "ignore" }) } catch {} }
    const timer = setTimeout(() => { killTree(); finish({ kind: "timeout" }) }, timeoutSec * 1000)
    if (opts.signal) {
      if (opts.signal.aborted) { killTree(); finish({ kind: "stopped" }) }
      else opts.signal.addEventListener("abort", () => { killTree(); finish({ kind: "stopped" }) }, { once: true })
    }
    child.on("error", (e) => finish({ kind: "spawn", message: `无法启动 CLI 底座:${e?.message || e}` }))
    child.on("close", (code) => finish({ kind: "close", code }))
    function finish(fail) {
      if (settled) return
      settled = true
      clearTimeout(timer)
      cleanup()
      const artifacts = scanArtifacts(opts.dir || process.cwd(), startedAt)
      const base = { sessionId: opts.sessionID || `cli_${startedAt}`, idle: true, stats: { permissions: 0, questions: 0 }, artifacts }
      if (fail.kind === "spawn") {
        resolve({ ok: false, ...base, error: fail.message, reply: null })
        return
      }
      if (fail.kind === "stopped") {
        resolve({ ok: false, ...base, error: "任务已停止", reply: out.trim() || null, stopped: true })
        return
      }
      if (fail.kind === "timeout") {
        resolve({ ok: false, ...base, error: `CLI 底座超时(${timeoutSec}s),已连子进程一起清理`, reply: out.trim() || null })
        return
      }
      const code = fail.code
      const reply = out.trim()
      resolve({
        ok: code === 0 && reply.length > 0,
        ...base,
        error: code === 0 ? null : `CLI 底座退出码 ${code}:${(errText.trim() || out.trim() || "(无输出)").slice(-800)}`,
        reply: reply || null,
      })
    }
  })
}
