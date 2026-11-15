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

/** 任务收尾展示「已修改的文件」:工作区内任务开始后新增/改动的文件(相对路径,按时间序)。
 *  排除构建产物(.neko-plugin 等,由 scanArtifacts 单列)、缓存/依赖目录与日志噪声 */
const MOD_SKIP_DIRS = new Set([".git", ".opencode", "node_modules", "__pycache__", ".pytest_cache", "runtime", ".venv", "venv", "dist", "build"])
export function scanModifiedFiles(dir, sinceMs) {
  const out = []
  const walk = (rel) => {
    let entries
    try {
      entries = readdirSync(rel ? join(dir, rel) : dir, { withFileTypes: true })
    } catch {
      return
    }
    for (const e of entries) {
      const r = rel ? `${rel}/${e.name}` : e.name
      if (e.isDirectory()) {
        if (MOD_SKIP_DIRS.has(e.name)) continue
        walk(r)
        continue
      }
      if (/\.(neko-plugin|neko-bundle|pyc|pyo|log|tmp|swp)$/.test(e.name)) continue
      try {
        const mtime = statSync(join(dir, r)).mtimeMs
        if (mtime >= sinceMs - 2000) out.push({ rel: r, mtime })
      } catch {}
    }
  }
  walk("")
  out.sort((a, b) => a.mtime - b.mtime)
  return out.map((x) => x.rel)
}

// ---------- 模型无响应重试策略 ----------
// 只重试「值得重试且没有任何产出」的失败:
//   · 看门狗判定无响应(模型/接口吊住)、等待超时、本轮没生成任何回复
//   · 连接类错误(引擎半死、网络断开)
// 不重试:用户主动停止、已经产出回复/文件(否则重复干活、重复扣费)、
//         密钥错/鉴权失败/额度耗尽/模型不存在这类确定性错误(重试只会白等)。
const RETRY_HOPELESS = /(401|403|unauthor|invalid[^,]{0,20}(key|token)|api[\s_-]?key|密钥|quota|余额|欠费|insufficient|billing|payment|model[^,]{0,24}not[^,]{0,12}found|\b404\b)/i

/**
 * 判断一次失败要不要自动重试。
 * @param {object} o
 * @param {number} o.attemptsMade   已经尝试过几次(本次失败是第几次)
 * @param {number} o.maxRetries     最多重试几次(0 = 关闭)
 * @param {object|null} o.result    驱动层返回(抛错时传 null)
 * @param {string} [o.error]        抛出的异常文本(result 为 null 时必填)
 * @param {boolean} [o.aborted]     用户是否点了停止
 * @param {number} [o.baseDelayMs]  首次退避基数(默认 3s,之后翻倍,上限 30s)
 * @returns {{waitMs:number, kind:"no-response"|"connection"|"error", reason:string}|null}
 */
export function planModelRetry(o = {}) {
  const attemptsMade = Math.max(1, Math.floor(Number(o.attemptsMade) || 1))
  const maxRetries = Math.max(0, Math.floor(Number(o.maxRetries) || 0))
  if (o.aborted) return null
  if (attemptsMade > maxRetries) return null // attemptsMade=1 且 maxRetries=0 → 不重试
  if (o.result && o.result.stopped) return null

  const raw = String(o.error ?? o.result?.error ?? "").trim()
  const failed = Boolean(o.error) || (o.result ? o.result.ok === false : true)
  if (!failed) return null // 成功,不用重试
  // 失败了但没有任何错误文本(CLI 退出码 0 却零输出、引擎空转)= 典型的「静默无响应」,
  // 不能因为没字可匹配就放过,继续重试
  if (raw && RETRY_HOPELESS.test(raw)) return null

  // 已经有产出就别重试:回复/产物/改动文件都算「干过活了」
  if (o.result) {
    const hasWork =
      Boolean(o.result.reply) ||
      (Array.isArray(o.result.artifacts) && o.result.artifacts.length > 0) ||
      (Array.isArray(o.result.modifiedFiles) && o.result.modifiedFiles.length > 0)
    if (hasWork) return null
  }

  const kind = !raw || /模型服务已[^,]{0,8}秒|没有生成新回复|等待超时|timed?\s?out|ETIMEDOUT|timeout/i.test(raw)
    ? "no-response"
    : /Unable to connect|fetch failed|ECONNREFUSED|ECONNRESET|socket hang up|network|EPIPE|连接/i.test(raw)
      ? "connection"
      : "error"

  const base = Math.max(500, Math.floor(Number(o.baseDelayMs) || 3000))
  const waitMs = Math.min(30_000, base * Math.pow(2, attemptsMade - 1))
  return { waitMs, kind, reason: raw.slice(0, 200) }
}

// ---------- 用户长期记忆 ----------
/**
 * 把用户自定义的长期偏好拼成提示词块。
 * 只做格式化和长度保护,不做存储;存储与增删由 wb-studio 负责。
 * @param {Array<{text:string}|string>} items
 * @param {{maxChars?:number}} [opts]
 * @returns {string} 无内容时返回空串(调用方直接拼接,不用判断)
 */
export function formatUserMemory(items, opts = {}) {
  const maxChars = Math.max(200, Math.floor(Number(opts.maxChars) || 2000))
  const list = (Array.isArray(items) ? items : [])
    .map((x) => String((x && typeof x === "object" ? x.text : x) || "").trim())
    .filter(Boolean)
  if (!list.length) return ""
  const head = "【用户长期偏好(必须遵守)】"
  const foot = "(以上是用户明确要求长期记住的个性化设计偏好;做插件时必须遵守,与本轮需求冲突时以本轮需求为准)"
  const lines = []
  for (const raw of list) {
    // 按码点截断:直接 slice 会把 emoji/生僻字的代理对劈成两半,提示词里出现乱码
    const clipped = Array.from(raw.replace(/\s+/g, " ")).slice(0, 300).join("")
    const line = `${lines.length + 1}. ${clipped}`
    const next = lines.length ? `${lines.join("\n")}\n${line}` : line
    if (head.length + next.length + foot.length + 4 > maxChars) break
    lines.push(line)
  }
  if (!lines.length) return ""
  return `${head}\n${lines.join("\n")}\n${foot}\n\n`
}

/**
 * @param {DriveOptions} opts
 * @returns {Promise<DriveResult>}
 */
// ---------- 许愿解析:把一句大白话拆成可执行的结构 ----------
// 目的不是"读懂所有中文",而是让模型少问废话、少自己瞎猜:
// 能推断的给出默认值并让它汇报,推断不了的才提问。
const WISH_SIGNALS = [
  // 注意:只有出现「提醒/定时/闹钟」这类核心词才算定时插件;
  // 「每天记账」里的"每天"只是频率,不能把记账误会成提醒。
  ["timer", /提醒|闹钟|定时|倒计时|打卡|准点|每隔|日程|纪念日/],
  ["freq", /每天|每周|每小时|每分钟|每隔|每天|每日/],
  ["storage", /记一笔|记一下|记下|记账|帐本|账本|笔记|记录|备忘|清单|收藏|整理|待办|历史|统计|收集|花费|开销|收支|花了多少/],
  ["ui", /面板|界面|窗口|图表|看板|仪表盘|可视化|页面|按钮/],
  ["network", /天气|新闻|汇率|翻译|接口|API|api|网页|抓取|爬|搜索|查询/],
  ["command", /执行|运行|打开|命令|脚本|终端|快捷键/],
  ["startup", /开机|启动时|上线|欢迎|问候|打招呼/],
]

function parseIntervalSeconds(text) {
  const s = String(text || "")
  const cn = { 一: 1, 两: 2, 二: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9, 十: 10 }
  const toNum = (v) => (/^\d+$/.test(v) ? Number(v) : cn[v] || 0)
  const m = s.match(/每(?:隔)?\s*(\d+|半|[一二两三四五六七八九十]+)?\s*(秒钟|秒|分钟|分|小时|钟头|天)\s*(半)?/)
  if (m) {
    const n = m[1] === "半" ? 0.5 : toNum(m[1] || "1")
    const unit = m[2]
    let sec = n * (unit === "天" ? 86400 : /小时|钟头/.test(unit) ? 3600 : /分钟|分/.test(unit) ? 60 : 1)
    if (m[3] === "半") sec += (unit === "天" ? 43200 : /小时|钟头/.test(unit) ? 1800 : 30)
    if (sec > 0) return Math.round(sec)
  }
  if (/每小时/.test(s)) return 3600
  if (/每分钟/.test(s)) return 60
  if (/每天/.test(s)) return 86400
  if (/每周/.test(s)) return 604800
  return 0
}

/**
 * 把用户的愿望解析成结构化画像。
 * @param {string} wish
 * @returns {{kind:string, trigger:string, intervalSec:number, storage:boolean, ui:boolean, network:boolean, command:boolean, startup:boolean, defaults:string[]}}
 */
export function analyzeWish(wish) {
  const text = String(wish || "")
  const has = Object.fromEntries(WISH_SIGNALS.map(([k, re]) => [k, re.test(text)]))
  const kind = has.timer ? "reminder" : has.storage ? "notes" : has.network || has.command ? "tool" : has.startup ? "greeting" : "other"
  // 只有「提醒类」才需要间隔;记账/笔记里的"每天"只是描述频率,不用换算成秒
  const intervalSec = has.timer ? parseIntervalSeconds(text) : 0
  const trigger = has.startup && !has.timer ? "startup" : has.timer ? "interval" : "manual"
  const defaults = []
  if (has.timer && !intervalSec) defaults.push("没说间隔 → 先按每小时(3600 秒)做,汇报里写清楚")
  if (has.timer && intervalSec && /小时|分钟|天/.test(text)) defaults.push(`间隔换算成 ${intervalSec} 秒写进代码,别用分钟/毫秒混写`)
  if (has.storage) defaults.push("数据默认存 self.data_path('data.json'),重启不丢")
  if (has.ui) defaults.push("要做界面就按 hosted-ui 注册面板,别只在后台打印")
  if (has.network) defaults.push("外部接口默认 10 秒超时 + 中文兜底文案")
  if (has.command) defaults.push("执行命令默认异步 + 30 秒超时 + 输出截断")
  return {
    kind,
    trigger,
    intervalSec,
    storage: has.storage,
    ui: has.ui,
    network: has.network,
    command: has.command,
    startup: has.startup,
    defaults,
  }
}

const KIND_LABEL = { reminder: "定时提醒", notes: "记录存储", tool: "工具/外部能力", greeting: "启动问候", other: "其他/自定义" }
const TRIGGER_LABEL = { interval: "定时(按间隔自己跑)", startup: "启动时跑一次", manual: "手动/对话触发" }

function wishProfileBlock(wish) {
  const p = analyzeWish(wish)
  const bits = [
    `类型:${KIND_LABEL[p.kind] || p.kind}`,
    `触发:${TRIGGER_LABEL[p.trigger] || p.trigger}`,
    p.intervalSec ? `间隔:${p.intervalSec} 秒` : "",
    `存储:${p.storage ? "需要落盘" : "不需要"}`,
    `界面:${p.ui ? "需要" : "不需要"}`,
    `联网:${p.network ? "需要" : "不需要"}`,
  ].filter(Boolean).join(" | ")
  const lines = ["【需求解析(系统预判,供参考;和愿望原话冲突时以愿望为准)】", "- " + bits]
  if (p.defaults.length) lines.push("- 默认值(愿望没说清就按这个做,并在汇报里说明):" + p.defaults.join(";"))
  return lines.join("\n") + "\n\n"
}

/**
 * 许愿式编程的主提示词。纯函数,便于单测与快照。
 * @param {string} wish 用户原话
 * @param {"build"|"plan"} mode
 * @param {number} askCount 本会话已提问次数
 * @param {string} memory 压缩后的历史摘要
 * @param {string} userMemory 用户长期偏好块
 * @param {string} expertBlock 专家团模式块(空串=未启用)
 */
export function buildWishPrompt(wish, mode, askCount = 0, memory = "", userMemory = "", expertBlock = "") {
  const memBlock = memory
    ? `【更早对话的记忆摘要(上下文已压缩)】\n${memory}\n\n(以上是更早内容的摘要,直接在此基础上继续;不要复述摘要本身)\n\n`
    : ""
  const userMemBlock = userMemory ? String(userMemory) : ""
  const expert = expertBlock ? String(expertBlock).trim() + "\n\n" : ""
  const profile = wishProfileBlock(wish)
  if (mode === "plan") {
    const left = Math.max(0, 3 - askCount)
    return memBlock + userMemBlock + expert + [
      "【用户的愿望/当前消息】" + wish,
      "",
      "现在是「先出方案」模式:不写代码、不改文件、不执行命令,只负责把需求聊清楚并给出方案。用户是零基础小白。",
      "",
      "你的工作方式(严格遵守):",
      "1. 先判断这个愿望说得够不够细(知不知道要做什么、什么时候触发)。够细就直接出方案,别纠缠。",
      "2. 不够细 → 每轮只问一个问题,并按下面的格式附带 2-4 个候选答案(换成真实候选,每行一个;选项要短、大白话、点一下就能答):",
      "   [[WB-Q]]",
      "   候选答案一",
      "   候选答案二",
      "   候选答案三",
      "   其他",
      "   [[/WB-Q]]",
      "   问题正文写在标记块外面,讲清为什么问、每个选项什么意思;禁止技术术语。最后一项固定写「其他」(不用加括号说明,前端会在它旁边配一个输入框供用户直接打字)。",
      left > 0
        ? `3. 纪律:本会话已经问过 ${askCount} 个问题,最多再问 ${left} 个;问满 3 个必须停止提问,按最合理的理解直接出方案。`
        : "3. 纪律:本会话已经问满 3 个问题了,这一轮必须停止提问,直接按最合理的理解出方案。",
      "4. 用户说「别问了 / 直接给方案 / 差不多了」→ 立即停止提问,直接出方案。",
      "5. 用户是在修改或补充上一版方案 → 不重新提问,直接给更新版。",
      "6. 方案必须讲清四件事:什么时候触发、数据存哪、要不要界面、失败怎么办。",
      "7. 正文全大白话(插件叫什么、干什么、怎么用、分几步做),末尾单独一行写 [[WB-PLAN]]。禁止技术术语。",
      "",
      profile + "(以上解析只用于减少提问,不要把术语念给用户听)",
    ].join("\n")
  }
  return memBlock + userMemBlock + expert + [
    "【用户的愿望】" + wish,
    "",
    profile + [
      "这是一位零基础小白许的愿,请把它做成能通过校验、能实际用的 N.E.K.O. 插件:",
      "1. 模板:reminder=定时提醒 / notes=记录存储 / hello_world=其他;类名=核心功能+Plugin,PLUGIN_ID 用小写下划线英文。",
      "2. 生成后必须让 wb-plugin verify 全绿(error 清零)再交付,最多 5 轮;遵守 SKILL.md 硬规则 R1–R15。",
      "3. 默认直接开工:小歧义按最常见理解做,汇报时说明。",
      "4. 只有「关键信息缺失、猜错会大返工」才允许停下来问(分不清提醒还是记录、不知道触发方式、不知道要不要界面)。提问规则:",
      askCount > 0
        ? "   - 本会话你已经问过一次了,这一轮必须直接开工,不许再提问。"
        : "   - 本会话最多问 1 个问题,问完必须开工,不要再追问;",
      "   - 用下面格式(问题正文写在标记块外,大白话):",
      "     [[WB-Q]]",
      "     候选一",
      "     候选二",
      "     其他",
      "     [[/WB-Q]]",
      "   - 选项 2-4 个,每个都短;最后一项固定「其他」(不用加括号说明,前端自带输入框);用户答完直接开工,不要复述确认。",
      "",
      "【交付验收(逐条自检,缺一条都不算完成)】",
      "1. 触发方式与愿望一致:在 N.E.K.O. 里能按愿望说的时间/方式跑起来;",
      "2. 时间单位只用秒,换算写进注释,不要分钟毫秒混用;",
      "3. 需要保存的数据用 self.data_path(...) 落 JSON,重启不丢;",
      "4. 外部请求失败给中文兜底文案,不要把异常直接甩给用户;",
      "5. 面向用户的文字全中文,不留 TODO/pass 占位;",
      "6. verify 全绿,并给出 3 步使用说明。",
      "",
      "【常见翻车点(不要犯)】",
      "- 硬编码 C://// 绝对路径;用 time.sleep 阻塞主流程;在 import 阶段发网络请求。",
      "",
      "5. 最后用中文说人话汇报:插件叫什么、干什么、什么时候触发、怎么装(下载 .neko-plugin → N.E.K.O. 插件管理器导入 → 启动);最后一行写「我替你定的默认:…」。",
    ].join("\n"),
  ].join("\n")
}

// 等真人点头:超时/任务停止/界面没回话一律返回 null(调用方按拒绝处理),绝不无限挂住任务
const APPROVE_TIMEOUT_MS = 300000
async function askUser(fn, info, signal) {
  if (typeof fn !== "function") return null
  return await new Promise((res) => {
    let done = false
    const finish = (v) => {
      if (done) return
      done = true
      clearTimeout(timer)
      try { if (signal) signal.removeEventListener("abort", onAbort) } catch {}
      res(v === undefined ? null : v)
    }
    const onAbort = () => finish(null)
    const timer = setTimeout(() => finish(null), APPROVE_TIMEOUT_MS)
    try { if (signal) signal.addEventListener("abort", onAbort, { once: true }) } catch {}
    Promise.resolve()
      .then(() => fn(info))
      .then((v) => finish(v))
      .catch((e) => {
        console.warn("[wb-agent] 请用户确认失败(按拒绝处理):", e && e.message ? e.message : e)
        finish(null)
      })
  })
}

export async function driveAgent(opts) {
  const SERVER = opts.server || "http://127.0.0.1:4096"
  const client = makeClient(SERVER, opts.auth)
  const TIMEOUT = opts.timeout ?? 1500
  // 无进展看门狗:引擎侧连续 N 秒没有任何事件(模型卡死/接口吊住)就自动中止,
  // 不再傻等到总超时(实测踩过:引擎请求吊死,用户白等 24 分钟才看到「等待超时」)。
  // 慢中转+长思考的合法响应也可能几分钟没有新事件(服务端缓冲),默认给足 6 分钟
  const STALL_MS = (opts.noProgressSec ?? 360) * 1000

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
  const status = { idle: false, idleAt: 0, permissions: 0, questions: 0, autoRetries: 0, lastActivityAt: Date.now(), approvals: [] }
  const errors = []
  const finalTexts = []
  // 等用户确认期间刷活:不然「无进展看门狗」会把「她在看弹窗」当成模型没响应而误杀任务
  const withHeartbeat = (p) => {
    const tick = setInterval(() => { status.lastActivityAt = Date.now() }, 15000)
    const stop = () => clearInterval(tick)
    p.then(stop, stop)
    return p
  }

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

            // 会话相关的任何事件都算「有进展」(模型流/工具/权限/错误),供无进展看门狗判断。
            // 实测:模型流式事件的 sessionID 藏在多层结构里,逐路径列举会漏;
            // 慢模型长思考时看门狗会把「正在干活」误判成「无响应」而误杀任务,故对
            // message.*/permission.* 类事件再做一次全文兜底匹配
            const evtStr =
              typeof t === "string" && (t.startsWith("message.") || t.startsWith("permission.") || t.startsWith("step."))
                ? JSON.stringify(p)
                : ""
            if (
              p.sessionID === sessionID || p.part?.sessionID === sessionID ||
              p.info?.sessionID === sessionID || p.message?.sessionID === sessionID ||
              (evtStr && evtStr.includes(sessionID))
            ) {
              status.lastActivityAt = Date.now()
            }

            if (t === "permission.asked" && p.sessionID === sessionID) {
              status.permissions++
              // 应答路由以引擎二进制路由串为准:POST /api/session/:sessionID/permission/:requestID/reply
              // body {reply:"once"|"always"|"reject", message?},成功=204。
              // 应答必须确认成功:引擎收到应答才放行工具,应答丢了=工具永久挂起、整个任务静默停摆
              // (实测踩过:client.call 不为非 2xx 抛错,旧 .catch 兜底是死代码)。
              const pid = String(p.id || p.requestID || "")
              // 外发写(push/gh 写远端/外网 POST)不再无脑放行:有界面就等真人点,没界面就拒绝
              const plan = classifyPermission(p, typeof opts.confirmPermission === "function")
              ;(async () => {
                let reply = plan.reply
                if (reply === "confirm") {
                  // 等用户点头期间要给看门狗「有进展」信号:她在那儿想,不是模型没响应
                  const ans = await withHeartbeat(askUser(opts.confirmPermission, {
                    kind: "permission", id: pid, sessionID, title: "Agent 想执行会写到仓库外的命令",
                    reason: plan.outward.why, detail: plan.outward.evidence, choices: ["once", "always"],
                  }, controller.signal))
                  reply = ans === "always" ? "always" : ans === "once" ? "once" : "reject"
                  status.approvals.push({ id: pid, kind: "permission", what: plan.outward.why, detail: plan.outward.evidence, reply, at: Date.now() })
                  if (reply === "reject") console.warn("[wb-agent] 外发写操作未获确认,已拒绝:" + plan.outward.evidence.slice(0, 160))
                }
                for (let round = 0; round < 3; round++) {
                  try {
                    const body = reply === "reject"
                      ? { reply, message: "工坊规则:往仓库外发布/推送的动作需要用户在页面上逐次确认,已拒绝。请改为在回答里说明需要用户确认,并继续其余工作。" }
                      : { reply }
                    const r = await client.call(`/session/${sessionID}/permission/${pid}/reply`, {
                      method: "POST",
                      body: JSON.stringify(body),
                    })
                    if (r.status >= 200 && r.status < 300) return
                  } catch {}
                  await new Promise((r) => setTimeout(r, 700))
                }
                errors.push(`权限应答失败,工具可能被挂起:${pid}`)
              })()
            }
            if (t === "question.asked" && p.sessionID === sessionID) {
              status.questions++
              // 提问应答同样以引擎路由串为准:POST /api/session/:sessionID/question/:requestID/reply
              // body {answers:[[选项label],…]}(每题一个数组、按题序),成功=204
              // 普通提问:每题自动选第一项(常见就是「按默认继续」);
              // 但问到「要不要推送/发布」这类外发写时:有界面就交给用户点,没界面则挑「不/先不」的选项,
              // 绝不能把「选第一项」当成用户同意往外发。
              const qid = String(p.id || p.requestID || "")
              const qplan = classifyQuestionReply(p, typeof opts.confirmQuestion === "function")
              ;(async () => {
                let answers = qplan.answers
                if (qplan.confirm) {
                  const picked = await withHeartbeat(askUser(opts.confirmQuestion, {
                    kind: "question", id: qid, sessionID, title: "Agent 在问你是否要往仓库外发布",
                    reason: qplan.outward ? qplan.outward.why : "", detail: qplan.outward ? qplan.outward.evidence : "",
                    questions: (p.questions || []).map((q) => ({
                      question: String(q.question || q.header || q.text || "").slice(0, 300),
                      options: ((q.options || q.choices || []).map((o) => (typeof o === "string" ? o : o?.label || o?.name || "")).filter(Boolean)),
                    })),
                  }, controller.signal))
                  status.approvals.push({ id: qid, kind: "question", what: (qplan.outward && qplan.outward.why) || "", detail: (qplan.outward && qplan.outward.evidence) || "", reply: Array.isArray(picked) && picked.length ? "user" : "fallback", at: Date.now() })
                  if (Array.isArray(picked) && picked.length) answers = picked
                }
                for (let round = 0; round < 3; round++) {
                  try {
                    const r = await client.call(`/session/${sessionID}/question/${qid}/reply`, {
                      method: "POST",
                      body: JSON.stringify({ answers }),
                    })
                    if (r.status >= 200 && r.status < 300) return
                  } catch {}
                  await new Promise((r) => setTimeout(r, 700))
                }
                errors.push(`提问应答失败:${qid}`)
              })()
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
      ? `模型服务已 ${Math.round(STALL_MS / 1000)} 秒没有任何响应,任务被自动停止(常见原因:接口卡住或服务不稳定)。可以再发一次,或到 ⚙ 设置里换一个更稳定的服务商`
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
    stats: { permissions: status.permissions, questions: status.questions, approvals: status.approvals.length },
    approvals: status.approvals,
    reply: finalReply,
    thinking,
    usage: turnUsage,
    artifacts: scanArtifacts(opts.dir, startedAt),
    modifiedFiles: scanModifiedFiles(opts.dir, startedAt),
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
      const modifiedFiles = scanModifiedFiles(opts.dir || process.cwd(), startedAt)
      const base = { sessionId: opts.sessionID || `cli_${startedAt}`, idle: true, stats: { permissions: 0, questions: 0 }, artifacts, modifiedFiles }
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

/**
 * ZCode 底座(https://github.com/zai-org/ZCode)
 *
 * 与 opencode 引擎并列的第二种「大脑」:非交互调用 `zcode --prompt … --output-format json`,
 * 一次任务起一个进程,进程退出即一轮结束。返回形状与 driveAgent/driveAgentCli 完全一致,
 * 上层(会话落库、自动压缩、产物扫描、停止按钮)无需区分底座。
 *
 * opts: {text, dir, cmd?:string[], mode?, zcodeMode?, timeoutSec, signal, sessionID}
 */
export function buildZcodeArgs({ text, dir, sessionID, zcodeMode = "yolo" } = {}) {
  const args = [
    "--prompt", String(text ?? ""),
    "--cwd", String(dir || process.cwd()),
    "--mode", String(zcodeMode || "yolo"),
    "--output-format", "json",
    "--no-color",
    // verbose 的日志走 stderr(进度事件持续输出),既是"还活着"的心跳,也不污染 stdout 的结果 JSON
    "--verbose",
  ]
  if (sessionID && /^sess[_-]/i.test(String(sessionID))) args.push("--resume", String(sessionID))
  return args
}

/** 解析 zcode 输出:优先整段 JSON,其次 NDJSON 里最后一条带回复的事件,最后退化为纯文本 */
export function parseZcodeOutput(stdout) {
  const raw = String(stdout == null ? "" : stdout)
  const t = raw.trim()
  if (!t) return { reply: null, sessionId: null, usage: null }
  const pick = (j) => {
    if (!j || typeof j !== "object") return null
    const r = j.response ?? j.result ?? j.text ?? (j.message && j.message.content) ?? null
    return typeof r === "string" && r.trim() ? r : null
  }
  // 会话 id 的字段变体:sessionId / session_id / sessionID / session.id
  const sid = (j) => {
    if (!j || typeof j !== "object") return null
    const v = j.sessionId ?? j.session_id ?? j.sessionID ?? (j.session && (j.session.id ?? j.session.sessionId)) ?? null
    return typeof v === "string" && v ? v : null
  }
  const usageOf = (j) => (j && typeof j === "object" && j.usage && typeof j.usage === "object" ? j.usage : null)
  // 逐条收集:正文取最后一条有内容的;sessionId/usage 任何一条带就算——
  // 真实输出里它们常常和 response 不在同一行(init/result 分属两个事件),只认 response 行会把会话 id 丢掉
  let reply = null, sessionId = null, usage = null
  const absorb = (j) => {
    const r = pick(j)
    if (r) reply = r
    if (!sessionId) { const s = sid(j); if (s) sessionId = s }
    if (!usage) { const u = usageOf(j); if (u) usage = u }
  }
  try {
    absorb(JSON.parse(t))
  } catch {
    for (const line of t.split(/\r?\n/)) {
      const s = line.trim()
      if (!s.startsWith("{")) continue
      try { absorb(JSON.parse(s)) } catch {}
    }
  }
  // 没有任何 JSON 事件带正文时整段当纯文本,但行内仍可能藏着 sessionId/usage
  return { reply: reply ?? t, sessionId, usage }
}

const ZCODE_RETRYABLE = /tpm\/rpm|rate limit|exceeded|upstream became unavailable|queue_full|429|overloaded/i
const zcodeSleep = (ms, signal) => new Promise((res) => {
  if (signal && signal.aborted) return res()
  const t = setTimeout(() => { if (signal) signal.removeEventListener("abort", onAbort); res() }, ms)
  function onAbort() { clearTimeout(t); res() }
  if (signal) signal.addEventListener("abort", onAbort, { once: true })
})

/** 值得再试一次:限流/上游抖动/空回复(不含用户取消与超时) */
function zcodeWorthRetry(r) {
  if (!r || r.cancelled || r.timeout || r.stopped) return false
  if (r.error && /没有任何输出,判定挂死/.test(String(r.error))) return false
  if (r.ok && r.reply) return false
  if (r.error && ZCODE_RETRYABLE.test(String(r.error))) return true
  return Boolean(r.ok && !r.reply)
}

export async function driveZcode(opts = {}) {
  // 会话失效 → 新开;限流/空回复 → 等几秒再来一次。最多两次,别无限重试烧钱
  // opts.noRetry:专家会诊这类"锦上添花"的子调用用——失败就快速返回原因,不叠退避(否则 9 个成员×重试会把限流网关敲死)
  const backoff = opts.noRetry ? [] : [15000, 40000]   // 免费额度的 tpm 限制通常按分钟算,退避太短等于白试
  let res = await runZcodeOnce(opts)
  for (let i = 0; i < backoff.length && zcodeWorthRetry(res); i += 1) {
    if (opts.signal && opts.signal.aborted) break
    const why = res.error && ZCODE_RETRYABLE.test(String(res.error)) ? "限流/上游抖动" : "空回复"
    console.warn(`[wb-agent-lib] ZCode ${why},${Math.round(backoff[i] / 1000)} 秒后重试(${i + 1}/${backoff.length})`)
    await zcodeSleep(backoff[i], opts.signal)
    if (opts.signal && opts.signal.aborted) { res = { ...res, ok: false, stopped: true, error: "任务已停止" }; break }
    res = await runZcodeOnce({ ...opts, sessionID: undefined })
  }
  return res
}

async function runZcodeOnce(opts = {}) {
  const startedAt = Date.now()
  const timeoutSec = opts.timeoutSec ?? 1500
  const cmd = Array.isArray(opts.cmd) && opts.cmd.length ? opts.cmd.map(String) : ["zcode"]
  const resolved = resolveCliBase(cmd[0])
  const spawnPath = resolved ? resolved.exe : cmd[0]
  const args = [...(resolved ? resolved.prefix : []), ...cmd.slice(1),
    ...buildZcodeArgs({ text: opts.text, dir: opts.dir, sessionID: opts.sessionID, zcodeMode: opts.zcodeMode })]
  return await new Promise((resolve) => {
    let settled = false
    let child
    try {
      child = spawn(spawnPath, args, {
        cwd: opts.dir || process.cwd(),
        env: { ...process.env, ...(opts.env || {}), WB_PROJECTS_DIR: opts.dir || "", WB_NEKO_REPO: opts.nekoRepo || process.env.WB_NEKO_REPO || "" },
        stdio: ["ignore", "pipe", "pipe"],
        windowsHide: true,
      })
    } catch (e) {
      resolve({ ok: false, sessionId: opts.sessionID || `zcode_${startedAt}`, idle: true, error: `无法启动 ZCode 底座:${e?.message || e}`, stats: { permissions: 0, questions: 0 }, reply: null, artifacts: [] })
      return
    }
    const CAP = 400000
    let out = "", errText = ""
    child.stdout.on("data", (c) => { if (out.length < CAP) out += c; heartbeat() })
    child.stderr.on("data", (c) => { if (errText.length < CAP) errText += c; heartbeat() })
    const killTree = () => { try { spawn("taskkill", ["/F", "/PID", String(child.pid), "/T"], { stdio: "ignore" }) } catch {} }
    const timer = setTimeout(() => { killTree(); finish({ kind: "timeout" }) }, timeoutSec * 1000)
    // 无输出看门狗:verbose 心跳一断就说明真挂死了(而不是"任务大、干得久"),到点杀树
    const stallSec = Math.max(60, Math.floor(Number(opts.stallTimeoutSec) || 360))
    let stallTimer = setTimeout(stallFire, stallSec * 1000)
    function heartbeat() { clearTimeout(stallTimer); stallTimer = setTimeout(stallFire, stallSec * 1000) }
    function stallFire() { killTree(); finish({ kind: "timeout", stall: true, stallSec }) }
    if (opts.signal) {
      if (opts.signal.aborted) { killTree(); finish({ kind: "stopped" }) }
      else opts.signal.addEventListener("abort", () => { killTree(); finish({ kind: "stopped" }) }, { once: true })
    }
    child.on("error", (e) => finish({ kind: "spawn", message: `无法启动 ZCode 底座:${e?.message || e}` }))
    child.on("close", (code) => finish({ kind: "close", code }))
    function finish(fail) {
      if (settled) return
      settled = true
      clearTimeout(timer)
      clearTimeout(stallTimer)
      const parsed = parseZcodeOutput(out)
      if (!parsed.sessionId) {
        // 诊断:输出里没有可用的会话 id 时留一份原始样本(截断),便于定位字段形态
        console.warn("[wb-agent-lib] zcode 输出未含 sessionId,前 240 字:" + String(out).replace(/\s+/g, " ").slice(0, 240))
      }
      if (parsed.usage && parsed.usage.inputTokens !== undefined && parsed.usage.input === undefined) {
        parsed.usage = {
          input: parsed.usage.inputTokens || 0,
          output: parsed.usage.outputTokens || 0,
          reasoning: parsed.usage.reasoningTokens || 0,
          total: parsed.usage.totalTokens || undefined,
          source: parsed.usage.source || undefined,
        }
      }
      const artifacts = scanArtifacts(opts.dir || process.cwd(), startedAt)
      const modifiedFiles = scanModifiedFiles(opts.dir || process.cwd(), startedAt)
      const base = {
        sessionId: parsed.sessionId || opts.sessionID || `zcode_${startedAt}`,
        idle: true,
        stats: { permissions: 0, questions: 0 },
        artifacts,
        modifiedFiles,
        ...(parsed.usage ? { usage: parsed.usage } : {}),
      }
      if (fail.kind === "spawn") { resolve({ ok: false, ...base, error: fail.message, reply: null }); return }
      if (fail.kind === "stopped") { resolve({ ok: false, ...base, error: "任务已停止", reply: parsed.reply, stopped: true }); return }
      if (fail.kind === "timeout") {
        resolve({
          ok: false, ...base,
          error: fail.stall
            ? `ZCode 连续 ${fail.stallSec}s 没有任何输出,判定挂死已清理(总上限 ${timeoutSec}s;若任务本来就很慢,可在设置里调大「无响应超时」)`
            : `ZCode 底座超时(${timeoutSec}s),已连子进程一起清理`,
          reply: parsed.reply,
        })
        return
      }
      const code = fail.code
      const reply = parsed.reply
      resolve({
        ok: code === 0 && !!reply,
        ...base,
        error: code === 0
          ? (reply ? null : "ZCode 没有返回内容")
          : `ZCode 退出码 ${code}:${(errText.trim() || out.trim() || "(无输出)").slice(0, 1200)}`,
        reply: reply || null,
      })
    }
  })
}

// ---------- 专家团模式(experts/:PR #4 引入的专家包) ----------
/**
 * 把专家包格式化成注入 buildWishPrompt 的提示块。纯函数。
 * @param pack loadExperts() 返回的一个包 {id, dir, manifest, agentIds, problems}
 * @param leadText 主理人 agent 文档里提取的「团队总原则」段(可为空)
 * @param maxChars 整块预算(默认 2200,超出先截纪律段再截整块)
 * @param disabledIds 被用户单独关闭的成员 id 列表(设置页可逐个开关)
 */
export function formatExpertBlock(pack, leadText = "", maxChars = 2200, disabledIds = []) {
  if (!pack || !pack.manifest) return ""
  const m = pack.manifest
  if (pack.problems && pack.problems.length) return ""
  const name = (m.displayName && m.displayName.zh) || m.name || pack.id
  const lead = (m.teamInfo && m.teamInfo.leadAgent) || m.agentName || ""
  const off = new Set(Array.isArray(disabledIds) ? disabledIds.map(String) : [])
  const all = (m.members || []).filter((x) => x && x.id)
  const members = all
    .filter((x) => x.id !== lead && !off.has(x.id))
    .map((x) => {
      const n = (x.displayName && x.displayName.zh) || x.id
      const p = (x.profession && x.profession.zh) || ""
      return p ? `${n}(${p})` : n
    })
  const roster = members.length
    ? members.join("、")
    : all.length
      ? "(专家成员均已单独关闭,仅保留主理人视角)"
      : "(见包内清单)"
  const out = [
    "【专家团模式(已启用)】",
    `本轮任务由「${name}」接管:你扮演这个团的主理人,组织下面这些专家的视角来完成用户的事;`,
    `成员: ${roster}。`,
    "工作方式:结论先行、短句列表、大白话;遇到对应领域的判断(测试/架构/精简/审查/安全/运维)就按对应成员的视角自查一遍再交付;",
    "注意:专家团只改变你的工作方式与严谨度,工坊原有的交付规则(验收标准、提问格式、全中文、verify 全绿)一条都不能少。",
  ]
  const leadSec = String(leadText || "").trim()
  if (leadSec) out.push("", leadSec)
  let s = out.join("\n")
  if (s.length > maxChars) {
    // 超预算:优先保住头部规则,从纪律段尾部往前截
    s = s.slice(0, Math.max(0, maxChars - 40))
    s += "\n…(专家团纪律过长已截断)"
  }
  return s
}


// ---------- 更新包完整性校验(纯逻辑,便于单测;调用方 wb-studio.mjs downloadFrom) ----------
// 发布脚本把整包 SHA256 写进双端 release 说明的「## 校验」段(- SHA256:`HEX`),
// GitHub 资产另有 digest 字段;两边都取一遍,谁有用谁。
export function expectedSha256(src) {
  const assets = (src && src.assets) || []
  for (const a of assets) {
    for (const raw of [a && a.digest, a && a.sha256]) {
      const m = String(raw || "").match(/([0-9a-f]{64})/i)
      if (m) return m[1].toLowerCase()
    }
  }
  const notes = String((src && src.notes) || "")
  const m = notes.match(/SHA256[^0-9a-f]{0,16}([0-9a-f]{64})/i)
  return m ? m[1].toLowerCase() : ""
}

// 测试/离线更新源 WB_UPDATE_FEED 的准入:只允许本机。
// 这个环境变量等于「换个地方下包」,写成公网地址就等于把自动更新的供应链交出去,
// 所以只认 file: 与 127.0.0.1/localhost/[::1](开发者本机自测用)。
export function isLocalUpdateFeed(url) {
  const raw = String(url || "").trim()
  if (!raw) return false
  if (/^file:/i.test(raw)) return true
  // 用 URL 解析而不是正则:正则容易被 http://127.0.0.1:80@evil.com/ 这种「假本机真远端」的写法绕过
  let u = null
  try { u = new URL(raw) } catch { return false }
  if (u.protocol !== "http:" && u.protocol !== "https:") return false
  if (u.username || u.password) return false
  const h = String(u.hostname || "").toLowerCase().replace(/^\[|\]$/g, "")
  if (h === "localhost" || h === "::1" || h === "0:0:0:0:0:0:0:1") return true
  return /^127\./.test(h) // 127/8 整段都是回环
}

// 校验判定:缺校验值一律不许装(宁让人手动安装,也不静默跳过校验)。
// wantSha(issue #7):上层已经决定了「这次以什么为准」(签名清单 > 资产 digest > 正文声明),
// 这里必须用那个值比对;自己再算一遍 expectedSha256(src) 会把清单依据丢掉,
// 变成「明明按清单放行了,却拿正文哈希验收」——清单与正文不一致时就会出岔子。
export function verifyUpdateIntegrity(src, actualSha, wantSha) {
  const want = String(wantSha || "").toLowerCase() || expectedSha256(src)
  const got = String(actualSha || "").toLowerCase()
  if (!want) return { ok: false, want, got, reason: "更新包没有登记校验值(SHA256),为安全起见不自动安装:请到发布页手动下载 setup.exe 双击安装" }
  if (!/^[0-9a-f]{64}$/.test(got)) return { ok: false, want, got, reason: "更新包校验值算不出来(文件可能已被占用或损坏),已取消安装" }
  if (got !== want) return { ok: false, want, got, reason: "更新包校验不通过(SHA256 不一致,可能被供应商/镜像改包),已放弃此通道;期望 " + want.slice(0, 12) + "… 实算 " + got.slice(0, 12) + "…" }
  return { ok: true, want, got, reason: "" }
}

// ---------- 自更新的独立信任根(GitHub issue #12) ----------
// 只把 SHA256 写在 release 正文里,等于「哈希和包走同一条渠道」:渠道被劫持(token 泄露、
// 镜像被抢注)时两者可以一起换;而客户端原先在 GitHub/码云之间「取版本号最高的源」,
// 被劫持的一端只要谎报更高版本就能胜出。这里把信任根挪到随包内置的 ed25519 公钥:
// 发布端用私钥对「版本+资产名+体积+SHA256」清单签名,客户端验签通过才认这份哈希。
//
// 私钥只存在于发布机 %USERPROFILE%\.neko-workshop\(workbench/update_sign.cjs),仓库里没有;
// 私钥丢失 = 签不出新清单,那时自动更新会退到「双源必须互相印证」这条路,再不行就提示手动安装,
// 而不是悄悄装来路不明的包。
export const UPDATE_TRUST = {
  alg: "ed25519",
  // SPKI DER 的 base64;换钥匙时同步改这里并跑 test/update-integrity.test.ts
  pubkey: "MCowBQYDK2VwAyEAkNXfon/rHVzO59Ldq8O2ZH67W+bVu1/OeN2yVZpszzE=",
  manifestAsset: "N.E.K.O.PluginWorkshop-Manifest.json",
}

// 版本号比较:三段子数字,缺位补 0;与 studio 侧共用这一份实现(以前两处各写一遍会漂)
export function cmpVersion(a, b) {
  const pa = String(a || "").replace(/^v/i, "").split(/[.+-]/).map((x) => parseInt(x, 10) || 0)
  const pb = String(b || "").replace(/^v/i, "").split(/[.+-]/).map((x) => parseInt(x, 10) || 0)
  for (let i = 0; i < 3; i++) {
    const d = (pa[i] || 0) - (pb[i] || 0)
    if (d) return d > 0 ? 1 : -1
  }
  return 0
}

/**
 * 验清单签名并核对内容。doc 形如 { v, alg, payload(base64 的原始清单字节), sig(base64) }。
 * 验的是 payload 解码出来的**那段字节**,不做二次序列化(序列化风格一变就验不过)。
 *
 * expect(issue #3):必须核对清单说的是不是「这次真要装的那一版」——
 * 一张签名完全合法的**旧版清单**如果也被接受,就等于允许拿旧签名骗回滚。
 * @param {{version?:string, asset?:string, size?:number}} [expect] 本次更新目标的版本/资产名/体积
 * @param {string} [pubkeyB64] 覆盖公钥(仅测试用:示范素材用独立测试密钥签,不碰生产私钥)
 * @returns {{ok:boolean, fatal:boolean, manifest:?object, reason:string}}
 *   fatal=true 表示「签名有问题或内容与目标不符」,调用方必须直接拒装,
 *   不能退化成「没有清单」再靠双渠道互证放行(issue #3 指出的正是这条降级路径)。
 */
export function verifyUpdateManifest(doc, expect, pubkeyB64) {
  const bad = (reason, fatal) => ({ ok: false, fatal: !!fatal, manifest: null, reason })
  if (!doc || typeof doc !== "object") return bad("清单不是一个对象", true)
  if (doc.alg !== UPDATE_TRUST.alg) return bad("签名算法不认识:" + doc.alg, true)
  if (!doc.payload || !doc.sig) return bad("清单缺 payload 或 sig", true)
  let payload
  let sig
  try {
    payload = Buffer.from(String(doc.payload), "base64")
    sig = Buffer.from(String(doc.sig), "base64")
  } catch {
    return bad("清单的 base64 解不开", true)
  }
  let m
  try { m = JSON.parse(payload.toString("utf8")) } catch { return bad("清单正文不是 JSON", true) }
  let ok = false
  try {
    const { createPublicKey, verify } = require("node:crypto")
    const pub = createPublicKey({
      key: Buffer.from(String(pubkeyB64 || UPDATE_TRUST.pubkey), "base64"), format: "der", type: "spki",
    })
    ok = verify(null, payload, pub, sig)
  } catch (e) {
    return bad("验签不可用:" + (e && e.message ? e.message : e), true)
  }
  if (!ok) return bad("清单签名不匹配(公钥对不上,或清单被改过)", true)
  const sha = String(m.sha256 || "").toLowerCase()
  if (!/^[0-9a-f]{64}$/.test(sha)) return bad("清单里没有合法的 SHA256", true)
  if (!(Number(m.size) > 0)) return bad("清单体积不合法", true)
  // 内容必须对应本次目标(旧清单/别的资产一概不算数)
  const e = expect || {}
  const norm = (v) => String(v || "").replace(/^v/i, "")
  if (e.version && norm(m.version) !== norm(e.version)) {
    return bad("清单版本(v" + norm(m.version) + ")与要装的版本(v" + norm(e.version) + ")不一致——可能是旧清单被拿来骗回滚", true)
  }
  if (e.asset && String(m.asset || "") !== String(e.asset)) {
    return bad("清单里的资产名(" + m.asset + ")与本次要下载的资产(" + e.asset + ")对不上", true)
  }
  if (e.size && Number(m.size) !== Number(e.size)) {
    return bad("清单体积(" + m.size + ")与渠道声明(" + e.size + ")对不上", true)
  }
  return { ok: true, fatal: false, manifest: { ...m, sha256: sha }, reason: "" }
}

/**
 * 选定要更新的版本,并判断这个结论有多少独立性(issue #12 第 2 点、#12/#3 组合)。
 * @param sources    渠道列表 [{source,version,notes,assets}]
 * @param manifest   清单结论:{sha, fatal, reason}(没有清单就传 {});也兼容直接传 sha 字符串
 * 规则(按信任强度从高到低):
 *   - 清单签名有问题或与目标不符(fatal)→ **直接拒装**,不退化成「靠双渠道互证」放行(issue #3);
 *   - 各渠道对同一版本声明的哈希互相矛盾 → 拒装;
 *   - 该版本被 ≥2 个渠道同时报出 → 渠道互相印证,可以装(trustRoot="agreement");
 *   - 只有 1 个渠道报这个版本 → 必须有验签通过的清单(trustRoot="manifest"),否则拒装。
 */
export function planUpdateTarget(sources, manifest) {
  const mf = typeof manifest === "string" ? { sha: manifest } : (manifest || {})
  if (mf.fatal) {
    return { ok: false, version: String(mf.version || ""), trustRoot: "manifest", reason: "更新清单不可信:" + (mf.reason || "验签未通过") + "(不因「多渠道一致」而放行)" }
  }
  const signed = String(mf.sha || "").toLowerCase()
  const list = (sources || []).filter((s) => s && s.version)
  if (!list.length) return { ok: false, version: "", trustRoot: "", reason: "一个版本信息都没拿到" }
  const best = list.map((s) => s.version).sort((a, b) => cmpVersion(b, a))[0]
  const agree = list.filter((s) => cmpVersion(s.version, best) === 0)
  const declared = [...new Set(agree.map((s) => expectedSha256(s)).filter(Boolean).map((x) => x.toLowerCase()))]
  if (signed && declared.length && declared[0] !== signed) {
    return { ok: false, version: best, trustRoot: "manifest", reason: "签名清单与渠道正文声明的 SHA256 不一致(" + declared[0].slice(0, 10) + "… vs " + signed.slice(0, 10) + "…),拒装" }
  }
  if (declared.length > 1) {
    return { ok: false, version: best, trustRoot: "", reason: "多个渠道对 v" + best + " 声明的 SHA256 不一致(" + declared.map((x) => x.slice(0, 10) + "…").join(" / ") + "),拒装并提示手动下载" }
  }
  if (agree.length < 2 && !signed) {
    return { ok: false, version: best, trustRoot: "", reason: "只有 " + agree.map((s) => s.source).join("/") + " 一个渠道报 v" + best + ",又没有可用的签名清单印证——单渠道被劫持就能推着升级,所以不自动装" }
  }
  return {
    ok: true,
    version: best,
    channels: agree.map((s) => s.source),
    sha: signed || declared[0] || "",
    trustRoot: signed ? "manifest" : "agreement",
    reason: "",
  }
}

// ---------- 外发写操作门禁(GitHub issue #9) ----------
// 无人值守跑到「推送到远端 / 建 issue / 开 PR / 往外部接口 POST」这一步时,不能由程序替用户点头:
// 这类动作把内容和仓库权限交到外面,事后撤不回(公开出去的 issue 删了也有缓存/邮件)。
// 工坊的默认姿态:命中特征就把决定权交回界面等真人点;拿不到真人应答一律按拒绝处理。
// 只读命令(git status/log/diff、gh issue view/list、拉取)不在此列,照常放行。
// 只读动词:这些子命令不改仓库外内容,照常放行(gh 的 list/view/diff/status 与 git 的本地操作)
const GH_READ_VERBS = ["list", "view", "status", "diff", "checkout", "watch", "log", "search", "get", "counts"]
const GH_WRITE_SCOPES = ["issue", "pr", "release", "repo", "workflow", "run", "actions", "gist"]
// git/gh 的全局选项(带值或不带值),必须跳过才能看到真正的子命令——否则 `git -C repo push`、
// `gh --repo x issue create` 这种写法会绕过判定(issue #10 指名)
const GIT_GLOBAL_VALUED = ["-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--super-prefix"]
const GIT_GLOBAL_BARE = ["--no-pager", "--literal-pathspecs", "--no-optional-locks", "--bare", "--paginate", "-m", "-v", "-q"]
const GH_GLOBAL_VALUED = ["-R", "--repo", "--hostname", "--json", "--jq", "--template"]
const HTTP_WRITE_RE = /(-X\s*['"]?(POST|PUT|PATCH|DELETE)|--data\b|--data-raw\b|--data-binary\b|--json\b|--form\b|-F\s|--upload-file|-T\s|-Method\s+(Post|Put|Patch|Delete))/i
// 注意:正则里的斜杠必须转义,否则字面量被 // 提前截断(踩过)
const LOCAL_HOST_RE = /(?:127\.0\.0\.1|localhost|\[::1\]|0:0:0:0:0:0:0:1|:\/\/(?:10\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.))/i
const REMOTE_URL_RE = /https?:\/\/[^/"'\s]+/i
const OUTWARD_TEXT_VERB = /(推送|推上|推给|推到|发到|发布到|上传到|传到|同步到|提交\s*(一个|这个)?\s*(issue|工单)|新建\s*(一个|这个)?\s*(issue|工单|PR)|创建\s*(一个|这个)?\s*(issue|工单|PR|合并请求)|开\s*(一个|这个)?\s*(PR|issue|工单|合并请求)|合并\s*(PR|合并请求)|提\s*(一个)?\s*issue|publish)/i
const OUTWARD_TEXT_TARGET = /(github|gitee|码云|远端|远程仓库|仓库|线上|公开|issue|工单|\bPR\b|合并请求|release|registry|npm|镜像仓库|插件市场|marketplace)/i
// 解释器内联代码走网络:python -c "requests.post(...)" / node -e "fetch(...)" 这类
// 用命令正则永远列不全,所以这里按「解释器 + 内联代码开关 + 远端 URL/写方法」三件同时命中才算
const INLINE_INTERP_RE = /(?:\bpython[0-9.]*\b|\bpy\.exe\b|\bnode\b|\bbun\b|\bdeno\b|\bpwsh\b|\bpowershell\b|\bphp\b|\bperl\b|\bruby\b|\bbash\b|\bsh\b)(?=[\s\S]{0,40}(?:\s-c\s|\s-e\s|--command\s|--eval\s|--input-string\s|-EncodedCommand\s))/i
const REMOTE_CALL_CODE_RE = /(requests\.(post|put|patch|delete)|httpx\.(post|put|patch|delete)|urllib\.request|urlopen|http\.client|aiohttp|fetch\s*\(|axios|invoke-restmethod|invoke-webrequest|newman|webhook)/i
// 其它「把东西送到机器外面」的通道(issue #1 补充):对象存储、镜像仓库、包索引、远端复制
const OTHER_OUTWARD_RE = [
  [/\b(twine|cargo|gem)\s+(upload|publish|push)\b/i, "发布包到远端索引"],
  [/\b(mvn|gradle)\s+(deploy|publish)\b/i, "发布构件到远端仓库"],
  [/\b(docker|podman|nerdctl|buildah)\s+push\b/i, "推送镜像到远端仓库"],
  [/\bscp\b|\bsftp\b|\bftp\b\s+-/i, "往远端主机传文件"],
  // rsync 的远端形态:host:/path、user@host:/path、::模块。
  // 冒号前要求至少两个字符,免得把 Windows 盘符(C:/)当远端 —— 本地复制不该弹窗
  [/\brsync\b[^\n]*?(?:::|[A-Za-z][\w.-]+:\/|[A-Za-z][\w.-]*@[\w.-]+:\/)/i, "rsync 到远端主机"],
  [/\b(aws|aliyun|ossutil|gsutil|az|b2)\b[^\n]{0,60}\b(cp|sync|upload|deploy)\b/i, "上传到对象存储"],
  [/\bhelm\s+push\b|\bargo\s+push\b/i, "推送到远端 chart/仓库"],
]
// 别名/配置类「绕道推送」:git -c alias.x=push、git config alias.push 'push --mirror'
const GIT_ALIAS_RE = /\bgit\b[^\n]{0,80}\balias[.=][^\n]{0,40}\bpush\b/i
// 复合命令分段符:引擎的 glob 不跨空格,`git add . && git push` 要拆开逐段判定(issue #1)
const SEGMENT_SPLIT = /(?:&&|\|\||[;\n|])/

// 跳过命令行前导的全局选项,返回 { 子命令, 之后剩余部分 }
function subcommandAfter(s, bin, valued, bare) {
  const head = new RegExp("\\b" + bin + "\\b", "i")
  if (!head.test(s)) return null
  // 从 bin 出现的位置往后逐 token 扫
  const idx = s.search(head)
  const rest = s.slice(idx + bin.length)
  const toks = rest.match(/^\s*((?:(?:-\S+|"[^"]*"|'[^']*'|[^\s"']+)\s*)*)/)
  const list = (toks ? toks[1] : "").trim().split(/\s+/).filter(Boolean)
  for (let i = 0; i < list.length; i++) {
    const t = list[i]
    if (/^--[^=]+=/.test(t)) {
      const k = t.split("=")[0]
      if (valued.includes(k)) continue
      if (bare.includes(k) || /^-/.test(k)) continue
      return { sub: t, rest: list.slice(i + 1).join(" ") } // --git-dir=x push 形式:等号后不是子命令
    }
    if (/^-/.test(t)) {
      if (valued.includes(t)) { i++; continue }   // -C <值>
      if (bare.includes(t)) continue
      // 未知选项:后面还可能是子命令,继续扫但不能越过它猜
      continue
    }
    return { sub: t.replace(/^["']|["']$/g, ""), rest: list.slice(i + 1).join(" ") }
  }
  return { sub: "", rest: "" }
}

// 命令文本 → 外发原因(空串=不是外发)
//
// 两层判定,缺一不可:
//   1) **整串**:有些形态必须看到全句才成立,比如 `python -c "import requests;requests.post(url)"`
//      —— 拆段之后解释器与网络调用被切散,反而会漏判(踩过);
//   2) **逐段**:复合命令 `git add . && git push` 里,推送不是句首,
//      引擎的通配又不跨空格,glob 拦不住,只能靠按 && / || / ; / | / 换行 拆开逐段判(issue #1)。
function outwardEvidence(text) {
  const whole = String(text || "")
  if (!whole) return ""
  const byWhole = outwardEvidenceOne(whole)
  if (byWhole) return byWhole
  for (const seg of whole.split(SEGMENT_SPLIT)) {
    const why = outwardEvidenceOne(String(seg || "").trim())
    if (why) return why
  }
  return ""
}

function outwardEvidenceOne(s) {
  if (!s) return ""
  const g = subcommandAfter(s, "git", GIT_GLOBAL_VALUED, GIT_GLOBAL_BARE)
  if (g && g.sub) {
    if (g.sub === "push") return "git push(推送到远端仓库)"
    if (g.sub === "send-email") return "git send-email(往外发邮件)"
  }
  const h = subcommandAfter(s, "gh", GH_GLOBAL_VALUED, [])
  if (h && h.sub) {
    const scope = String(h.sub).toLowerCase()
    const verb = String(h.rest || "").split(/\s+/)[0] || ""
    if (scope === "api") return "gh api(可直接调远端写接口)"
    if (GH_WRITE_SCOPES.includes(scope) && !GH_READ_VERBS.includes(String(verb).toLowerCase())) {
      return "gh " + scope + " " + verb + "(改远端仓库内容)"
    }
  }
  // 别名/配置绕道:git -c alias.x=push、git config alias.push 'push --mirror' 之后跑 git x
  if (GIT_ALIAS_RE.test(s)) return "用 git 别名/配置绕道推送"
  if (/\bhub\s+sync\b/i.test(s)) return "同步到远端分支"
  for (const [re, why] of OTHER_OUTWARD_RE) if (re.test(s)) return why
  if (/\b(npm|pnpm|yarn|bun)\s+publish\b/i.test(s)) return "发布包到 registry"
  if (/\b(curl|wget|Invoke-RestMethod|Invoke-WebRequest|iwr)\b/i.test(s) && HTTP_WRITE_RE.test(s) && !LOCAL_HOST_RE.test(s)) {
    return "往外部接口写数据"
  }
  if (INLINE_INTERP_RE.test(s) && (REMOTE_CALL_CODE_RE.test(s) || (HTTP_WRITE_RE.test(s) && !LOCAL_HOST_RE.test(s)))) {
    // 解释器内联代码 + 远端调用:典型 `python -c "import requests;requests.post(...)"`
    if (REMOTE_URL_RE.test(s) && !LOCAL_HOST_RE.test(s)) return "用内联脚本往外部接口写数据"
  }
  if (OUTWARD_TEXT_VERB.test(s) && OUTWARD_TEXT_TARGET.test(s)) return "文案里明确要往仓库外发布/推送"
  return ""
}

// ---------- 引擎权限配置必须由同一张规则表生成(issue #10) ----------
// 引擎把 `bash: "allow"` 展开成 `{"*":"allow"}`(编译版里能看到),于是任何 bash 命令
// 都不会发出 permission.asked,工坊侧的门禁根本不会被触发。
// 这里产出「按模式细分」的规则:外发写形态先设 ask,再把只读形态设回 allow;
// 引擎取**最后命中**的规则(编译版里是 findLast),所以规则表只能有一个方向的例外:
// 「*」放行 + 外发形态询问。以前还额外写了一批只读回放行(`"*git add*"` 之类),
// 结果 `bash -c "git add . && git push"` 这种复合命令被后插入的回放行整体放行 ——
// 门禁又能被绕过(issue #1)。回放行全部删掉,只读命令交给「*」放行即可。
// 注意:glob 只能匹配字符串形态,列不尽所有写法;`python deploy.py` 这类「脚本内部再外发」
// 由 W1 规则与运行时判定兜住,不假装正则能覆盖一切(见下方 resolveBashEffect 的说明)。
const OUTWARD_ASK_GLOBS = [
  // git:推送与「别名间接推送」。前缀形态与「包含」形态都列:
  // 不同版本的匹配器有的是「前缀 glob(隐含结尾 *)」有的是「整串匹配」,只写一种会因语义不同而漏(issue #1)
  "git push*", "*git push*",
  "git -C * push*", "*git -C * push*", "git -c * push*", "*git -c * push*",
  "git --git-dir=* push*", "*git --git-dir=* push*", "git --work-tree=* push*", "*git --work-tree=* push*",
  "git --no-pager push*", "*git --no-pager push*",
  "git send-email*", "*git send-email*", "*git config*alias*", "git config*alias*", "*git -c alias.*",
  "*--mirror*", "*--atomic*",
  // gh:整个都是远端操作,读子命令也一样问(工坊日常极少用 gh 读,问了不心疼)
  "gh *", "*gh *", "*hub *",
  // 发布类(前缀形态与包含形态都列,理由同 git)
  "npm publish*", "*npm publish*", "pnpm publish*", "*pnpm publish*", "yarn publish*", "*yarn publish*",
  "bun publish*", "*bun publish*", "twine upload*", "*twine upload*",
  "cargo publish*", "*cargo publish*", "gem push*", "*gem push*", "mvn deploy*", "*mvn deploy*",
  "gradle publish*", "*gradle publish*", "docker push*", "*docker push*", "podman push*", "*podman push*",
  // 直接对外发数据的命令行工具
  "*curl *--data*", "*curl *-d *", "*curl *--json*", "*curl *-f *", "*curl *--upload-file*", "*curl *-T *",
  "*wget *--post-data*", "*wget *--post-file*", "*wget *--method=*", "*wget *--content-post*",
  "*Invoke-RestMethod*", "*Invoke-WebRequest*", "*iwr *", "*http post*", "*http PUT*",
  "*scp *", "*sftp *", "*rsync *:*", "*aws s3 cp*", "*aws s3 sync*", "*gsutil cp*", "*az storage blob upload*",
  "*ossutil cp*", "*gh-api*",
  // 解释器内联代码(脚本里发 HTTP)
  "*python* -c *", "*python* -e *", "*node -e *", "*deno eval *", "*pwsh -Command *", "*powershell -Command *",
  "*bash -c *curl*", "*sh -c *curl*", "*bash -c *git push*", "*sh -c *git push*",
]

/**
 * glob 转正则:支持 `*`,并按前缀 glob 的语义**隐含结尾 `*`**。
 * 这一条很关键:不加隐含结尾的话 `git push*` 匹配不上 `git push origin main`(后面还有内容),
 * 而 `bash -c "git add . && git push"` 也可能因为形态算不中而整条放行(issue #1 的洞)。
 * 引擎用的是 git 风格 wildmatch(默认隐含结尾通配),这里保持同一口径。
 */
function globToRe(g) {
  let p = String(g)
  if (!p.endsWith("*")) p += "*"
  return new RegExp("^" + p.split("*").map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("[\\s\\S]*") + "$", "i")
}

/**
 * 按引擎的「最后命中胜出」语义算出某条命令的效果,用来在单测里钉住规则表的行为。
 * 引擎自己匹配时可能有更细的实现,这里复现的是它的取舍顺序(后定义的规则赢),
 * 因此任何一条会让外发命令被判成 allow 的规则改动都会在测试里红。
 * @returns "allow" | "ask"
 */
export function resolveBashEffect(cmd, extraRules) {
  const table = Object.assign({ "*": "allow" }, enginePermissionConfig().bash, extraRules || {})
  const s = String(cmd || "")
  let effect = "allow"
  let matched = false
  for (const [g, act] of Object.entries(table)) {
    if (g === "*") continue
    if (globToRe(g).test(s)) { matched = true; effect = act }
  }
  return matched ? effect : String(table["*"] || "allow")
}

/**
 * 规则表去重:通配匹配是大小写不敏感的,所以 `git -C * push*` 与 `git -c * push*`
 * 在语义上是同一个键 —— 写两遍会在 JSON 里留下「只有大小写不同」的重复键,
 * 大小写不敏感的解析器(PowerShell ConvertFrom-Json、部分配置校验)会直接报错。
 * 引擎按什么规则匹配我们不赌,但**产出的配置必须是任何解析器都能吃的干净 JSON**。
 */
function dedupeGlobs(list) {
  const seen = new Set()
  const out = []
  for (const g of list) {
    const k = String(g).toLowerCase()
    if (seen.has(k)) continue
    seen.add(k)
    out.push(g)
  }
  return out
}

/**
 * 给引擎 opencode.json 用的 permission 块。
 *
 * ⚠ 为什么通配必须是 ask(实测结论,不是推断):
 *   真引擎跑 `git add . && git push origin main` 时,
 *   - `git push*`(前缀形态)命中不了 —— 命令不是以它开头;
 *   - `*git push*`(包含形态)也命中不了 —— 引擎的通配**不跨空格**,`*` 只能匹配一个词;
 *   于是整条命令落到 `{"*":"allow"}` 被直接执行,复合命令成了门禁的洞(issue #1)。
 *   glob 列不全写法这个事实没法靠堆模式补救,所以默认改成「都要问」。
 *
 * 「都要问」不会把人淹死:工坊在 driveAgent 里对**非外发写**的询问自动答 once(毫秒级,不打扰用户),
 * 只有命中外发写判定(按 `&&`/`;`/`|` 逐段解析,不依赖 glob)才进确认队列。
 * 也就是说:引擎负责「一律先问」,工坊负责「只把真外发的拦下来给人看」。
 */
export function enginePermissionConfig() {
  const bash = { "*": "ask" }
  // 外发形态显式再标一遍:万一将来有人把通配改回 allow,这些规则还在(纵深防御,不靠单点)
  for (const g of dedupeGlobs(OUTWARD_ASK_GLOBS)) bash[g] = "ask"
  return { edit: "allow", webfetch: "allow", bash }
}

// 从权限/提问事件里挖出命令文本:字段名各版本不一样,全量序列化再扫最稳
export function detectOutwardWrite(evt) {
  const p = evt || {}
  const named = [p.patterns, p.pattern, p.command, p.cmd, p.text, p.input?.command, p.metadata?.command, p.args, p.title, p.description]
  for (const v of named) {
    const t = Array.isArray(v) ? v.join(" ") : String((v && typeof v === "object" && v.text) || v || "")
    if (!t) continue
    const why = outwardEvidence(t)
    if (why) return { outward: true, why, evidence: t.slice(0, 400) }
  }
  const all = (() => { try { return JSON.stringify(p) } catch { return "" } })()
  const why = outwardEvidence(all)
  return why ? { outward: true, why, evidence: (p.patterns ? String(p.patterns) : all).slice(0, 400) } : { outward: false, why: "", evidence: "" }
}

// 权限应答判定:纯函数,方便单测。
// reply="confirm" 表示「要真人点头」,由调用方去问界面;界面不可用时(纯 CLI/无人值守)直接 reject。
export function classifyPermission(p, hasApprover) {  const ow = detectOutwardWrite(p)
  if (!ow.outward) {
    // 读写工作区外目录(external_directory)是无人值守建插件的常态,持久放行避免反复弹权限
    return { reply: p && p.action === "external_directory" ? "always" : "once", outward: null }
  }
  return { reply: hasApprover ? "confirm" : "reject", outward: ow }
}

// 提问应答判定:普通提问照旧选第一项;命中外发意图的提问必须真人选,
// 界面不在时选「看起来是拒绝/暂缓」的选项(而不是默认第一个,第一个通常是「好」)。
export function classifyQuestionReply(p, hasApprover) {
  const qs = Array.isArray(p && p.questions) ? p.questions : []
  const labels = qs.map((q) => {
    const opts = (q && (q.options || q.choices)) || []
    return opts.map((o) => (typeof o === "string" ? o : (o && (o.label || o.name || o.id)) || "")).filter(Boolean)
  })
  const DECLINE_RE = /(不|先别|先不|暂不|别|取消|算了|再等|拒绝|否|no |don'?t|cancel|decline|skip)/i
  // 「保守答案」:优先挑明显带「不/先不」的选项,找不到就选最后一项(第一项通常是「好」,不能当同意)
  const declining = labels.map((opts) => {
    if (!opts.length) return [""]
    const d = opts.find((o) => DECLINE_RE.test(o))
    return [d || opts[opts.length - 1]]
  })
  // 默认应答:每题选第一项(保持既有行为,普通澄清题不因此变慢)
  const defaults = labels.map((opts) => [opts[0] || ""])
  const head = qs.map((q) => String((q && (q.question || q.header || q.text)) || "")).join(" ")
  const ow = detectOutwardWrite({ text: head, patterns: [head, labels.flat().join(" ")] })
  if (!ow.outward) return { confirm: false, answers: defaults, outward: null }
  // 外发意图:有界面 → 交给真人点(answers 是兜底,超时/停止/没答时用保守答案)
  if (hasApprover) return { confirm: true, answers: declining, outward: ow }
  // 没界面(纯 CLI/无人值守)→ 直接按保守答案回,不替用户答应
  return { confirm: false, answers: declining, outward: ow }
}
