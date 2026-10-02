#!/usr/bin/env bun
/**
 * wb-studio - N.E.K.O. 附属工作台·一键版本地服务
 *
 * 小白体验:双击 start.cmd → 自动拉起 opencode 引擎 → 浏览器打开对话框 →
 * 一句话 → Agent 自动生成插件 → 网页显示结果与 .neko-plugin 下载链接。
 *
 * 端点:
 *   GET  /                 对话网页
 *   GET  /api/health       {server, model, workspace, neko}
 *   POST /api/task         {text} → 同步执行,返回 driveAgent 结果(JSON)
 *   GET  /api/download?f=  下载工作区内 .neko-plugin
 *
 * 配置:同目录 wb-studio.config.json(端口/工作区/模型/引擎启动命令)。
 */

import { createServer } from "node:http"
import { readFileSync, existsSync, readdirSync, statSync, createReadStream, appendFileSync } from "node:fs"
import { join, dirname, basename, normalize, resolve, relative, isAbsolute } from "node:path"
import { spawn } from "node:child_process"
import { fileURLToPath } from "node:url"
import { homedir } from "node:os"
import { driveAgent, driveAgentCli } from "./wb-agent-lib.mjs"

const __dirname = dirname(fileURLToPath(import.meta.url))

// 控制台输出同步落盘到 runtime\studio.log:引擎静默崩溃/任务异常时,事后有线索可查
try {
  const studioLogPath = join(__dirname, "runtime", "studio.log")
  const fmtArgs = (a) =>
    a.map((x) => {
      if (typeof x === "string") return x
      try { return JSON.stringify(x) } catch { return String(x) }
    }).join(" ")
  const wrapLog = (fn, tag) => (...a) => {
    try { appendFileSync(studioLogPath, `[${new Date().toISOString()}]${tag} ${fmtArgs(a)}\n`) } catch {}
    fn(...a)
  }
  console.log = wrapLog(console.log.bind(console), "")
  console.warn = wrapLog(console.warn.bind(console), " WARN")
  console.error = wrapLog(console.error.bind(console), " ERR")
} catch {}

// ---------- 配置 ----------
// 便携版:配置里以 "./" 或 ".\\" 开头的路径相对包根(即本文件所在目录)解析;
// 开发版:仍支持绝对路径。
const CONFIG_PATH = join(__dirname, "wb-studio.config.json")
const defaultConfig = {
  port: 5099,
  server: "http://127.0.0.1:4096",
  workspace: "./workspace",
  model: "",
  // 引擎模式:"exe"=编译版 opencode-cli.exe(v2 API,Basic 认证,推荐便携包用);"bun"=源码 dev serve(v1 API)
  //            "cli"=任意 Agent CLI 底座(engineCmd/enginePreset,无 opencode 依赖,模型凭据由底座自理)
  engineMode: "exe",
  engineExe: "./_engine/opencode-cli.exe",
  // cli 模式命令模板:数组里含 {text} 的元素替换为整段任务文本;优先级高于 enginePreset
  engineCmd: [],
  // cli 模式预设:claude|codex|omp|opencode(等价 engineCmd,见 CLI_ENGINE_PRESETS)
  enginePreset: "",
  opencodeRepo: join(homedir(), "dev", "opencode"),
  bunPath: "./_bin/bun.exe",
  // 便携 Python(空白即用系统 python);N.E.K.O CLI 依赖 pydantic/psutil,由 _site 提供见 start.cmd PYTHONPATH
  pythonPath: "./_py/python.exe",
  nekoRepo: "",
  enginePort: 4096,
  timeoutSec: 1500,
  stallTimeoutSec: 360, // 无进展看门狗:任务连续 N 秒没有任何引擎事件就自动中止(慢中转+长思考可几分钟无事件,给足 6 分钟)
  strictVerify: false, // 严格校验:warning 一律视为失败(wb.cmd 自动加 --strict,须修到零警告)
  contextLimit: 128000, // 上下文容量(tokens):模型上下文窗口大小,用于底部容量显示
  openBrowser: true,
  openMode: "app", // "app"=内置窗口(Edge/Chrome --app,关窗即退出);"browser"=系统浏览器(不随窗口退出)
  appWindow: "1200x860",
}
function resolvePath(p) {
  if (!p) return p
  if (/^\.\.[\/\\]|^\.[\/\\]/.test(p)) return join(__dirname, p)
  return p
}
let config = defaultConfig
try {
  config = { ...defaultConfig, ...JSON.parse(readFileSync(CONFIG_PATH, "utf8")) }
} catch (e) {
  // 配置文件损坏(常见:BOM/手改语法错)时不再静默用默认值,提示出来以免"改了端口/路径却不生效"
  console.error(`[wb-studio] ⚠ 配置文件解析失败,已用默认配置: ${e.message}`)
}
for (const k of ["workspace", "engineExe", "bunPath", "pythonPath", "opencodeRepo", "nekoRepo"]) {
  config[k] = resolvePath(config[k])
}

// ---------- CLI 底座(engineMode:"cli")----------
// 任意本地 Agent CLI 都能当工坊的大脑:非交互 print 模式、cwd=工作区、凭据自理。
const CLI_ENGINE_PRESETS = {
  claude: ["claude", "-p", "{text}"],
  codex: ["codex", "exec", "{text}"],
  omp: ["omp", "-p", "{text}"],
  opencode: ["opencode", "run", "{text}"],
}
function cliEngineCmd() {
  if (config.engineMode !== "cli") return null
  if (Array.isArray(config.engineCmd) && config.engineCmd.length) return config.engineCmd.map(String)
  const preset = CLI_ENGINE_PRESETS[String(config.enginePreset || "").toLowerCase()]
  return preset ? [...preset] : null
}
function cliEngineReady() {
  const cmd = cliEngineCmd()
  return Boolean(cmd && cmd.some((a) => a.includes("{text}") || a.includes("{textFile}") || a.includes("{stdin}")))
}

// ---------- N.E.K.O 仓库自动探测 ----------
function detectNekoRepo() {
  const candidates = [
    config.nekoRepo,
    join(config.workspace, "..", "N.E.K.O"),
    join(homedir(), "dev", "N.E.K.O"),
    "C:\\N.E.K.O",
    "D:\\N.E.K.O",
    join(process.env.LOCALAPPDATA || "", "Programs", "N.E.K.O"),
    join(process.env.LOCALAPPDATA || "", "N.E.K.O"),
  ].filter(Boolean)
  for (const c of candidates) {
    if (existsSync(join(c, "plugin", "neko_plugin_cli", "cli.py"))) return c
  }
  return null
}
config.nekoRepo = detectNekoRepo()
if (!config.nekoRepo) {
  console.error("[wb-studio] ⚠ 未找到 N.E.K.O(插件 check/build 需要它)。")
  console.error("[wb-studio]   请把本工坊文件夹放到 N.E.K.O 旁边,或在 wb-studio.config.json 的 nekoRepo 填 N.E.K.O 安装路径")
} else {
  console.log(`[wb-studio] N.E.K.O: ${config.nekoRepo}`)
}

// ---------- 模型服务配置(引导页读写) ----------
// 引擎读 runtime\config\opencode\opencode.json;没有有效密钥时展示引导页
const ENGINE_CONFIG_DIR = join(__dirname, "runtime", "config", "opencode")
const ENGINE_CONFIG_FILE = join(ENGINE_CONFIG_DIR, "opencode.json")
const PLACEHOLDER = /^(YOUR_API_KEY|\s*|x+)$/i

function currentProvider() {
  try {
    const cfg = JSON.parse(readFileSync(ENGINE_CONFIG_FILE, "utf8"))
    const p = cfg.provider?.custom
    const key = p?.options?.apiKey || ""
    return { baseURL: p?.options?.baseURL || "", apiKey: key, model: (cfg.model || "").replace(/^custom\//, ""), protocol: p?.protocol || "openai" }
  } catch {}
  // 注意:不回退环境变量 —— 引导页以引擎配置文件为准,
  // 否则开发机(有 SENSENOVA_API_KEY)永远跳过引导,而小白机器(没有)反而能走到引导,逻辑反了。
  return null
}

// ---------- 多协议支持 ----------
// 引擎(AI SDK)原生支持 20 种协议包,自定义 provider 用 npm 字段指定协议;
// 这里只暴露最常见的几种,其余全部走 OpenAI 兼容(绝大多数中转/聚合站都是)
const PROTOCOLS = {
  openai: { npm: "@ai-sdk/openai-compatible", label: "OpenAI 兼容", needsBaseURL: true, defaultBaseURL: "" },
  anthropic: { npm: "@ai-sdk/anthropic", label: "Anthropic(Claude)格式", needsBaseURL: true, defaultBaseURL: "https://api.anthropic.com" },
  google: { npm: "@ai-sdk/google", label: "Google Gemini 格式", needsBaseURL: false, defaultBaseURL: "" },
  openai_official: { npm: "@ai-sdk/openai", label: "OpenAI 官方", needsBaseURL: false, defaultBaseURL: "" },
  openrouter: { npm: "@openrouter/ai-sdk-provider", label: "OpenRouter", needsBaseURL: false, defaultBaseURL: "" },
  groq: { npm: "@ai-sdk/groq", label: "Groq", needsBaseURL: false, defaultBaseURL: "" },
  mistral: { npm: "@ai-sdk/mistral", label: "Mistral", needsBaseURL: false, defaultBaseURL: "" },
  xai: { npm: "@ai-sdk/xai", label: "xAI(Grok)", needsBaseURL: false, defaultBaseURL: "" },
}
function protocolInfo(id) {
  return PROTOCOLS[id] || PROTOCOLS.openai
}

function writeProvider({ baseURL, apiKey, modelID, protocol }) {
  const fs = require("node:fs")
  const proto = protocolInfo(protocol)
  fs.mkdirSync(ENGINE_CONFIG_DIR, { recursive: true })
  const options = { apiKey }
  // 需要地址的协议才写 baseURL(留空则由协议 SDK 走官方默认端点);Anthropic 官方 SDK 的自定义中转必须去掉 /v1
  if (proto.needsBaseURL && baseURL) options.baseURL = baseURL
  const cfg = {
    $schema: "https://opencode.ai/config.json",
    model: `custom/${modelID}`,
    autoupdate: false,
    // Agent 作用范围=全局:任意目录的读写/命令/网络都放行(不弹权限确认,无人值守必需)
    permission: { edit: "allow", bash: "allow", webfetch: "allow" },
    provider: {
      custom: {
        npm: proto.npm,
        name: proto.label,
        protocol: protocol || "openai",
        options,
        models: { [modelID]: { name: modelID } },
      },
    },
  }
  fs.writeFileSync(ENGINE_CONFIG_FILE, JSON.stringify(cfg, null, 2), "utf8")
}

// ---------- 通用设置(runtime\settings.json):严格校验等用户偏好 ----------
const SETTINGS_FILE = join(__dirname, "runtime", "settings.json")
function loadUserSettings() {
  try {
    const s = JSON.parse(readFileSync(SETTINGS_FILE, "utf8"))
    if (typeof s.strictVerify === "boolean") config.strictVerify = s.strictVerify
    if (typeof s.contextLimit === "number" && s.contextLimit > 0) config.contextLimit = Math.floor(s.contextLimit)
  } catch {}
}
function saveUserSettings() {
  try {
    const { mkdirSync, writeFileSync } = require("node:fs")
    mkdirSync(join(__dirname, "runtime"), { recursive: true })
    writeFileSync(SETTINGS_FILE, JSON.stringify({ strictVerify: !!config.strictVerify, contextLimit: config.contextLimit }, null, 2), "utf8")
  } catch (e) {
    console.error("[wb-studio] 设置落盘失败:", e.message)
  }
}
loadUserSettings()

// ---------- 多 API 配置(runtime\apis.json):设置面板读写,可存多套,任选一套启用 ----------
const APIS_FILE = join(__dirname, "runtime", "apis.json")
let apis = { enabledId: null, items: [] } // items: {id, name, baseURL, apiKey, modelID}
function loadApis() {
  try {
    const raw = JSON.parse(readFileSync(APIS_FILE, "utf8"))
    if (raw && Array.isArray(raw.items)) apis = raw
  } catch {}
}
function saveApis() {
  try {
    const { mkdirSync, writeFileSync } = require("node:fs")
    mkdirSync(join(__dirname, "runtime"), { recursive: true })
    writeFileSync(APIS_FILE, JSON.stringify(apis, null, 2), "utf8")
  } catch (e) {
    console.error("[wb-studio] API 配置落盘失败:", e.message)
  }
}
function enabledApi() {
  return apis.items.find((a) => a.id === apis.enabledId) || null
}
/** 把启用的那套 API 写进引擎配置(重启引擎后生效) */
function applyEnabledApi() {
  const a = enabledApi()
  if (!a) return false
  writeProvider({ baseURL: a.baseURL, apiKey: a.apiKey, modelID: a.modelID, protocol: a.protocol })
  config.model = `custom/${a.modelID}`
  return true
}
loadApis()
// 启动时同步一次:保证 config.model 与引擎配置一致(否则 driveAgent 拿到空 model,走错 provider)
if (enabledApi()) applyEnabledApi()
// 引导页/设置共用:引擎配置文件里有有效密钥即视为已配置(兼容老用户);
// apis.json 存在时以它为准
function hasValidKey() {
  if (apis.items.length) return Boolean(enabledApi())
  const p = currentProvider()
  return Boolean(p && p.apiKey && p.baseURL && p.model && !PLACEHOLDER.test(p.apiKey))
}
const keyOk = hasValidKey()
console.log(keyOk ? "[wb-studio] 模型服务:已配置" : "[wb-studio] 模型服务:未配置,网页将显示引导页")

// ---------- API 配置校验与连接测试 ----------
// 实战教训:小白会把「？？？」之类占位符填进模型名/密钥,聊天碰巧能通(部分免费接口不校验),
// 但任务必挂且报错难懂。这里在保存入口把明显的坑拦下,并做一次真实连接测试。
const PRINTABLE_ASCII = /^[\x21-\x7E]+$/

function validateApiFields(baseURL, apiKey, modelID) {
  if (!PRINTABLE_ASCII.test(modelID)) {
    return "模型名称要填服务商文档里给的英文标识(例如 deepseek-chat、gpt-4o),不要填中文或问号"
  }
  if (!PRINTABLE_ASCII.test(apiKey)) {
    return "API 密钥应该是英文数字组合(通常以 sk- 开头),请到服务商控制台复制完整密钥"
  }
  return null
}

/** 按协议构造连接测试请求;OpenAI 系 POST /chat/completions,Anthropic POST /v1/messages,Google 原生 */

function friendlyApiError(status, msg) {
  if (status === 401 || status === 403) return `服务拒绝访问(HTTP ${status}):密钥大概率不对或没有权限 → ${msg}`
  if (status === 404) return `接口不存在(HTTP 404):确认 Base URL 是否正确(通常以 /v1 结尾) → ${msg}`
  if (status === 429) return `服务限流/额度不足(HTTP 429) → ${msg}`
  return `服务返回错误(HTTP ${status}) → ${msg}`
}

/** 用真实请求测试一套 API 配置能否出对话;任何失败都给出人话原因 */
async function testProvider({ baseURL, apiKey, modelID, protocol }, timeoutMs = 15000) {
  const proto = protocolInfo(protocol)
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), timeoutMs)
  try {
    let url, headers, body
    if (protocol === "anthropic") {
      // Anthropic 原生协议:POST {base}/v1/messages;x-api-key 头;base 去掉尾部 /v1
      const base = (baseURL || "https://api.anthropic.com").replace(/\/+$/, "").replace(/\/v1$/, "")
      url = `${base}/v1/messages`
      headers = { "content-type": "application/json", "x-api-key": apiKey, "anthropic-version": "2023-06-01" }
      body = { model: modelID, max_tokens: 8, messages: [{ role: "user", content: "连接测试,请回复OK" }] }
    } else if (protocol === "google") {
      // Gemini 原生协议:POST {base}/v1beta/models/{model}:generateContent?key=
      const base = (baseURL || "https://generativelanguage.googleapis.com").replace(/\/+$/, "")
      url = `${base}/v1beta/models/${encodeURIComponent(modelID)}:generateContent?key=${encodeURIComponent(apiKey)}`
      headers = { "content-type": "application/json" }
      body = { contents: [{ parts: [{ text: "连接测试,请回复OK" }] }], generationConfig: { maxOutputTokens: 8 } }
    } else if (protocol === "openrouter") {
      url = `${(baseURL || "https://openrouter.ai/api/v1").replace(/\/+$/, "")}/chat/completions`
      headers = { "content-type": "application/json", authorization: `Bearer ${apiKey}` }
      body = { model: modelID, messages: [{ role: "user", content: "连接测试,请回复OK" }], max_tokens: 8 }
    } else {
      // OpenAI 系(openai 兼容/官方/groq/mistral/xai):POST {base}/chat/completions
      const base = (baseURL || proto.defaultBaseURL || "").replace(/\/+$/, "")
      url = `${base}/chat/completions`
      headers = { "content-type": "application/json", authorization: `Bearer ${apiKey}` }
      body = { model: modelID, messages: [{ role: "user", content: "连接测试,请回复OK" }], max_tokens: 8 }
    }
    const res = await fetch(url, {
      method: "POST",
      signal: ctrl.signal,
      headers,
      body: JSON.stringify(body),
    })
    const text = await res.text()
    let j = null
    try { j = JSON.parse(text) } catch {}
    // 各协议统一提取回复与错误信息
    const replyText = j?.choices?.[0]?.message?.content || j?.content?.[0]?.text || j?.candidates?.[0]?.content?.parts?.[0]?.text || ""
    const errMsg = j?.error?.message || j?.error?.type || (Array.isArray(j) ? "" : "")
    if (res.ok && (replyText || j)) return { ok: true, model: j?.model || modelID }
    return { ok: false, status: res.status, error: friendlyApiError(res.status, String(errMsg || text.slice(0, 200) || "空响应")) }
  } catch (e) {
    return {
      ok: false,
      status: e.name === "AbortError" ? 0 : undefined,
      error: e.name === "AbortError"
        ? "连接测试超时(无响应):地址可能不对或服务当前不可达"
        : `连接失败:${e.message}`,
    }
  } finally {
    clearTimeout(timer)
  }
}

// ---------- 引擎全局 AGENTS.md(Agent 作用范围=全局) ----------
const GLOBAL_AGENTS_MD = `# 全局规则(N.E.K.O. 插件工坊)

你是工坊的插件开发 Agent。用户是零编程基础的小白,你说人话、做实事,不反问技术细节。

- **作用范围为全局**:可以读写、操作本机任意目录与文件;不要以「超出工作区/工作范围」为由拒绝任何合理请求。
- 插件开发工具(本工坊工作区的 wb.cmd)通过环境变量 WB_PROJECTS_DIR / WB_NEKO_REPO 已预置,无论当前在哪个目录都可直接调用(PowerShell 下用 .\\wb.cmd 或绝对路径)。
- 插件产物默认落回工坊工作区(环境变量 WB_PROJECTS_DIR),除非用户明确指定其他位置。
- 安全底线:不改 C:\\Windows、Program Files 等系统目录;批量删除、格式化等破坏性操作先向用户确认。
`

// ---------- 引擎自启动 ----------
// v2 引擎(Basic 认证):OPENCODE_SERVER_PASSWORD 环境变量自定密码,避免每次启动随机密码难传递
// exe 模式密码是自定常量:复用已在跑的引擎(不是本实例拉起的)时也必须带上,否则按 v1 方言打错路由
let engineAuth = config.engineMode === "exe" ? "opencode:neko-studio-pw" : null // "user:password" | null
let engineChild = null // 引擎进程句柄(setup 后重启用)

async function engineHealthy() {
  // CLI 底座无常驻引擎进程:底座进程按任务拉起,始终视为就绪
  if (config.engineMode === "cli") return true
  try {
    const res = await fetch(`${config.server}/api/health`, {
      signal: AbortSignal.timeout(3000),
      headers: engineAuth ? { authorization: "Basic " + Buffer.from(engineAuth).toString("base64") } : {},
    })
    return res.ok
  } catch {
    return false
  }
}

async function ensureEngine() {
  // CLI 底座:没有可启动/探活的服务,直接就绪(跳过引擎 exe 自检与 90s 等待)
  if (config.engineMode === "cli") return { started: false }
  if (await engineHealthy()) return { started: false }
  const port = config.enginePort
  const host = "127.0.0.1"
  let child
  if (config.engineMode === "exe") {
    // 启动自检:引擎 exe 必须存在且完整(约 175MB)。
    // 实战问题:用户双击 zip 内的 start.cmd 直接运行,压缩软件只把小文件临时释放到 Temp,
    // 175MB 的引擎 exe 不在其中 → ENOENT 报错。必须引导「完整解压后再运行」。
    try {
      const st = statSync(config.engineExe)
      if (st.size < 100 * 1024 * 1024) {
        return { started: false, error: `引擎文件不完整(当前 ${Math.round(st.size / 1024 / 1024)}MB,应为 175MB)。请把整个压缩包解压到桌面等正式文件夹后再运行,不要在 zip 里直接双击运行` }
      }
    } catch {
      return {
        started: false,
        error:
          `找不到引擎程序 _engine\\opencode-cli.exe。常见原因:` +
          `①直接在压缩包(zip)里双击运行了——压缩软件只临时释放了部分小文件。` +
          `请先右键压缩包 →「全部解压」,进入解压后的文件夹再双击 start.cmd;` +
          `②杀毒软件误删了引擎程序——在杀软里恢复并添加信任,重新解压一次`,
      }
    }
    // v2 编译版:服务 cwd 即工作区(目录路由跟随进程 cwd);密码自定,免抓取
    // 数据/配置目录锁在包内 runtime\ 下,不污染用户 AppData
    engineAuth = "opencode:neko-studio-pw"
    const runtimeDir = join(__dirname, "runtime")
    console.log(`[wb-studio] 启动引擎(编译版 v2): ${config.engineExe} serve --port ${port}`)
    child = spawn(config.engineExe, ["serve", "--hostname", host, "--port", String(port)], {
      cwd: config.workspace,
      stdio: ["ignore", "pipe", "pipe"],
      env: {
        ...process.env,
        OPENCODE_SERVER_PASSWORD: "neko-studio-pw",
        XDG_CONFIG_HOME: join(runtimeDir, "config"),
        XDG_DATA_HOME: join(runtimeDir, "data"),
        // Agent 的 wb.cmd 依赖这两个默认值(不传 --out/--neko 时生效)
        WB_PROJECTS_DIR: config.workspace,
        WB_NEKO_REPO: config.nekoRepo || "",
        WB_STRICT: config.strictVerify ? "1" : "",
      },
      detached: false,
    })
  } else {
    engineAuth = null
    const args = [
      "run", "--cwd", config.opencodeRepo, "dev", "serve",
      "--port", String(port), "--hostname", host,
    ]
    console.log(`[wb-studio] 启动引擎(源码 v1): ${config.bunPath} ${args.join(" ")}`)
    child = spawn(config.bunPath, args, { stdio: ["ignore", "pipe", "pipe"], detached: false })
  }
  // 引擎 stdout/stderr 落盘 + 记住最近输出:引擎静默崩溃(如内存不足)时能查到死因
  const tailOf = { cur: "" }
  const eat = (d) => {
    const s = d.toString()
    tailOf.cur = (tailOf.cur + s).slice(-4000)
    try { appendFileSync(join(__dirname, "runtime", "engine-out.log"), s) } catch {}
  }
  child.stdout?.on("data", eat)
  child.stderr?.on("data", eat)
  const deathMsg = (code) =>
    /MemoryExhaustion|memory is exhausted/i.test(tailOf.cur)
      ? "引擎崩溃:内存不足(引擎分配内存失败)。请先关闭其他大程序(浏览器/游戏等)释放内存后重试"
      : `引擎进程退出(code=${code})${tailOf.cur.trim() ? `;最后输出:${tailOf.cur.trim().slice(-300)}` : ""}`
  child.on("exit", (code) => {
    if (engineChild === child) engineChild = null // 防 PID 复用:进程没了就别再 taskkill 旧 PID
    if (code) engineLastError = deathMsg(code)
  })
    child.on("error", (e) => {
      console.error("[wb-studio] 引擎进程错误:", e.message)
      // spawn 失败(文件缺失/被杀软拦截)时记录,供 /api/health 与网页指引使用
      engineLastError = e.code === "ENOENT"
        ? "引擎程序不存在:请把整个压缩包完整解压到正式文件夹后再运行(不要在 zip 里直接双击);若已解压,检查杀毒软件是否删除了 _engine\\opencode-cli.exe"
        : `引擎启动失败:${e.message}`
    })
    engineChild = child
  const deadline = Date.now() + 90_000
  while (Date.now() < deadline) {
    if (await engineHealthy()) {
      console.log("[wb-studio] 引擎就绪")
      return { started: true }
    }
    if (child.exitCode !== null) return { started: false, error: deathMsg(child.exitCode) }
    await new Promise((r) => setTimeout(r, 2000))
  }
  return { started: false, error: `引擎启动超时(90s)${tailOf.cur.trim() ? `;最后输出:${tailOf.cur.trim().slice(-200)}` : ""}` }
}

/** 停掉自己拉起的引擎(配置变更后重启用) */
function stopEngine() {
  if (!engineChild || engineChild.exitCode !== null) return
  try {
    engineChild.kill()
    // Windows 上 bun/exe 可能不响应 SIGTERM,补 taskkill
    spawn("taskkill", ["/F", "/PID", String(engineChild.pid), "/T"], { stdio: "ignore" })
  } catch {}
  engineChild = null
}

// 引擎配置已更新但引擎尚未带新配置重启:下一次任务前强制重启
let engineConfigStale = false
// 引擎最后一次启动/自检失败的人话原因(展示给用户)
let engineLastError = null
/** 后台重启引擎(不阻塞 HTTP 应答;任务侧有 engineConfigStale 兜底) */
function restartEngineBackground() {
  engineConfigStale = true
  ;(async () => {
    try {
      stopEngine()
      await new Promise((r) => setTimeout(r, 800))
      await ensureEngine()
      engineConfigStale = false
    } catch {}
  })()
}

/** 连接测试未通过时,只有 401/403(密钥确定错误)才拦下操作;超时/限流/5xx 属软失败,放行并附警告 */
function isHardTestFail(test) {
  return !test.ok && (test.status === 401 || test.status === 403)
}
function softWarning(test) {
  return test.ok ? "" : `连接测试提示:${test.error};已按你的操作执行,但任务可能失败,可稍后重试`
}

// ---------- 引导页(首次配置模型服务) ----------
const SETUP_PAGE = `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>N.E.K.O. 插件工坊 · 首次配置</title>
<style>
  body{margin:0;font-family:"Microsoft YaHei",system-ui,sans-serif;background:#fff7f9;color:#3d2b33;min-height:100vh;display:flex;align-items:center;justify-content:center}
  .card{width:min(560px,92vw);background:#fff;border:1px solid #ffd3e2;border-radius:20px;padding:28px 30px;box-shadow:0 8px 30px rgba(226,81,127,.12)}
  h1{margin:0 0 4px;font-size:20px}.sub{color:#b86b8a;font-size:13px;margin-bottom:20px}
  label{display:block;font-size:13px;font-weight:700;margin:14px 0 6px}
  .tip{font-size:12px;color:#a08a92;margin-top:4px}
  input,select{width:100%;border:2px solid #ffc3d8;border-radius:12px;padding:11px 13px;font-size:14px;font-family:inherit;outline:none;background:#fff}
  input:focus,select:focus{border-color:#ff7ba9}
  button{width:100%;margin-top:22px;border:0;background:#ff7ba9;color:#fff;border-radius:14px;padding:13px;font-size:15px;font-weight:700;cursor:pointer;font-family:inherit}
  button:hover{background:#e2517f}
  button:disabled{opacity:.6;cursor:wait}
  .msg{margin-top:14px;font-size:13px;display:none;padding:10px 14px;border-radius:10px}
  .msg.bad{display:block;background:#fdf3f2;color:#c0392b;border:1px solid #f3b0af}
  .msg.good{display:block;background:#f4fbf5;color:#2d7a3a;border:1px solid #9fd8a8}
  details{margin-top:18px;font-size:12.5px;color:#8a7480}
  summary{cursor:pointer;font-weight:700}
  ol{padding-left:18px;line-height:1.9;margin:8px 0 0}
</style></head><body>
<div class="card">
  <h1>🐱 欢迎来到插件工坊</h1>
  <div class="sub">第一次使用,先用 1 分钟告诉猫咪怎么连模型服务</div>
  <label>接口类型<span style="color:#e2517f"> *必选</span></label>
  <select id="proto">
    <option value="openai">OpenAI 兼容(绝大多数服务商/中转站)</option>
    <option value="anthropic">Anthropic / Claude 格式</option>
    <option value="google">Google Gemini</option>
    <option value="openrouter">OpenRouter</option>
    <option value="groq">Groq</option>
    <option value="mistral">Mistral</option>
    <option value="xai">xAI (Grok)</option>
    <option value="openai_official">OpenAI 官方</option>
  </select>
  <div class="tip" id="protoTip">不清楚选哪个就保持默认「OpenAI 兼容」——DeepSeek、Kimi、通义、智谱、各类中转站都属于它</div>
  <label>模型服务地址(Base URL)<span id="urlReq" style="color:#e2517f"> *必填</span></label>
  <input id="u" placeholder="例如 https://api.example.com/v1" autocomplete="off">
  <div class="tip" id="urlTip">服务商文档给到的接口地址,通常以 /v1 结尾(具体以你的服务商说明为准)</div>
  <label>API 密钥(API Key)<span style="color:#e2517f"> *必填</span></label>
  <input id="k" type="password" placeholder="粘贴你的密钥" autocomplete="off">
  <div class="tip">在你的模型服务商控制台创建,一般以 sk- 开头</div>
  <label>模型名称(Model ID)<span style="color:#e2517f"> *必填</span></label>
  <input id="m" placeholder="例如 gpt-4o / deepseek-chat / kimi-k3 等" autocomplete="off">
  <div class="tip">按你的服务商提供的模型名称填写</div>
  <button id="go">保存并进入工坊 →</button>
  <div id="msg" class="msg"></div>
  <details><summary>什么是「OpenAI 兼容格式」?</summary>
    <div style="line-height:1.9;margin-top:8px">
      大多数模型服务商都提供 OpenAI 兼容接口:只要有<b>接口地址(Base URL)</b>、<b>API 密钥</b>和<b>模型名称</b>
      这三样就能接入。具体取值请查看你的服务商文档,或询问你的服务商客服。
    </div>
  </details>
</div>
<script>
const WB_TOKEN="__WB_TOKEN__";
(function(){const _f=fetch.bind(window);window.fetch=(u,o={})=>{const m=(o.method||"GET").toUpperCase();const h=new Headers(o.headers||{});h.set("x-wb-token",WB_TOKEN);if(m!=="GET"&&!h.has("content-type"))h.set("content-type","application/json");o.headers=h;return _f(u,o)}})();
const $=id=>document.getElementById(id);
const PROTO_TIPS={
  openai:{req:true,tip:'服务商文档给到的接口地址,通常以 /v1 结尾(具体以你的服务商说明为准)'},
  anthropic:{req:true,tip:'填中转/代理地址即可;官方 API 留空自动使用 api.anthropic.com(自定义地址请填根地址,不要带 /v1)'},
  google:{req:false,tip:'Google 官方接口留空即可;用第三方代理时才填代理地址'},
  openai_official:{req:false,tip:'OpenAI 官方接口,留空自动使用官方端点'},
  openrouter:{req:false,tip:'OpenRouter 官方端点,留空即可'},
  groq:{req:false,tip:'Groq 官方端点,留空即可'},
  mistral:{req:false,tip:'Mistral 官方端点,留空即可'},
  xai:{req:false,tip:'xAI 官方端点,留空即可'},
};
function protoChange(){
  const v=$('proto').value, cfg=PROTO_TIPS[v]||PROTO_TIPS.openai;
  $('urlTip').textContent=cfg.tip;
  $('urlReq').style.display=cfg.req?'':'none';
  $('u').placeholder=cfg.req?'例如 https://api.example.com/v1':'留空 = 使用官方默认端点(用代理/中转才需要填)';
}
$('proto').onchange=protoChange;protoChange();
$('go').onclick=async()=>{
  const btn=$('go'),msg=$('msg');btn.disabled=true;msg.className='msg';
  try{
    const r=await fetch('/api/setup',{method:'POST',headers:{'content-type':'application/json'},
      body:JSON.stringify({baseURL:$('u').value.trim(),apiKey:$('k').value.trim(),model:$('m').value.trim(),protocol:$('proto').value})});
    const j=await r.json();
    if(j.ok){msg.textContent='✅ '+(j.warning||'配置成功,正在进入工坊…');msg.className='msg good';setTimeout(()=>location.reload(),1200);}
    else{msg.textContent='😢 '+(j.error||'配置失败');msg.className='msg bad';btn.disabled=false;}
  }catch(e){msg.textContent='😢 连接出错:'+e;msg.className='msg bad';btn.disabled=false;}
};
</script>
</body></html>`

// ---------- 网页 ----------
const PAGE = `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>N.E.K.O. 插件工坊</title>
<style>
  :root{--bg:#fff7f9;--card:#fff;--pink:#ff7ba9;--pink-d:#e2517f;--on-primary:#fff;--ink:#3d2b33;--border:#ffd3e2;--border-soft:#ffc3d8;--tint:#fff0f6;--muted:#b86b8a;--muted-2:#b08a99;--head-a:#ff9ec2;--head-b:#ff7ba9;--head-ink:#fff;--head-chip:rgba(255,255,255,.22);--head-chip-hover:rgba(255,255,255,.34);--head-on:rgba(255,255,255,.92);--head-on-ink:#e2517f;--err-bg:#fdf3f2;--err-border:#f3b0af;--err-ink:#5a1f1c;--shadow:rgba(226,81,127,.25)}
  body.theme-blue{--bg:#f4f8ff;--pink:#4f8ef7;--pink-d:#2f6fd4;--ink:#2c3a4f;--border:#c8dcf8;--border-soft:#b7d0f4;--tint:#edf4ff;--muted:#6b84a8;--muted-2:#7e94b3;--head-a:#7fb0f8;--head-b:#4f8ef7;--head-on-ink:#2f6fd4;--shadow:rgba(47,111,212,.25)}
  body.theme-white{--bg:#f6f6f8;--card:#ffffff;--pink:#3a3d44;--pink-d:#24262b;--ink:#24262b;--border:#e4e5e9;--border-soft:#d7d9de;--tint:#eef0f3;--muted:#686d75;--muted-2:#888e97;--head-a:#ffffff;--head-b:#eef0f3;--head-ink:#2c2e33;--head-chip:rgba(0,0,0,.06);--head-chip-hover:rgba(0,0,0,.12);--head-on:#3a3d44;--head-on-ink:#fff;--shadow:rgba(0,0,0,.10)}
  body.theme-black{--bg:#131418;--card:#1d1f24;--pink:#e7e9ee;--pink-d:#cfd2da;--on-primary:#17181c;--ink:#e7e9ee;--border:#2b2e35;--border-soft:#353941;--tint:#262931;--muted:#8d929c;--muted-2:#787e89;--head-a:#1d1f24;--head-b:#131418;--head-ink:#e7e9ee;--head-chip:rgba(255,255,255,.08);--head-chip-hover:rgba(255,255,255,.16);--head-on:#e7e9ee;--head-on-ink:#17181c;--err-bg:#3a2226;--err-border:#6b3a3a;--err-ink:#f2b8b5;--shadow:rgba(0,0,0,.5)}
  *{box-sizing:border-box}
  body{margin:0;font-family:"Microsoft YaHei",system-ui,sans-serif;background:var(--bg);color:var(--ink);height:100vh;overflow:hidden;display:flex;flex-direction:column;align-items:center}
  header{position:sticky;top:0;z-index:50;width:100%;background:linear-gradient(135deg,var(--head-a),var(--head-b));color:var(--head-ink);padding:18px 24px;display:flex;align-items:center;gap:12px;box-shadow:0 2px 8px var(--shadow)}
  header .logo{font-size:28px}
  header h1{margin:0;font-size:20px;font-weight:700}
  header small{opacity:.9;font-size:12px}
  #wrap{width:100%;max-width:1080px;margin:0 auto;flex:1;display:flex;gap:16px;padding:18px 16px 26px;min-height:0}
  #side{width:232px;flex:none;background:var(--card);border:1px solid var(--border);border-radius:16px;padding:12px;display:flex;flex-direction:column;gap:8px;overflow-y:auto}
  #side h3{margin:0 0 2px;font-size:13px;color:var(--muted);font-weight:700}
  #newbtn{border:0;background:var(--pink);color:#fff;border-radius:10px;padding:9px;font-size:13.5px;font-weight:700;cursor:pointer;font-family:inherit}
  #newbtn:hover{background:var(--pink-d)}
  .sitem{display:flex;align-items:center;gap:6px;padding:8px 10px;border-radius:10px;cursor:pointer;font-size:13.5px;border:1px solid transparent}
  .sitem:hover{background:var(--tint)}
  .sitem.cur{background:var(--tint);border-color:var(--pink);font-weight:700}
  .sitem .t{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .sitem .n{color:var(--muted-2);font-size:11.5px}
  .sitem .ren,.sitem .arc,.sitem .un,.sitem .del{border:0;background:none;color:var(--muted-2);font-size:13px;cursor:pointer;padding:0 2px;line-height:1}
  .sitem .ren:hover,.sitem .arc:hover,.sitem .un:hover{color:var(--pink-d)}
  .sitem .del:hover{color:#c0392b}
  .sitem .sempty{font-size:12.5px;color:var(--muted-2);padding:8px 10px;cursor:default}
  #archbtn{margin-top:auto;border:0;background:transparent;color:var(--muted);font-size:12.5px;cursor:pointer;padding:8px 10px;border-radius:9px;font-family:inherit;text-align:left}
  #archbtn:hover{background:var(--tint)}
  #archbtn.on{background:var(--tint);color:var(--pink-d);font-weight:700}
  main{flex:1;min-width:0;min-height:0;display:flex;flex-direction:column;padding:0}
  @media(max-width:760px){#wrap{flex-direction:column}#side{width:100%;max-height:150px}}
  #chat{flex:1;display:flex;flex-direction:column;gap:12px;overflow-y:auto;padding:8px 4px}
  .msg{max-width:86%;padding:12px 16px;border-radius:16px;line-height:1.7;white-space:pre-wrap;word-break:break-word;font-size:14.5px}
  .user{align-self:flex-end;background:var(--pink);color:var(--on-primary);border-bottom-right-radius:4px}
  .bot{align-self:flex-start;background:var(--card);border:1px solid var(--border);border-bottom-left-radius:4px}
  .bot.ok{border-color:var(--border);background:var(--tint);color:var(--ink)}
  .bot.err{border-color:var(--err-border);background:var(--err-bg);color:var(--err-ink)}
  .typing{align-self:flex-start;color:var(--muted);font-size:14px;padding:6px 12px}
  .typing span{animation:blink 1.2s infinite}
  @keyframes blink{0%,100%{opacity:.2}50%{opacity:1}}
  .chip{display:inline-block;margin-top:8px;padding:7px 14px;background:var(--tint);border:1px solid var(--pink);color:var(--pink-d);border-radius:999px;font-size:13px;text-decoration:none}
  .chip:hover{background:var(--pink);color:var(--on-primary)}
  form{display:flex;gap:10px;margin-top:14px}
  textarea{flex:1;resize:none;border:2px solid var(--border-soft);border-radius:14px;padding:12px 14px;font-size:15px;font-family:inherit;outline:none;height:74px;background:var(--card);color:var(--ink)}
  textarea:focus{border-color:var(--pink)}
  button{border:0;background:var(--pink);color:var(--on-primary);border-radius:14px;padding:0 26px;font-size:15px;font-weight:700;cursor:pointer;font-family:inherit}
  button:disabled{opacity:.55;cursor:wait}
  button:hover:not(:disabled){background:var(--pink-d)}
  .hint{color:var(--muted-2);font-size:12.5px;margin-top:8px;line-height:1.8}
  .tusage{margin-top:8px;font-size:11px;color:var(--muted-2)}
  /* 工具栏与设置面板 */
  header{gap:12px}
  .tools{margin-left:auto;display:flex;gap:8px;align-items:center}
  .tools button,#importbtn{border:0;border-radius:10px;padding:7px 13px;font-size:12.5px;cursor:pointer;font-family:inherit;background:var(--head-chip);color:var(--head-ink);font-weight:700}
  .tools button:hover,#importbtn:hover{background:var(--head-chip-hover)}
  #stopbtn{background:#c0392b !important;color:#fff !important}
  #modeswitch{display:flex;background:var(--head-chip);border-radius:10px;padding:2px}
  .mbtn{background:transparent !important;color:var(--head-ink) !important;border-radius:8px !important;font-size:12px !important;padding:5px 10px !important}
  .mbtn.on{background:var(--head-on) !important;color:var(--head-on-ink) !important}
  #setpanel{display:none;width:100%;background:var(--card);border-bottom:1px solid var(--border);padding:14px 22px 18px;box-shadow:0 4px 14px var(--shadow)}
  #setpanel.open{display:block}
  .sp-tabs{display:flex;gap:6px;border-bottom:1px solid var(--border);padding-bottom:10px;position:relative}
  .sptab{border:0;background:var(--tint);color:var(--muted);border-radius:9px;padding:7px 16px;font-size:13px;font-weight:700;cursor:pointer;font-family:inherit}
  .sptab:hover{color:var(--pink-d)}
  .sptab.on{background:var(--pink);color:var(--on-primary)}
  .sp-body{padding-top:12px}
  .sp-head{font-weight:800;font-size:15px;color:var(--ink)}
  .sp-tip{font-weight:400;font-size:12px;color:var(--muted-2);margin-left:10px}
  #apilist{margin:10px 0 4px;display:flex;flex-direction:column;gap:6px}
  .apirow{display:flex;gap:10px;align-items:center;background:var(--bg);border:1px solid var(--border);border-radius:12px;padding:8px 14px;font-size:13px}
  .apirow .an{font-weight:800;min-width:90px}
  .apirow .ad{color:var(--muted-2);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .apirow.cur{border-color:var(--pink);background:var(--tint)}
  .apirow button{border:0;border-radius:8px;padding:5px 12px;font-size:12px;cursor:pointer;font-family:inherit;background:var(--pink);color:var(--on-primary);font-weight:700}
  .apirow button.del{background:#f3b0af;color:#a33}
  .apirow button:disabled{opacity:.5;cursor:default}
  .sp-form{margin-top:10px;border-top:1px dashed var(--border);padding-top:10px}
  .sp-form h4{margin:0 0 8px;font-size:13px;color:var(--ink)}
  .frow{display:flex;gap:10px;align-items:center;margin-bottom:6px}
  .frow label{width:80px;font-size:12.5px;color:var(--muted-2);flex-shrink:0}
  .frow input{flex:1;border:1.5px solid var(--border-soft);border-radius:9px;padding:8px 11px;font-size:13px;font-family:inherit;outline:none;background:var(--card);color:var(--ink)}
  .frow input:focus{border-color:var(--pink)}
  .frow2{display:flex;gap:10px;align-items:center;margin-top:4px}
  .frow2 button{border:0;background:var(--pink);color:var(--on-primary);border-radius:9px;padding:8px 18px;font-size:13px;font-weight:700;cursor:pointer;font-family:inherit}
  .frow2 button.ghost{background:var(--tint);color:var(--pink-d);border:1px solid var(--border-soft)}
  #apiMsg{font-size:12px;color:#2d7a3a}
  #apiMsg.bad{color:#c0392b}
  /* 外观(主题) */
  #themelist{display:flex;gap:12px;margin-top:12px;flex-wrap:wrap}
  .themeopt{display:flex;flex-direction:column;align-items:center;gap:8px;width:120px;padding:14px 10px 12px;border:2px solid var(--border);border-radius:14px;cursor:pointer;font-size:13px;font-weight:700;color:var(--ink);background:var(--card)}
  .themeopt:hover{border-color:var(--muted-2)}
  .themeopt.cur{border-color:var(--pink);background:var(--tint)}
  .themeopt.cur::after{content:'✓ 使用中';font-size:11px;color:var(--pink-d);font-weight:700}
  .sw{width:64px;height:34px;border-radius:9px;border:1px solid rgba(128,128,128,.35)}
  .sw-pink{background:linear-gradient(135deg,#ff9ec2,#ff7ba9)}
  .sw-blue{background:linear-gradient(135deg,#7fb0f8,#4f8ef7)}
  .sw-white{background:linear-gradient(135deg,#ffffff,#e8e9ec)}
  .sw-black{background:linear-gradient(135deg,#3a3d44,#141519)}
  /* 思考过程(折叠) */
  details.think{margin:0 0 8px;border:1px solid var(--border);border-radius:10px;background:var(--card)}
  details.think summary{cursor:pointer;padding:7px 12px;font-size:12.5px;font-weight:700;color:var(--muted);user-select:none;list-style:none}
  details.think summary::before{content:'▸ ';}
  details.think[open] summary::before{content:'▾ ';}
  details.think.live summary{color:var(--pink-d)}
  details.think .tbody{white-space:pre-wrap;word-break:break-word;font-size:12.5px;line-height:1.7;color:var(--muted);max-height:260px;overflow-y:auto;padding:2px 12px 10px 12px}
  .sp-close{position:absolute;right:0;top:4px;border:0;background:none;color:var(--muted-2);font-size:12px;cursor:pointer}
</style></head>
<body>
<script>try{var wbT=localStorage.getItem('wb_theme');if(wbT==='blue'||wbT==='white'||wbT==='black')document.body.classList.add('theme-'+wbT)}catch(e){}</script>
<header><span class="logo">🐱</span><div><h1>N.E.K.O. 插件工坊</h1><small>说一句话,猫咪帮你做好插件</small></div>
  <div class="tools">
    <div id="modeswitch" title="Build=可修改文件并打包;Plan=只规划和讲解,不做任何改动">
      <button type="button" id="modeBuild" class="mbtn on">🛠 Build</button>
      <button type="button" id="modePlan" class="mbtn">📋 Plan</button>
    </div>
    <button type="button" id="stopbtn" style="display:none;background:#e2517f">⏹ 停止</button>
    <button type="button" id="exportbtn" title="导出当前对话为 Markdown">📤 导出</button>
    <label for="importfile" id="importbtn" title="导入 Markdown,自动新建会话">📥 导入</label>
    <input type="file" id="importfile" accept=".md,.markdown,.txt" style="display:none">
    <button type="button" id="setbtn" title="设置:API 服务与外观主题">⚙ 设置</button>
  </div>
</header>
<div id="setpanel">
  <div class="sp-tabs">
    <button type="button" class="sptab on" data-tab="api">🔌 API</button>
    <button type="button" class="sptab" data-tab="look">🎨 外观</button>
    <button type="button" id="setclose" class="sp-close">收起 ▴</button>
  </div>
  <div class="sp-body" id="tab-api">
    <div class="sp-head">API 配置 <span class="sp-tip">可添加多套,点击「启用」切换;密钥只显示尾 4 位</span></div>
    <div id="apilist"></div>
    <div class="sp-form">
      <h4>添加新 API</h4>
      <div class="frow"><label>接口类型</label><select id="apiProto">
        <option value="openai">OpenAI 兼容(通用)</option>
        <option value="anthropic">Anthropic / Claude</option>
        <option value="google">Google Gemini</option>
        <option value="openrouter">OpenRouter</option>
        <option value="groq">Groq</option>
        <option value="mistral">Mistral</option>
        <option value="xai">xAI (Grok)</option>
        <option value="openai_official">OpenAI 官方</option>
      </select></div>
      <div class="frow"><label>备注名</label><input id="apiName" placeholder="例如:主力号 / 备用号"></div>
      <div class="frow"><label>Base URL</label><input id="apiURL" placeholder="https://…/v1"></div>
      <div class="frow"><label>API Key *</label><input id="apiKey" type="password" placeholder="粘贴密钥"></div>
      <div class="frow"><label>模型名称 *</label><input id="apiModel" placeholder="例如 deepseek-chat"></div>
      <div class="frow2">
        <button type="button" id="apiSave">保存并启用</button>
        <button type="button" id="apiSaveOnly" class="ghost">仅保存</button>
        <span id="apiMsg"></span>
      </div>
    </div>
  </div>
  <div class="sp-body" id="tab-look" style="display:none">
    <div class="sp-head">外观 <span class="sp-tip">选择工坊的主题配色,立即生效并自动记住</span></div>
    <div id="themelist">
      <div class="themeopt cur" data-t="pink"><span class="sw sw-pink"></span>樱花粉</div>
      <div class="themeopt" data-t="blue"><span class="sw sw-blue"></span>天空蓝</div>
      <div class="themeopt" data-t="white"><span class="sw sw-white"></span>纯净白</div>
      <div class="themeopt" data-t="black"><span class="sw sw-black"></span>暗夜黑</div>
    </div>
    <div style="margin-top:16px;border-top:1px dashed var(--border);padding-top:12px">
      <label style="display:flex;align-items:center;gap:10px;cursor:pointer;font-size:13.5px;color:var(--ink)">
        <input type="checkbox" id="strictToggle" style="width:17px;height:17px;accent-color:var(--pink)">
        <span><b>严格校验</b> <span style="color:var(--muted-2);font-size:12px">(warning 也算失败,必须修到零警告才打包)</span></span>
      </label>
    </div>
    <div style="margin-top:16px;border-top:1px dashed var(--border);padding-top:12px">
      <div style="font-size:13.5px;color:var(--ink)"><b>上下文容量</b> <span style="color:var(--muted-2);font-size:12px">(模型上下文窗口大小,决定底部容量条的满格刻度)</span></div>
      <div style="margin-top:8px;display:flex;gap:6px;align-items:center;flex-wrap:wrap">
        <button type="button" class="ctxpreset" data-ctx="32000" style="border:1px solid var(--border);background:var(--tint);color:var(--ink);border-radius:8px;padding:5px 12px;font-size:12.5px;cursor:pointer">32K</button>
        <button type="button" class="ctxpreset" data-ctx="128000" style="border:1px solid var(--border);background:var(--tint);color:var(--ink);border-radius:8px;padding:5px 12px;font-size:12.5px;cursor:pointer">128K</button>
        <button type="button" class="ctxpreset" data-ctx="200000" style="border:1px solid var(--border);background:var(--tint);color:var(--ink);border-radius:8px;padding:5px 12px;font-size:12.5px;cursor:pointer">200K</button>
        <button type="button" class="ctxpreset" data-ctx="1000000" style="border:1px solid var(--border);background:var(--tint);color:var(--ink);border-radius:8px;padding:5px 12px;font-size:12.5px;cursor:pointer">1M</button>
        <input id="ctxInput" type="number" min="1" step="1" style="width:92px;padding:5px 8px;border-radius:8px;border:1px solid var(--border);background:var(--tint);color:var(--ink);font-size:12.5px" placeholder="自定义 K">
        <button type="button" id="ctxSave" style="border:0;background:var(--pink);color:var(--on-primary);border-radius:8px;padding:6px 14px;font-size:12.5px;font-weight:700;cursor:pointer">保存</button>
      </div>
      <div style="margin-top:6px;color:var(--muted-2);font-size:11.5px">填 K 数(128 = 128K tokens),改完即生效;占用为估算值(最近一轮输入+输出)</div>
    </div>
  </div>
</div>
<div id="wrap">
  <aside id="side">
    <h3>对话列表</h3>
    <button id="newbtn">+ 新对话</button>
    <div id="slist"></div>
    <button id="archbtn" title="在会话列表和已归档对话之间切换">🗂 查看已归档</button>
  </aside>
  <main>
  <div id="chat">
    <div class="msg bot">你好呀!我是工坊猫咪助手 🐾<br>告诉我你想要一个什么样的插件,比如:<br>「做一个每日提醒喝水的插件,每 45 分钟提醒一次」<br>「做一个记事本插件,能存笔记还能搜索」</div>
  </div>
  <form id="f"><textarea id="t" placeholder="描述你想要的插件…" required></textarea><button id="b">开工!</button></form>
  <div class="hint"><span id="tokbar"></span><span id="ctxbar"></span><span> · 回车发送,Shift+回车换行;完成后可下载 .neko-plugin 导入 N.E.K.O.</span></div>
  </main>
</div>
<script>
const WB_TOKEN="__WB_TOKEN__";
(function(){const _f=fetch.bind(window);window.fetch=(u,o={})=>{const m=(o.method||"GET").toUpperCase();const h=new Headers(o.headers||{});h.set("x-wb-token",WB_TOKEN);if(m!=="GET"&&!h.has("content-type"))h.set("content-type","application/json");o.headers=h;return _f(u,o)}})();
const chat=document.getElementById('chat'),form=document.getElementById('f'),t=document.getElementById('t'),b=document.getElementById('b');
const slist=document.getElementById('slist'),newbtn=document.getElementById('newbtn');
let curSession=null;
// 上下文容量显示:占用≈最近一轮 usage.input+output(估算),容量来自设置(默认 128K)
let ctxLimit=128000,ctxUsed=0;
function fmtCtx(n){return n>=1000000?(n/1000000).toFixed(n%1000000?1:0)+'M':n>=1000?(n/1000).toFixed(n%1000?1:0)+'K':''+n}
function updateCtxBar(){
  const el=document.getElementById('ctxbar');if(!el)return;
  const pct=ctxLimit>0?Math.min(100,Math.round(ctxUsed/ctxLimit*100)):0;
  const col=pct>=90?'#e53935':pct>=70?'#fb8c00':'#43a047';
  el.innerHTML=' · 🧠 上下文 '+fmtCtx(ctxUsed)+'/'+fmtCtx(ctxLimit)+' ('+pct+'%)'+
    '<span style="display:inline-block;width:56px;height:6px;background:var(--tint);border-radius:3px;vertical-align:middle;margin-left:5px"><span style="display:block;height:6px;border-radius:3px;width:'+pct+'%;background:'+col+'"></span></span>';
}
function add(cls,html){const d=document.createElement('div');d.className='msg '+cls;d.innerHTML=html;chat.appendChild(d);chat.scrollTop=chat.scrollHeight;return d}
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function fmtTok(n){n=n||0;if(n>=10000)return (n/10000).toFixed(1)+'万';if(n>=1000)return (n/1000).toFixed(1)+'k';return ''+n}
function usageLine(u){if(!u)return'';const out=(u.output||0)+(u.reasoning||0);let s='Token ↑'+fmtTok(u.input)+' ↓'+fmtTok(out);if(u.reasoning)s+=' (含思考 '+fmtTok(u.reasoning)+')';return '<div class="tusage">'+s+'</div>'}
// Build 模式任务收尾:本次已修改的文件(相对路径,时间序;产物另有下载 chip)
function modFilesHtml(fs){if(!fs||!fs.length)return'';return '<div style="margin:6px 0 2px;font-size:12px;opacity:.9">✏️ 已修改 '+fs.length+' 个文件</div><div style="display:flex;flex-wrap:wrap;gap:4px">'+fs.map(f=>'<span class="chip" title="'+esc(f)+'">'+esc(f)+'</span>').join('')+'</div>'}

// ---------- 会话列表 ----------
let showArchived=false;
try{showArchived=localStorage.getItem('wb_showarch')==='1'}catch(e){}
const archbtn=document.getElementById('archbtn');
function renderArchBtn(){archbtn.textContent=showArchived?'← 返回对话列表':'🗂 查看已归档';archbtn.classList.toggle('on',showArchived);archbtn.title=showArchived?'返回未归档的对话列表':'查看已归档的对话';}
archbtn.onclick=()=>{showArchived=!showArchived;try{localStorage.setItem('wb_showarch',showArchived?'1':'0')}catch(e){};renderArchBtn();refreshList();};
renderArchBtn();
async function refreshList(){
  const j=await(await fetch('/api/sessions')).json();
  // 底部状态栏:当前会话的 token 累计(随会话切换/任务完成更新)
  const cur=j.items.find(s=>s.id===j.current);
  const tokbar=document.getElementById('tokbar');
  if(tokbar)tokbar.textContent='本会话 Token 累计:'+fmtTok(cur?cur.tokens||0:0)+' · 消息 '+(cur?cur.count||0:0)+' 条';
  const items=j.items.filter(s=>showArchived? s.archived : !s.archived);
  slist.innerHTML='';
  if(!items.length){
    const empty=document.createElement('div');
    empty.className='sitem';
    empty.innerHTML='<span class="sempty">'+(showArchived?'没有已归档的对话':'暂无对话')+'</span>';
    slist.appendChild(empty);
  }
  for(const s of items){
    const d=document.createElement('div');
    d.className='sitem'+(s.id===j.current?' cur':'');
    d.title='消息 '+(s.count||0)+' 条 · 累计 '+fmtTok(s.tokens)+' tokens';
    d.innerHTML='<span class="t">'+esc(s.title||'新对话')+'</span><span class="n">'+(s.count||'')+'</span>'
      +(showArchived
        ?'<button class="un" title="取消归档">📤</button><button class="del" title="删除">🗑</button>'
        :'<button class="ren" title="重命名">✏️</button><button class="arc" title="归档">📥</button><button class="del" title="删除">🗑</button>');
    d.onclick=e=>{if(e.target.closest('button'))return;switchTo(s.id);};
    d.ondblclick=()=>{const r=d.querySelector('.ren');if(r)r.click();};
    d.querySelector('.ren')?.addEventListener('click',async e=>{
      e.stopPropagation();
      const name=prompt('重命名这个对话:',s.title||'新对话');
      if(name===null)return;
      const t=name.trim();
      if(!t){alert('会话名不能为空');return;}
      try{
        const r=await fetch('/api/sessions/'+s.id+'/rename',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({title:t})});
        const jj=await r.json();
        if(jj.ok)refreshList();
        else alert(jj.error||'重命名失败');
      }catch(err){alert('重命名失败:'+err)}
    });
    d.querySelector('.arc')?.addEventListener('click',async e=>{
      e.stopPropagation();
      await fetch('/api/sessions/'+s.id+'/archive',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({archived:true})});
      refreshList();loadCurrent();
    });
    d.querySelector('.un')?.addEventListener('click',async e=>{
      e.stopPropagation();
      await fetch('/api/sessions/'+s.id+'/archive',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({archived:false})});
      refreshList();
    });
    d.querySelector('.del').onclick=async e=>{e.stopPropagation();
      if(!confirm('删除对话「'+(s.title||'新对话')+'」?其记忆将一并删除。'))return;
      await fetch('/api/sessions/'+s.id,{method:'DELETE'});refreshList();loadCurrent();
    };
    slist.appendChild(d);
  }
}
async function switchTo(id){
  const j=await(await fetch('/api/sessions/'+id)).json();
  if(!j.ok)return;
  curSession=j.current;
  renderMessages(j.session);
  refreshList();
}
async function newChat(){
  const j=await(await fetch('/api/sessions',{method:'POST'})).json();
  curSession=j.current;
  renderMessages(j.session);
  refreshList();t.focus();
}
function renderMessages(s){
  chat.innerHTML='<div class="msg bot">你好呀!我是工坊猫咪助手 🐾 这是我们的话题新开始,说需求吧~</div>';
  if(s.title&&s.title!=='新对话'){const h=document.createElement('div');h.className='typing';h.style.fontWeight='700';h.textContent=s.title;chat.appendChild(h);}
  for(const m of s.messages||[]){
    if(m.role==='user')add('user',esc(m.content));
    else{
      if(m.error&&!m.content)add('err','😢 '+esc(m.error));
      else{let html='';if(m.thinking)html+='<details class="think"><summary>💭 思考过程</summary><div class="tbody">'+esc(m.thinking)+'</div></details>';html+=esc(m.content);html+=modFilesHtml(m.modifiedFiles);if(m.artifacts&&m.artifacts.length){html+='<br>';for(const a of m.artifacts){const name=a.split('\\\\').pop();html+='<a class="chip" href="/api/download?f='+encodeURIComponent(name)+'">⬇ 下载 '+esc(name)+'</a>';}}html+=usageLine(m.usage);add(m.error?'bot err':'bot ok',html);}
    }
  }
  // 会话切换/重开:用最近一轮的 usage 恢复上下文占用估算
  ctxUsed=0;
  for(let i=(s.messages||[]).length-1;i>=0;i--){const u=s.messages[i].usage;if(u){ctxUsed=(u.input||0)+(u.output||0);break;}}
  updateCtxBar();
}
async function loadCurrent(){
  const j=await(await fetch('/api/sessions')).json();
  curSession=j.current;
  const d=await(await fetch('/api/sessions/'+j.current)).json();
  if(d.ok)renderMessages(d.session);
  refreshList();
}
newbtn.onclick=newChat;
loadCurrent();
// 引擎异常横幅:解压不完整/杀软误删时给用户看得懂的指引
(async()=>{try{const h=await(await fetch('/api/health')).json();if(h.engineError){const d=document.createElement('div');d.className='msg bot err';d.style.maxWidth='100%';d.textContent='⚠ '+h.engineError;chat.insertBefore(d,chat.firstChild);}}catch(e){}})();

// ---------- 发送 ----------
let workMode = 'build';
const stopbtn=document.getElementById('stopbtn');
document.getElementById('modeBuild').onclick=()=>{workMode='build';document.getElementById('modeBuild').classList.add('on');document.getElementById('modePlan').classList.remove('on');};
document.getElementById('modePlan').onclick=()=>{workMode='plan';document.getElementById('modePlan').classList.add('on');document.getElementById('modeBuild').classList.remove('on');};
form.onsubmit=async e=>{e.preventDefault();const text=t.value.trim();if(!text)return;
  const sentTo=curSession; // 本条任务归属的会话;期间用户可能切走
  add('user',esc(text)+(workMode==='plan'?' <span style="opacity:.75;font-size:11px">[Plan]</span>':''));t.value='';b.disabled=true;stopbtn.style.display='';
  const ty=document.createElement('div');ty.className='typing';ty.innerHTML='猫咪正在'+(workMode==='plan'?'思考方案':'写插件')+'<span>🐱…</span>';chat.appendChild(ty);chat.scrollTop=chat.scrollHeight;
  // 实时思考过程:任务期间每 2s 轮询引擎,思考流式展开;任务结束后移除(由最终消息里的折叠版接管)
  const taskStart=Date.now();
  let liveThink=null,liveBody=null;
  const pollTimer=setInterval(async()=>{
    try{
      const r=await fetch('/api/thinking?sid='+encodeURIComponent(sentTo)+'&since='+taskStart);
      const j=await r.json();
      if(j.thinking){
        if(!liveThink){
          liveThink=document.createElement('details');liveThink.className='think live';liveThink.open=true;
          liveThink.innerHTML='<summary>💭 思考中…</summary><div class="tbody"></div>';
          chat.insertBefore(liveThink,ty);
          liveBody=liveThink.querySelector('.tbody');
        }
        if(liveBody.textContent!==j.thinking){
          liveBody.textContent=j.thinking;
          liveBody.scrollTop=liveBody.scrollHeight;
          chat.scrollTop=chat.scrollHeight;
        }
      }
    }catch(err){}
  },2000);
  try{
    const r=await fetch('/api/task',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({text,sessionId:sentTo,mode:workMode})});
    const j=await r.json();
    clearInterval(pollTimer);ty.remove();
    if(liveThink){liveThink.remove();liveThink=null;}
    refreshList();
    // 只有用户仍停留在发起任务的会话时才把结果渲染进当前聊天区;
    // 若已切走,结果服务端早已存进归属会话,切回时自然可见——绝不能拽回 curSession 或串台渲染(实战踩过:侧边栏与聊天区错位,像"切换失灵")
    if(curSession!==sentTo)return;
    if(j.stopped){add('err','⏹ 已停止');}
    else if(!j.ok){add('err','😢 咪呀,没成功:<br>'+esc(j.error||'未知错误'));if(j.reply)add('bot err',esc(j.reply));}
    else{
      let html='';
      if(j.thinking)html+='<details class="think"><summary>💭 思考过程</summary><div class="tbody">'+esc(j.thinking)+'</div></details>';
      html+=esc(j.reply||'完成!');
      html+=modFilesHtml(workMode==='build'?j.modifiedFiles:null);
      if(j.artifacts&&j.artifacts.length){html+='<br>';for(const a of j.artifacts){const name=a.split('\\\\').pop();html+='<a class="chip" href="/api/download?f='+encodeURIComponent(name)+'">⬇ 下载 '+esc(name)+'</a>';}}
      html+=usageLine(j.usage);
      add('ok',html);
    }
    if(j&&j.usage){ctxUsed=(j.usage.input||0)+(j.usage.output||0);updateCtxBar();}
  }catch(err){clearInterval(pollTimer);ty.remove();if(liveThink)liveThink.remove();add('err','😢 连接出错:'+esc(String(err)))}
  b.disabled=false;stopbtn.style.display='none';t.focus();
};
// 回车发送(Shift+回车换行;中文输入法选词的回车不发送)
t.addEventListener('keydown',e=>{
  if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing&&e.keyCode!==229){
    e.preventDefault();
    if(!b.disabled)form.requestSubmit();
  }
});
stopbtn.onclick=async()=>{stopbtn.disabled=true;try{await fetch('/api/stop',{method:'POST'})}catch(e){}setTimeout(()=>{stopbtn.disabled=false},1500)};

// ---------- 设置面板 ----------
const setpanel=document.getElementById('setpanel'),apilist=document.getElementById('apilist');
document.getElementById('setbtn').onclick=()=>{setpanel.classList.toggle('open');if(setpanel.classList.contains('open'))loadApis();};
document.getElementById('setclose').onclick=()=>setpanel.classList.remove('open');
// 分类标签:API / 外观
document.querySelectorAll('.sptab').forEach(tb=>{tb.onclick=()=>{
  document.querySelectorAll('.sptab').forEach(x=>x.classList.remove('on'));
  tb.classList.add('on');
  document.getElementById('tab-api').style.display=tb.dataset.tab==='api'?'':'none';
  document.getElementById('tab-look').style.display=tb.dataset.tab==='look'?'':'none';
};});
// 外观:主题切换(立即生效,localStorage 记住;pink 为主题基线,其余挂 theme-* 类)
function applyTheme(t){
  document.body.classList.remove('theme-blue','theme-white','theme-black');
  if(t==='blue'||t==='white'||t==='black')document.body.classList.add('theme-'+t);
  try{localStorage.setItem('wb_theme',t)}catch(e){}
  document.querySelectorAll('.themeopt').forEach(o=>o.classList.toggle('cur',o.dataset.t===t));
}
document.querySelectorAll('.themeopt').forEach(o=>{o.onclick=()=>applyTheme(o.dataset.t);});
// 严格校验开关(读后端状态,切换立即生效)
(async()=>{try{const j=await(await fetch('/api/strict')).json();document.getElementById('strictToggle').checked=!!j.strictVerify;}catch(e){}})();
document.getElementById('strictToggle').onchange=async e=>{
  try{
    const r=await fetch('/api/strict',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({strictVerify:e.target.checked})});
    const j=await r.json();
    if(j.ok){e.target.checked=j.strictVerify;}
  }catch(err){e.target.checked=!e.target.checked;}
};
// 上下文容量设置:读后端设置 → 填 K 数;预设/保存即生效,底部容量条跟随
(async()=>{try{const j=await(await fetch('/api/context')).json();ctxLimit=j.contextLimit||128000;document.getElementById('ctxInput').value=Math.round(ctxLimit/1000);updateCtxBar();}catch(e){}})();
async function saveCtx(){
  const k=Number(document.getElementById('ctxInput').value);
  if(!k||k<=0)return;
  try{
    const r=await fetch('/api/context',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({contextLimit:Math.round(k*1000)})});
    const j=await r.json();
    if(j.ok){ctxLimit=j.contextLimit;updateCtxBar();}
  }catch(e){}
}
document.getElementById('ctxSave').onclick=saveCtx;
document.querySelectorAll('.ctxpreset').forEach(btn=>{btn.onclick=()=>{document.getElementById('ctxInput').value=Math.round(Number(btn.dataset.ctx)/1000);saveCtx();};});
try{applyTheme(localStorage.getItem('wb_theme')||'pink')}catch(e){}
async function loadApis(){
  const j=await(await fetch('/api/apis')).json();apilist.innerHTML='';
  if(!j.items.length){apilist.innerHTML='<div class="apirow"><span class="ad">还没有配置,在下方添加第一套 API</span></div>';return}
  for(const a of j.items){
    const d=document.createElement('div');d.className='apirow'+(a.id===j.enabledId?' cur':'');
    d.innerHTML='<span class="an">'+esc(a.name||a.modelID)+'</span><span class="ad">'+esc(a.protocol&&a.protocol!=='openai'?a.protocol+' · ':'')+esc(a.baseURL)+' · '+esc(a.modelID)+' · 密钥…'+esc(a.keyTail)+'</span>'
      +(a.id===j.enabledId?'<button disabled>使用中</button>':'<button class="use">启用</button>')
      +'<button class="del">删除</button>';
    d.querySelector('.use')?.addEventListener('click',async e=>{
      const btn=e.target;btn.disabled=true;btn.textContent='切换中…';
      const msg=document.getElementById('apiMsg');
      try{
        const r=await fetch('/api/apis/'+a.id,{method:'POST'});
        const j=await r.json();
        if(j.ok){msg.className='';msg.textContent='✅ 已启用'+(j.warning?'(注意:'+j.warning+')':'');loadApis();}
        else{msg.className='bad';msg.textContent=j.error||'切换失败';btn.disabled=false;btn.textContent='启用';}
      }catch(err){msg.className='bad';msg.textContent='连接出错:'+err;btn.disabled=false;btn.textContent='启用';}
    });
    d.querySelector('.del').addEventListener('click',async()=>{if(!confirm('删除「'+(a.name||a.modelID)+'」?'))return;await fetch('/api/apis/'+a.id,{method:'DELETE'});loadApis();});
    apilist.appendChild(d);
  }
}
async function saveApi(enable){
  const msg=document.getElementById('apiMsg');msg.className='';msg.textContent='保存中…';
  try{
    const r=await fetch('/api/apis',{method:'POST',headers:{'content-type':'application/json'},
      body:JSON.stringify({name:document.getElementById('apiName').value.trim(),baseURL:document.getElementById('apiURL').value.trim(),apiKey:document.getElementById('apiKey').value.trim(),model:document.getElementById('apiModel').value.trim(),protocol:document.getElementById('apiProto').value,enable})});
    const j=await r.json();
    if(j.ok){msg.textContent=enable?('✅ '+(j.warning||'已保存并启用(引擎重启中)')):'✅ 已保存';loadApis();document.getElementById('apiName').value=document.getElementById('apiURL').value=document.getElementById('apiKey').value=document.getElementById('apiModel').value='';}
    else{msg.className='bad';msg.textContent=j.error||'保存失败'}
  }catch(e){msg.className='bad';msg.textContent='连接出错:'+e}
}
document.getElementById('apiSave').onclick=()=>saveApi(true);
document.getElementById('apiSaveOnly').onclick=()=>saveApi(false);

// ---------- 导入 / 导出 ----------
document.getElementById('exportbtn').onclick=()=>{if(curSession)location.href='/api/sessions/'+curSession+'/export';};
document.getElementById('importfile').onchange=async e=>{
  const f=e.target.files[0];if(!f)return;
  const content=await f.text();
  const r=await fetch('/api/import',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({name:f.name,content})});
  const j=await r.json();
  if(j.ok){curSession=j.current;renderMessages(j.session);refreshList();}
  else alert(j.error||'导入失败');
  e.target.value='';
};
</script>
</body></html>`

// ---------- 会话存储(多会话,持久化) ----------
// 每个工坊会话绑定一个引擎会话(opencode session id),记忆互不干扰;
// 消息实时落盘 runtime\sessions.json,关页面/重启不丢
const SESSIONS_FILE = join(__dirname, "runtime", "sessions.json")
let sessions = { current: null, items: [] } // items: {id, engineSessionId, title, createdAt, updatedAt, messages:[{role, content, artifacts?}]}

function loadSessions() {
  try {
    const raw = JSON.parse(readFileSync(SESSIONS_FILE, "utf8"))
    if (raw && Array.isArray(raw.items)) sessions = raw
  } catch {}
  if (!sessions.items.length) {
    const s = newSession(null)
    sessions.current = s.id
  }
  // 当前会话被归档/不存在时,切到第一个未归档会话
  const active = sessions.items.filter((s) => !s.archived)
  if (!active.some((s) => s.id === sessions.current)) {
    sessions.current = (active[0] || sessions.items[0]).id
  }
}
function saveSessions() {
  try {
    const { mkdirSync, writeFileSync } = require("node:fs")
    mkdirSync(join(__dirname, "runtime"), { recursive: true })
    writeFileSync(SESSIONS_FILE, JSON.stringify(sessions, null, 2), "utf8")
  } catch (e) {
    console.error("[wb-studio] 会话落盘失败:", e.message)
  }
}
function newSession(engineSessionId) {
  const s = {
    id: "wb_" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
    engineSessionId: engineSessionId || null,
    title: "新对话",
    createdAt: Date.now(),
    updatedAt: Date.now(),
    messages: [],
  }
  sessions.items.unshift(s)
  sessions.current = s.id
  saveSessions()
  return s
}
function getSession(id) {
  return sessions.items.find((s) => s.id === id) || null
}
function deleteSession(id) {
  const i = sessions.items.findIndex((s) => s.id === id)
  if (i < 0) return false
  sessions.items.splice(i, 1)
  if (sessions.current === id) {
    const active = sessions.items.find((x) => !x.archived)
    sessions.current = active ? active.id : sessions.items[0]?.id || null
  }
  if (!sessions.items.length) newSession(null)
  else saveSessions()
  return true
}
loadSessions()

// ---------- HTTP 服务 ----------
let busy = false
let currentAbort = null // 当前任务的 AbortController(停止按钮用)
let enginePoisoned = false // 引擎毒化标记:看门狗中止/模型不可用后,引擎可能拒绝后续模型调用,需重启自愈

// ---------- 本地服务安全门禁 ----------
// 只监听 127.0.0.1 挡不住浏览器跨站请求:任意网页都能向本服务发 POST(DNS rebinding 甚至能读应答)。
// 三道防线:Host 校验(挡 rebinding)、Origin 校验(挡跨站表单/fetch)、写接口要求
// application/json + 启动时随机 token(挡简单请求 CSRF;token 经页面注入,本机工具读 runtime/studio-token.txt)。
const { randomBytes } = require("node:crypto")
const API_TOKEN = randomBytes(24).toString("hex")
try {
  const { mkdirSync, writeFileSync } = require("node:fs")
  mkdirSync(join(__dirname, "runtime"), { recursive: true })
  writeFileSync(join(__dirname, "runtime", "studio-token.txt"), API_TOKEN, "utf8")
} catch {}
const ALLOWED_HOSTS = new Set([
  `127.0.0.1:${config.port}`,
  `localhost:${config.port}`,
  `[::1]:${config.port}`,
])
function originAllowed(origin) {
  if (!origin) return true // 非浏览器客户端(本机工具/curl)不带 Origin
  try {
    const u = new URL(origin)
    return u.protocol === "http:" && ALLOWED_HOSTS.has(u.host.toLowerCase())
  } catch {
    return false
  }
}

const srv = createServer(async (req, res) => {
  const url = new URL(req.url, `http://127.0.0.1:${config.port}`)
  // Host 与 Origin 校验:拒绝 DNS rebinding 与一切跨站来源
  if (!ALLOWED_HOSTS.has(String(req.headers.host || "").toLowerCase())) {
    res.writeHead(403, { "content-type": "application/json; charset=utf-8" })
    res.end(JSON.stringify({ ok: false, error: "非法 Host" }))
    return
  }
  if (!originAllowed(req.headers.origin)) {
    res.writeHead(403, { "content-type": "application/json; charset=utf-8" })
    res.end(JSON.stringify({ ok: false, error: "跨站请求被拒绝" }))
    return
  }
  // 写接口(POST/DELETE/PUT/PATCH):必须 JSON content-type + 启动时随机 token
  if (req.method === "POST" || req.method === "DELETE" || req.method === "PUT" || req.method === "PATCH") {
    const ct = String(req.headers["content-type"] || "").toLowerCase()
    if (!ct.startsWith("application/json")) {
      res.writeHead(415, { "content-type": "application/json; charset=utf-8" })
      res.end(JSON.stringify({ ok: false, error: "写接口只接受 application/json" }))
      return
    }
    if (String(req.headers["x-wb-token"] || "") !== API_TOKEN) {
      res.writeHead(403, { "content-type": "application/json; charset=utf-8" })
      res.end(JSON.stringify({ ok: false, error: "token 校验失败(请从本工作台页面操作;本机工具需读 runtime/studio-token.txt 并随 x-wb-token 头发送)" }))
      return
    }
  }
  try {
    if (req.method === "GET" && url.pathname === "/") {
      // 未配置密钥时优先展示引导页;页面内嵌启动时随机 token(__WB_TOKEN__ 占位符注入)
      res.writeHead(200, { "content-type": "text/html; charset=utf-8" })
      res.end(((hasValidKey() || cliEngineReady()) ? PAGE : SETUP_PAGE).split("__WB_TOKEN__").join(API_TOKEN))
      return
    }
    if (req.method === "POST" && url.pathname === "/api/setup") {
      let body = ""
      for await (const ch of req) body += ch
      let b = {}
      try { b = JSON.parse(body) } catch {}
      const baseURL = String(b.baseURL || "").trim().replace(/\/+$/, "")
      const apiKey = String(b.apiKey || "").trim()
      const modelID = String(b.model || "").trim()
      const protocol = String(b.protocol || "openai").trim()
      if (!baseURL) {
        res.writeHead(400, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify({ ok: false, error: "请填写模型服务地址(Base URL)" }))
        return
      }
      if (!apiKey || PLACEHOLDER.test(apiKey)) {
        res.writeHead(400, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify({ ok: false, error: "请填写你的 API 密钥" }))
        return
      }
      if (!modelID) {
        res.writeHead(400, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify({ ok: false, error: "请填写模型名称(Model ID)" }))
        return
      }
      if (!/^https?:\/\//.test(baseURL)) {
        res.writeHead(400, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify({ ok: false, error: "API 地址要以 http(s):// 开头" }))
        return
      }
      const invalid = validateApiFields(baseURL, apiKey, modelID)
      if (invalid) {
        res.writeHead(400, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify({ ok: false, error: invalid }))
        return
      }
      // 保存前连接测试(20s):仅密钥确定错误(401/403)拦下;超时/限流放行并附警告
      const test = await testProvider({ baseURL, apiKey, modelID, protocol }, 20_000)
      if (isHardTestFail(test)) {
        res.writeHead(400, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify({ ok: false, error: `密钥被服务拒绝:${test.error}` }))
        return
      }
      const warning = test.ok
        ? (test.model && test.model !== modelID ? `连接成功(服务商实际使用模型:${test.model})` : "")
        : softWarning(test)
      try {
        // 写入多 API 存储 + 写引擎配置(引导页等价于“添加并启用第一套 API”)
        const id = "api_" + Date.now().toString(36)
        apis.items.push({ id, name: modelID, baseURL, apiKey, modelID, protocol })
        apis.enabledId = id
        saveApis()
        writeProvider({ baseURL, apiKey, modelID, protocol })
      } catch (e) {
        res.writeHead(500, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify({ ok: false, error: `写入配置失败: ${e.message}` }))
        return
      }
      config.model = `custom/${modelID}`
      // 后台带新配置重启引擎,应答立即返回(任务侧有 engineConfigStale 兜底)
      restartEngineBackground()
      res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
      res.end(JSON.stringify({ ok: true, warning, testModel: test.model || null }))
      return
    }
    if (req.method === "GET" && url.pathname === "/api/health") {
      res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
      res.end(JSON.stringify({
        engine: await engineHealthy(),
        engineError: engineLastError,
        model: config.model,
        workspace: config.workspace,
        neko: config.nekoRepo || null,
      }))
      return
    }
    // ---------- 会话管理 API ----------
    if (req.method === "GET" && url.pathname === "/api/sessions") {
      res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
      res.end(JSON.stringify({
        ok: true,
        current: sessions.current,
        items: sessions.items.map((s) => ({
          id: s.id,
          title: s.title,
          createdAt: s.createdAt,
          updatedAt: s.updatedAt,
          count: s.messages.length,
          archived: !!s.archived,
          tokens: s.totalTokens || 0,
        })),
      }))
      return
    }
    if (req.method === "POST" && url.pathname === "/api/sessions") {
      const s = newSession(null)
      res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
      res.end(JSON.stringify({ ok: true, current: sessions.current, session: { id: s.id, title: s.title, messages: [] } }))
      return
    }
    // ---------- 会话归档/取消归档 ----------
    let arm = url.pathname.match(/^\/api\/sessions\/([^/]+)\/archive$/)
    if (req.method === "POST" && arm) {
      let body = ""
      for await (const ch of req) body += ch
      let b = {}
      try { b = JSON.parse(body) } catch {}
      const s = getSession(arm[1])
      if (!s) { res.writeHead(404); res.end(JSON.stringify({ ok: false, error: "会话不存在" })); return }
      s.archived = !!b.archived
      // 归档的是当前会话:切换到第一个未归档会话;全部被归档则新建一个
      if (s.archived && sessions.current === s.id) {
        const active = sessions.items.find((x) => !x.archived)
        if (active) sessions.current = active.id
        else newSession(null)
      }
      saveSessions()
      res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
      res.end(JSON.stringify({ ok: true, id: s.id, archived: s.archived, current: sessions.current }))
      return
    }
    // ---------- 会话重命名(自定义命名) ----------
    let rm = url.pathname.match(/^\/api\/sessions\/([^/]+)\/rename$/)
    if (req.method === "POST" && rm) {
      let body = ""
      for await (const ch of req) body += ch
      let b = {}
      try { b = JSON.parse(body) } catch {}
      const title = String(b.title || "").trim().slice(0, 50)
      if (!title) {
        res.writeHead(400, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify({ ok: false, error: "会话名不能为空" }))
        return
      }
      const s = getSession(rm[1])
      if (!s) { res.writeHead(404); res.end(JSON.stringify({ ok: false, error: "会话不存在" })); return }
      s.title = title
      s.updatedAt = Date.now()
      saveSessions()
      res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
      res.end(JSON.stringify({ ok: true, id: s.id, title: s.title }))
      return
    }
    let m = url.pathname.match(/^\/api\/sessions\/([^/]+)$/)
    if (req.method === "DELETE" && m) {
      const ok = deleteSession(m[1])
      res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
      res.end(JSON.stringify({ ok, current: sessions.current }))
      return
    }
    if (req.method === "GET" && m) {
      const s = getSession(m[1])
      if (!s) { res.writeHead(404); res.end(JSON.stringify({ ok: false, error: "会话不存在" })); return }
      sessions.current = s.id
      saveSessions()
      res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
      res.end(JSON.stringify({ ok: true, current: s.id, session: { id: s.id, title: s.title, messages: s.messages } }))
      return
    }
    // ---------- API 配置管理(设置面板) ----------
    if (req.method === "GET" && url.pathname === "/api/apis") {
      res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
      // 密钥脱敏返回(只回尾 4 位,前端显示用)
      res.end(JSON.stringify({
        ok: true,
        enabledId: apis.enabledId,
        items: apis.items.map((a) => ({
          id: a.id, name: a.name, baseURL: a.baseURL, modelID: a.modelID,
          protocol: a.protocol || "openai",
          keyTail: a.apiKey ? a.apiKey.slice(-4) : "",
        })),
      }))
      return
    }
    if (req.method === "POST" && url.pathname === "/api/apis") {
      let body = ""
      for await (const ch of req) body += ch
      let b = {}
      try { b = JSON.parse(body) } catch {}
      const baseURL = String(b.baseURL || "").trim().replace(/\/+$/, "")
      const apiKey = String(b.apiKey || "").trim()
      const modelID = String(b.model || "").trim()
      const protocol = String(b.protocol || "openai").trim()
      const name = String(b.name || "").trim() || modelID
      const enable = b.enable !== false // 默认保存即启用
      if (!baseURL || !/^https?:\/\//.test(baseURL)) {
        res.writeHead(400, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify({ ok: false, error: "API 地址要以 http(s):// 开头" }))
        return
      }
      if (!apiKey) { res.writeHead(400, { "content-type": "application/json; charset=utf-8" }); res.end(JSON.stringify({ ok: false, error: "请填写 API 密钥" })); return }
      if (!modelID) { res.writeHead(400, { "content-type": "application/json; charset=utf-8" }); res.end(JSON.stringify({ ok: false, error: "请填写模型名称" })); return }
      const invalid = validateApiFields(baseURL, apiKey, modelID)
      if (invalid) { res.writeHead(400, { "content-type": "application/json; charset=utf-8" }); res.end(JSON.stringify({ ok: false, error: invalid })); return }
      const willEnable = enable || !apis.enabledId
      // 连接测试 12s:只有密钥确定错误(401/403)才拦下;超时/限流放行并附警告
      let warning = ""
      if (willEnable) {
        const test = await testProvider({ baseURL, apiKey, modelID, protocol }, 12_000)
        if (isHardTestFail(test)) {
          res.writeHead(400, { "content-type": "application/json; charset=utf-8" })
          res.end(JSON.stringify({ ok: false, error: `密钥被拒绝:${test.error}` }))
          return
        }
        warning = test.ok
          ? (test.model && test.model !== modelID ? `连接成功(服务商实际使用模型:${test.model})` : "")
          : softWarning(test)
      }
      const id = "api_" + Date.now().toString(36) + Math.random().toString(36).slice(2, 5)
      apis.items.push({ id, name, baseURL, apiKey, modelID, protocol })
      // "仅保存"不切换当前启用项;仅当一套都没有时才默认启用,避免新用户保存后还是没配置
      if (enable || !apis.enabledId) apis.enabledId = id
      if (apis.enabledId === id) applyEnabledApi()
      saveApis()
      if (apis.enabledId === id) restartEngineBackground()
      res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
      res.end(JSON.stringify({ ok: true, id, enabledId: apis.enabledId, warning }))
      return
    }
    let am = url.pathname.match(/^\/api\/apis\/([^/]+)$/)
    if (req.method === "DELETE" && am) {
      const i = apis.items.findIndex((a) => a.id === am[1])
      if (i >= 0) {
        apis.items.splice(i, 1)
        if (apis.enabledId === am[1]) {
          apis.enabledId = apis.items[0]?.id || null
          if (apis.enabledId) applyEnabledApi()
        }
        saveApis()
      }
      res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
      res.end(JSON.stringify({ ok: true, enabledId: apis.enabledId }))
      return
    }
    if (req.method === "POST" && am) {
      // 启用某套 API:立即切换并快速应答(连接测试 12s,软失败不拦),引擎后台带新配置重启
      const a = apis.items.find((x) => x.id === am[1])
      if (!a) { res.writeHead(404, { "content-type": "application/json; charset=utf-8" }); res.end(JSON.stringify({ ok: false, error: "配置不存在" })); return }
      const test = await testProvider({ baseURL: a.baseURL, apiKey: a.apiKey, modelID: a.modelID, protocol: a.protocol }, 12_000)
      if (isHardTestFail(test)) {
        res.writeHead(400, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify({ ok: false, error: `这套配置当前不可用:${test.error}(未切换,继续用原来的)` }))
        return
      }
      apis.enabledId = a.id
      saveApis()
      applyEnabledApi()
      restartEngineBackground()
      res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
      res.end(JSON.stringify({ ok: true, enabledId: apis.enabledId, warning: softWarning(test) }))
      return
    }
    // ---------- N.E.K.O 实测闭环 ----------
    const NEKO_BASE = config.nekoTestBase || "http://127.0.0.1:48916"
    const nekoAlive = async () => {
      try {
        const r = await fetch(`${NEKO_BASE}/plugins`, { signal: AbortSignal.timeout(8000) })
        return r.ok
      } catch { return false }
    }
    if (req.method === "GET" && url.pathname === "/api/neko/health") {
      res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
      res.end(JSON.stringify({ ok: true, alive: await nekoAlive(), base: NEKO_BASE }))
      return
    }
    if (req.method === "POST" && url.pathname === "/api/neko/install") {
      // {artifact: 绝对路径或工作区文件名} 安装/更新插件并启动
      let body = ""
      for await (const ch of req) body += ch
      let b = {}
      try { b = JSON.parse(body) } catch {}
      const art = String(b.artifact || "")
      const safe = normalize(basename(art))
      if (!/\.(neko-plugin|neko-bundle)$/.test(safe)) {
        res.writeHead(400, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify({ ok: false, error: "产物路径无效(需要 .neko-plugin)" }))
        return
      }
      // 只允许安装工作区内的产物:调用方给的绝对路径也不能指向工作区外(防本机任意文件被读走/安装)
      const wsRoot = resolve(config.workspace)
      const cand = resolve(wsRoot, art)
      const rel = relative(wsRoot, cand)
      if (rel.startsWith("..") || isAbsolute(rel)) {
        res.writeHead(403, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify({ ok: false, error: "产物必须位于工作区内,已拒绝工作区外路径" }))
        return
      }
      const full = existsSync(cand) ? cand : join(wsRoot, safe)
      if (!existsSync(full)) {
        res.writeHead(404, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify({ ok: false, error: `找不到产物文件:${safe}` }))
        return
      }
      if (!(await nekoAlive())) {
        res.writeHead(503, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify({ ok: false, error: "N.E.K.O 未在本机运行(插件服务 48916 不通)。请先启动 N.E.K.O 再实测" }))
        return
      }
      try {
        // plugin_id 从文件名取(xxx.neko-plugin -> xxx)
        const pluginId = safe.replace(/\.neko-(plugin|bundle)$/, "")
        // 已装同 id:先删(on_conflict 只支持 fail,更新走删+装)
        const lst = await (await fetch(`${NEKO_BASE}/plugins`, { signal: AbortSignal.timeout(15000) })).json()
        const ids = Array.isArray(lst) ? lst.map((p) => p.id || p.plugin_id || p) : (lst.plugins || []).map((p) => p.id || p.plugin_id)
        let removed = false
        if (ids.includes(pluginId)) {
          // 删除会触发插件注册表全量重扫(慢机器上约 30s),给足余量
          const del = await fetch(`${NEKO_BASE}/plugin/${pluginId}`, { method: "DELETE", signal: AbortSignal.timeout(120000) })
          removed = del.ok
        }
        // multipart 上传安装
        const buf = readFileSync(full)
        const fd = new FormData()
        fd.append("file", new Blob([buf]), safe)
        const up = await fetch(`${NEKO_BASE}/plugin-cli/upload-and-install?on_conflict=fail`, {
          method: "POST", body: fd, signal: AbortSignal.timeout(180000),
        })
        const upText = await up.text()
        if (!up.ok) {
          res.writeHead(502, { "content-type": "application/json; charset=utf-8" })
          res.end(JSON.stringify({ ok: false, error: `安装失败(HTTP ${up.status}):${upText.slice(0, 300)}`, pluginId, removedOld: removed }))
          return
        }
        // 启动插件(尽力而为;部分插件 auto_start 已自动跑)
        const st = await fetch(`${NEKO_BASE}/plugin/${pluginId}/start`, { method: "POST", signal: AbortSignal.timeout(60000) }).catch(() => null)
        res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify({
          ok: true, pluginId, artifact: safe,
          updated: removed,
          install: (() => { try { return JSON.parse(upText) } catch { return { raw: upText.slice(0, 200) } } })(),
          started: st ? st.ok : false,
        }))
      } catch (e) {
        res.writeHead(502, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify({ ok: false, error: `与 N.E.K.O 通信失败:${e.message}` }))
      }
      return
    }
    if (req.method === "POST" && url.pathname === "/api/neko/trigger") {
      // {pluginId, tool, args} -> 触发入口,返回插件真实输出
      // entry 型入口走 /runs 异步协议(与 N.E.K.O 主服 task_executor 同款),
      // register_llm_tool 型工具走 /api/llm-tools/callback
      let body = ""
      for await (const ch of req) body += ch
      let b = {}
      try { b = JSON.parse(body) } catch {}
      const pluginId = String(b.pluginId || "")
      const tool = String(b.tool || "")
      const args = b.args && typeof b.args === "object" ? b.args : {}
      if (!pluginId || !tool) {
        res.writeHead(400, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify({ ok: false, error: "需要 pluginId 和 tool" }))
        return
      }
      if (!(await nekoAlive())) {
        res.writeHead(503, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify({ ok: false, error: "N.E.K.O 未运行" }))
        return
      }
      try {
        // 查插件与入口清单(顺便把可用入口报给自修循环)
        const lst = await (await fetch(`${NEKO_BASE}/plugins`, { signal: AbortSignal.timeout(15000) })).json()
        const plugins = Array.isArray(lst) ? lst : (lst.plugins || [])
        const info = plugins.find((p) => String(p.id || p.plugin_id) === pluginId)
        if (!info) {
          res.writeHead(404, { "content-type": "application/json; charset=utf-8" })
          res.end(JSON.stringify({
            ok: false, pluginId, tool,
            error: `插件未安装:${pluginId}。已安装:${plugins.map((p) => p.id || p.plugin_id).join(", ") || "(无)"}`,
          }))
          return
        }
        const entryIds = (info.entries || []).map((e) => String(e.id))
        const hit = entryIds.find((id) => id === tool) || entryIds.find((id) => id.toLowerCase() === tool.toLowerCase())
        if (hit) {
          // entry 派发:POST /runs -> 轮询到终态 -> /runs/{id}/export 取结果
          const created = await fetch(`${NEKO_BASE}/runs`, {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ plugin_id: pluginId, entry_id: hit, args, task_id: `wb_${Date.now()}` }),
            signal: AbortSignal.timeout(15000),
          })
          const ct = await created.json().catch(() => ({}))
          if (!created.ok || !ct.run_id) {
            res.writeHead(502, { "content-type": "application/json; charset=utf-8" })
            res.end(JSON.stringify({
              ok: false, pluginId, tool,
              error: `创建运行失败(HTTP ${created.status}):${JSON.stringify(ct).slice(0, 300)}`,
            }))
            return
          }
          const runId = String(ct.run_id)
          const terminal = new Set(["succeeded", "failed", "canceled", "timeout"])
          const deadline = Date.now() + 120000
          let rec = null
          let pollErrs = 0
          while (Date.now() < deadline) {
            const rr = await fetch(`${NEKO_BASE}/runs/${runId}`, { signal: AbortSignal.timeout(8000) }).catch(() => null)
            if (rr && rr.ok) {
              pollErrs = 0
              rec = await rr.json().catch(() => null)
              if (rec && terminal.has(rec.status)) break
            } else if (++pollErrs >= 5) {
              break
            }
            await new Promise((r) => setTimeout(r, 500))
          }
          const status = (rec && rec.status) || "timeout"
          let result = null, meta = null, err = null
          if (rec && rec.error) {
            err = typeof rec.error === "object" ? String(rec.error.message || rec.error.code || "运行失败") : String(rec.error)
          }
          try {
            const ex = await (await fetch(`${NEKO_BASE}/runs/${runId}/export?limit=50`, { signal: AbortSignal.timeout(10000) })).json()
            const items = (ex && ex.items) || []
            for (const it of items) {
              if (!it || typeof it !== "object") continue
              const raw = it.json ?? it.json_data
              if (it.type === "json" && raw != null) {
                if (typeof raw === "object") {
                  result = raw.data ?? raw
                  meta = raw.meta ?? null
                  if (raw.error) err = typeof raw.error === "object" ? String(raw.error.message || JSON.stringify(raw.error)) : String(raw.error)
                } else {
                  result = raw
                }
                break
              }
              if (it.type === "text" && result == null && it.text != null) result = it.text
            }
          } catch {}
          res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
          res.end(JSON.stringify({
            ok: status === "succeeded" && !err, pluginId, tool,
            runId, runStatus: status, result, meta,
            error: status === "succeeded" && !err ? null : (err || `运行状态:${status}`),
          }))
          return
        }
        // 无同名 entry:按 register_llm_tool 型工具走 callback
        const r = await fetch(`${NEKO_BASE}/api/llm-tools/callback/${encodeURIComponent(pluginId)}/${encodeURIComponent(tool)}`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ name: tool, arguments: args, call_id: `wb_${Date.now()}`, raw_arguments: JSON.stringify(args) }),
          signal: AbortSignal.timeout(60000),
        })
        const j = await r.json().catch(() => ({ output: null, is_error: true, error: "响应解析失败" }))
        if (r.status === 404 || j.error === "TOOL_NOT_REGISTERED") {
          res.writeHead(404, { "content-type": "application/json; charset=utf-8" })
          res.end(JSON.stringify({
            ok: false, pluginId, tool,
            error: `入口不存在:${tool}。该插件可用入口:${entryIds.length ? entryIds.join(", ") : "(无可触发入口,仅生命周期/定时运行)"}`,
          }))
          return
        }
        res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify({ ok: !(j.is_error), pluginId, tool, result: j.output ?? j, error: j.is_error ? String(j.error || "插件返回错误") : null }))
      } catch (e) {
        res.writeHead(502, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify({ ok: false, error: `触发失败:${e.message}` }))
      }
      return
    }
    // ---------- 严格校验开关 ----------
    if (url.pathname === "/api/strict") {
      if (req.method === "GET") {
        res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify({ ok: true, strictVerify: !!config.strictVerify }))
        return
      }
      if (req.method === "POST") {
        let body = ""
        for await (const ch of req) body += ch
        let b = {}
        try { b = JSON.parse(body) } catch {}
        config.strictVerify = !!b.strictVerify
        saveUserSettings()
        // 引擎子进程环境跟随:重启后台生效(任务侧有 engineConfigStale 兜底)
        engineConfigStale = true
        res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify({ ok: true, strictVerify: config.strictVerify }))
        return
      }
    }
    // ---------- 上下文容量(纯显示设置,不动引擎) ----------
    if (url.pathname === "/api/context") {
      if (req.method === "GET") {
        res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify({ ok: true, contextLimit: config.contextLimit }))
        return
      }
      if (req.method === "POST") {
        let body = ""
        for await (const ch of req) body += ch
        let b = {}
        try { b = JSON.parse(body) } catch {}
        const v = Math.floor(Number(b.contextLimit))
        if (!Number.isFinite(v) || v < 1000 || v > 10_000_000) {
          res.writeHead(400, { "content-type": "application/json; charset=utf-8" })
          res.end(JSON.stringify({ ok: false, error: "上下文容量要在 1000 ~ 10000000 tokens 之间" }))
          return
        }
        config.contextLimit = v
        saveUserSettings()
        res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify({ ok: true, contextLimit: config.contextLimit }))
        return
      }
    }
    if (req.method === "POST" && url.pathname === "/api/stop") {
      // 停止当前任务:中断引擎会话并让 driveAgent 等待循环尽快退出
      if (currentAbort) currentAbort.abort()
      res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
      res.end(JSON.stringify({ ok: true }))
      return
    }
    // ---------- Markdown 导入/导出 ----------
    if (req.method === "POST" && url.pathname === "/api/import") {
      let body = ""
      for await (const ch of req) body += ch
      let b = {}
      try { b = JSON.parse(body) } catch {}
      const name = String(b.name || "").replace(/\.md$/i, "").trim() || "导入的文档"
      const md = String(b.content || "")
      if (!md.trim()) { res.writeHead(400, { "content-type": "application/json; charset=utf-8" }); res.end(JSON.stringify({ ok: false, error: "Markdown 内容是空的" })); return }
      const s = newSession(null)
      s.title = name.slice(0, 30)
      s.messages.push({ role: "user", content: `请根据以下 Markdown 文档继续(文档名:${name}):`, at: Date.now() })
      s.messages.push({ role: "assistant", content: md, at: Date.now() })
      saveSessions()
      res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
      res.end(JSON.stringify({ ok: true, current: sessions.current, session: { id: s.id, title: s.title, messages: s.messages } }))
      return
    }
    let em = url.pathname.match(/^\/api\/sessions\/([^/]+)\/export$/)
    if (req.method === "GET" && em) {
      const s = getSession(em[1])
      if (!s) { res.writeHead(404); res.end(JSON.stringify({ ok: false, error: "会话不存在" })); return }
      let md = `# ${s.title}\n\n> 导出自 N.E.K.O. 插件工坊 · ${new Date().toLocaleString("zh-CN")}\n\n`
      for (const msg of s.messages) {
        if (msg.role === "user") md += `## 🐱 我\n\n${msg.content}\n\n`
        else if (msg.error && !msg.content) md += `## 🤖 猫咪\n\n> ⚠ ${msg.error}\n\n`
        else {
          md += `## 🤖 猫咪\n\n${msg.content}\n\n`
          if (msg.artifacts && msg.artifacts.length) {
            md += `产物:${msg.artifacts.map((a) => `\`${String(a).split("\\").pop()}\``).join(", ")}\n\n`
          }
        }
      }
      const fname = (s.title || "会话").replace(/[\\/:*?"<>|]/g, "_").slice(0, 40) + ".md"
      res.writeHead(200, {
        "content-type": "text/markdown; charset=utf-8",
        "content-disposition": `attachment; filename*=UTF-8''${encodeURIComponent(fname)}`,
      })
      res.end(md)
      return
    }
    // ---------- 实时思考过程(任务进行中,前端每 2s 轮询) ----------
    if (req.method === "GET" && url.pathname === "/api/thinking") {
      const sid = url.searchParams.get("sid") || ""
      const since = Number(url.searchParams.get("since") || 0)
      const s = getSession(sid)
      if (!s || !s.engineSessionId || !engineAuth) {
        res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify({ ok: true, thinking: "" }))
        return
      }
      try {
        const r = await fetch(`${config.server}/api/session/${s.engineSessionId}/message`, {
          headers: { authorization: "Basic " + Buffer.from(engineAuth).toString("base64") },
          signal: AbortSignal.timeout(5000),
        })
        const j = await r.json()
        const list = (j?.data || []).filter((m) => m.type === "assistant" && (m.time?.created || 0) >= since - 1000)
        list.sort((a, b) => (a.time?.created || 0) - (b.time?.created || 0))
        const thinkAll = []
        for (const m of list) {
          const tp = (m.content || []).filter((c) => (c.type === "reasoning" || c.type === "thinking") && c.text).map((c) => c.text)
          if (tp.length) thinkAll.push(tp.join("\n\n"))
        }
        res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify({ ok: true, thinking: thinkAll.join("\n\n") }))
      } catch {
        res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify({ ok: true, thinking: "" }))
      }
      return
    }
    if (req.method === "POST" && url.pathname === "/api/task") {
      if (!hasValidKey() && !cliEngineReady()) {
        res.writeHead(400, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify({ ok: false, error: "请先完成模型服务配置(刷新页面填写密钥)" }))
        return
      }
      if (busy) { res.writeHead(429); res.end(JSON.stringify({ ok: false, error: "上一个任务还没完成,稍等一下" })); return }
      let body = ""
      for await (const ch of req) body += ch
      let text = "", sid = null, mode = "build"
      try { const j = JSON.parse(body); text = String(j.text || "").trim(); sid = j.sessionId || null; mode = j.mode === "plan" ? "plan" : "build" } catch {}
      if (!text) { res.writeHead(400); res.end(JSON.stringify({ ok: false, error: "需求内容是空的" })); return }
      let s = sid ? getSession(sid) : null
      if (!s) s = getSession(sessions.current) || newSession(null)
      // 给已归档会话发消息 = 恢复它
      if (s.archived) { s.archived = false; saveSessions() }
      busy = true
      currentAbort = new AbortController()
      // 引擎毒化自愈:上次任务异常(看门狗中止/模型不可用/连接失败)后,引擎进程可能拒绝一切模型调用
      // (实测:不重启则后续任务全部静默挂起)。任务前先重启恢复;配置刚切换未生效时同样先重启
      let unhealthy = enginePoisoned || engineConfigStale
      if (!unhealthy && !(await engineHealthy())) {
        await new Promise((r) => setTimeout(r, 1000))
        unhealthy = !(await engineHealthy()) // 探活两次再下结论,避免误杀正忙但健康的引擎
      }
      if (unhealthy) {
        enginePoisoned = false
        engineConfigStale = false
        console.log("[wb-studio] 引擎状态异常,任务前自动重启引擎…")
        stopEngine()
        await new Promise((r) => setTimeout(r, 1500))
        const eng = await ensureEngine()
        if (eng && eng.error) {
          // 引擎起不来就明说原因,绝不能放行去连死引擎(否则用户只看到"Unable to connect"莫名其妙)
          busy = false
          currentAbort = null
          res.writeHead(503, { "content-type": "application/json; charset=utf-8" })
          res.end(JSON.stringify({ ok: false, error: `引擎启动失败:${eng.error}${engineLastError && !String(eng.error).includes(engineLastError) ? `(${engineLastError})` : ""}` }))
          return
        }
      }
      const driveOpts = {
        text, dir: config.workspace, model: config.model,
        server: config.server, timeout: config.timeoutSec,
        noProgressSec: config.stallTimeoutSec,
        auth: engineAuth || undefined,
        sessionID: s.engineSessionId || undefined,
        mode,
        signal: currentAbort.signal,
      }
      if (cliEngineReady()) {
        // CLI 底座:整段任务交给外部 Agent CLI(凭据自理),无 SSE/毒化恢复路径
        let result
        try {
          result = await driveAgentCli({
            text, dir: config.workspace, cmd: cliEngineCmd(), nekoRepo: config.nekoRepo || undefined,
            timeoutSec: config.timeoutSec, signal: currentAbort.signal,
            sessionID: s.engineSessionId || undefined,
          })
        } finally {
          busy = false
          currentAbort = null
        }
        s.engineSessionId = result.sessionId || s.engineSessionId
        s.updatedAt = Date.now()
        s.messages.push({ role: "user", content: text, at: Date.now(), mode })
        if (result.reply) {
          s.messages.push({
            role: "assistant",
            content: result.reply,
            at: Date.now(),
            error: result.ok ? undefined : String(result.error || ""),
            artifacts: result.artifacts || [],
            modifiedFiles: result.modifiedFiles || [],
          })
        } else {
          s.messages.push({ role: "assistant", content: "", at: Date.now(), error: String(result.error || "") })
        }
        if (s.title === "新对话" && text) {
          s.title = text.replace(/\s+/g, " ").slice(0, 18) + (text.length > 18 ? "…" : "")
        }
        saveSessions()
        result.sessionId = s.id
        res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify(result))
        return
      }
      try {
        let result
        try {
          result = await driveAgent(driveOpts)
        } catch (e) {
          // 引擎半死(连接失败/会话创建失败等):自动重启后重试一次
          console.error("[wb-studio] 任务异常,自动重启引擎并重试一次:", e?.message)
          stopEngine()
          await new Promise((r) => setTimeout(r, 1500))
          const started = await ensureEngine()
          if (!started.started && started.error) {
            // 重启也失败:带上真实死因报错,不再把原始连接错误甩给用户
            throw new Error(`${String(e?.message || e)}(引擎重启也失败:${started.error})`)
          }
          result = await driveAgent(driveOpts)
        }
        // 看门狗中止/模型不可用/模型静默挂起都会毒化引擎:重启自愈(同步完成后才应答,
        // 避免用户立刻重试时撞上引擎重启中途)
        if (!result.ok && (result.stopped || /模型服务已|ModelUnavailable|等待超时|没有生成新回复/.test(String(result.error || "")))) {
          console.log("[wb-studio] 检测到引擎毒化特征,自动重启恢复…")
          enginePoisoned = false
          stopEngine()
          await new Promise((r) => setTimeout(r, 1500))
          await ensureEngine()
        }
        // 回写会话记忆(引擎会话 id + 双方消息 + 产物),实时落盘
        s.engineSessionId = result.sessionId || s.engineSessionId
        s.updatedAt = Date.now()
        s.messages.push({ role: "user", content: text, at: Date.now(), mode })
        // token 用量:本轮累计 + 会话累计(即使任务失败,已消耗的也要记账)
        const u = result.usage || {}
        const turnTotal = (u.input || 0) + (u.output || 0) + (u.reasoning || 0)
        if (turnTotal > 0) s.totalTokens = (s.totalTokens || 0) + turnTotal
        if (result.reply) {
          s.messages.push({
            role: "assistant",
            content: result.reply,
            at: Date.now(),
            thinking: result.thinking || undefined,
            usage: turnTotal > 0 ? u : undefined,
            error: result.ok ? undefined : String(result.error || ""),
            artifacts: result.artifacts || [],
            modifiedFiles: result.modifiedFiles || [],
          })
        } else if (result.error) {
          s.messages.push({ role: "assistant", content: "", at: Date.now(), error: String(result.error), usage: turnTotal > 0 ? u : undefined })
        }
        // 首条用户消息截断为标题
        if (s.title === "新对话" && text) {
          s.title = text.replace(/\s+/g, " ").slice(0, 18) + (text.length > 18 ? "…" : "")
        }
        saveSessions()
        if (!result.ok) {
          const raw = String(result.error || "")
          if (result.stopped) {
            result.error = "任务已停止"
          } else if (/401|403|api key|apikey|unauthorized|invalid/i.test(raw)) {
            result.error = "模型服务拒绝访问:密钥可能不对或已过期,请刷新页面重新配置"
          } else if (/quota|429|rate.?limit|tpm|rpm/i.test(raw)) {
            // 优先把服务商原文里的补救信息(如额度重置时间)带给用户,别用笼统话术盖掉
            let hint = ""
            try {
              const m = raw.match(/"message"\s*:\s*"([^"]{1,240})"/)
              if (m) hint = `:${m[1]}`
            } catch {}
            result.error = `模型服务限流/额度不足${hint || ":等几分钟再试,或检查账户额度"}。也可到设置里切换其他 API 继续`
          } else if (/Unable to connect|fetch failed|ECONNREFUSED/i.test(raw)) {
            result.error = "网络连接失败:本地引擎或模型服务连不上。检查网络(或加速器)后重试"
          } else if (!raw.trim()) {
            result.error = result.reply ? "任务中断,见下方猫咪留言" : "未知错误"
          }
          result.sessionId = s.id
        }
        result.sessionId = s.id // 网页层拿到的统一是工坊会话 id
        res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify(result))
      } finally {
        busy = false
        currentAbort = null
      }
      return
    }
    if (req.method === "GET" && url.pathname === "/api/download") {
      const f = url.searchParams.get("f") || ""
      const safe = normalize(basename(f))
      if (!/\.(neko-plugin|neko-bundle)$/.test(safe)) { res.writeHead(400); res.end("bad file"); return }
      const full = join(config.workspace, safe)
      if (!existsSync(full)) { res.writeHead(404); res.end("not found"); return }
      res.writeHead(200, {
        "content-type": "application/octet-stream",
        "content-disposition": `attachment; filename="${encodeURIComponent(safe)}"`,
      })
      createReadStream(full).pipe(res)
      return
    }
    res.writeHead(404); res.end("not found")
  } catch (e) {
    try { res.writeHead(500); res.end(JSON.stringify({ ok: false, error: String(e?.message || e) })) } catch {}
  }
})

// ---------- 启动 ----------
const boot = async () => {
  // 已有活的工坊在跑?说明是重复启动:不再起第二个服务(start.cmd 会直接给它开窗口),本实例退出。
  // 探工坊自己的端口:以前探引擎,结果残留的孤儿引擎把新实例误判成"已在运行"直接退出,重启变哑巴
  try {
    const h = await fetch(`http://127.0.0.1:${config.port}/api/health`, { signal: AbortSignal.timeout(1500) })
    if (h.ok) {
      console.log("[wb-studio] 检测到工坊已在运行,本次启动退出")
      process.exit(0)
    }
  } catch {}
  // 上次异常退出(断电/被强杀)留下的死锁:服务已死但锁还在,start.cmd 会误以为在跑。
  // 走到这里说明 5099 没有活服务,锁必是残留,清掉
  try { if (existsSync(STUDIO_LOCK)) require("node:fs").unlinkSync(STUDIO_LOCK) } catch {}
  // CLI 底座:把 nekoRepo 透传给底座进程(wb-plugin CLI 依赖 WB_NEKO_REPO)
  if (config.nekoRepo) process.env.WB_NEKO_REPO = config.nekoRepo
  // 引擎全局 AGENTS.md:Agent 作用范围=全局,在任何目录干活都带这份身份与规则
  try {
    require("node:fs").mkdirSync(ENGINE_CONFIG_DIR, { recursive: true })
    const gAgents = join(ENGINE_CONFIG_DIR, "AGENTS.md")
    if (!existsSync(gAgents)) {
      require("node:fs").writeFileSync(gAgents, GLOBAL_AGENTS_MD, "utf8")
    }
  } catch {}
  // 便携 Python:包内自带,加入 PATH 供 Agent 用 wb.cmd 跑 N.E.K.O check/build
  if (config.pythonPath && existsSync(config.pythonPath)) {
    const pyDir = dirname(config.pythonPath)
    process.env.Path = `${pyDir};${process.env.Path || ""}`
    process.env.PYTHONPATH = join(__dirname, "_site")
    console.log(`[wb-studio] 便携 Python: ${pyDir}`)
  }
  // 文件日志(内置窗口模式下控制台不可见,日志落在 runtime\studio.log)
  try {
    const { mkdirSync } = require("node:fs")
    mkdirSync(join(__dirname, "runtime"), { recursive: true })
    appendFileSync(join(__dirname, "runtime", "studio.log"), `[${new Date().toISOString()}] studio 启动 pid=${process.pid}\n`)
    process.on("exit", (code) => {
      try { appendFileSync(join(__dirname, "runtime", "studio.log"), `[${new Date().toISOString()}] studio 退出 code=${code}\n`) } catch {}
    })
  } catch {}
  console.log(`[wb-studio] 引擎检查中 (${config.server}) ...`)
  const engine = await ensureEngine()
  if (engine.error) {
    engineLastError = engine.error
    console.error(`[wb-studio] ⚠ ${engine.error}(仍将启动网页,任务会失败)`)
  }
  srv.listen(config.port, "127.0.0.1", () => {
    const url = `http://127.0.0.1:${config.port}`
    console.log(`[wb-studio] 对话页: ${url}`)
    console.log(`[wb-studio] 工作区: ${config.workspace}`)
    if (config.openBrowser && !process.env.WB_STUDIO_NOWINDOW) openWorkshopWindow(url)
  })
}

// ---------- 内置窗口(Edge/Chrome --app 模式)与退出管理 ----------
const STUDIO_LOCK = join(__dirname, "runtime", "studio.lock")

function findChromium() {
  const candidates = [
    "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
    join(process.env.LOCALAPPDATA || "", "Microsoft", "Edge", "Application", "msedge.exe"),
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
    join(process.env.LOCALAPPDATA || "", "Google", "Chrome", "Application", "chrome.exe"),
  ]
  for (const c of candidates) { if (c && existsSync(c)) return c }
  return null
}

function openWorkshopWindow(url) {
  const exe = findChromium()
  const [w, h] = String(config.appWindow || "1200x860").split("x")
  // 写 lock(含 chromium 路径):start.cmd 二次启动时据此重开窗口而不是再起一个 studio
  try {
    const { writeFileSync } = require("node:fs")
    writeFileSync(STUDIO_LOCK, exe || "", "utf8")
  } catch {}
  if (exe && config.openMode !== "browser") {
    // 专属配置目录:与用户日常浏览器完全隔离,可并存
    const profile = join(__dirname, "runtime", "edge-profile")
    const args = [
      `--user-data-dir=${profile}`,
      `--app=${url}`,
      `--window-size=${w || 1200},${h || 860}`,
      "--no-first-run", "--no-default-browser-check",
      "--disable-features=Translate,msSmartScreenPrompt",
    ]
    console.log(`[wb-studio] 内置窗口: ${exe.includes("msedge") ? "Edge" : "Chrome"}`)
    const child = spawn(exe, args, { stdio: "ignore", detached: false })
    // 关窗即安全退出:用户关掉工坊窗口 = 停引擎 + 释放端口 + 收干净子进程
    // 若窗口启动后 8 秒内就异常退出(闪退/profile 锁),视为启动失败,不触发自杀
    let windowSeenAlive = false
    const aliveTimer = setTimeout(() => { windowSeenAlive = true }, 8000)
    child.on("exit", () => {
      clearTimeout(aliveTimer)
      if (!windowSeenAlive) {
        console.log("[wb-studio] 内置窗口启动失败(可能已有同配置实例),改用系统浏览器")
        spawn("cmd", ["/c", "start", "", url], { stdio: "ignore", shell: false })
        return
      }
      console.log("[wb-studio] 工坊窗口已关闭,正在安全退出…")
      try { if (existsSync(STUDIO_LOCK)) require("node:fs").unlinkSync(STUDIO_LOCK) } catch {}
      stopEngine()
      process.exit(0)
    })
  } else {
    // 回退:系统浏览器(不随窗口关闭退出,工坊留在后台)
    spawn("cmd", ["/c", "start", "", url], { stdio: "ignore", shell: false })
  }
}
boot()
