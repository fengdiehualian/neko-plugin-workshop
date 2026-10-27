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
import { driveAgent, driveAgentCli, planModelRetry, formatUserMemory, buildWishPrompt } from "./wb-agent-lib.mjs"

const __dirname = dirname(fileURLToPath(import.meta.url))

// 当前版本:用于内置更新比较(打包后 package.json 就在根目录)
const APP_VERSION = (() => {
  try { return JSON.parse(readFileSync(join(__dirname, "package.json"), "utf8")).version || "0.0.0" } catch { return "0.0.0" }
})()

// 统一的 JSON 原子落盘:先写临时文件再 rename。
// 直接 writeFileSync 覆盖,遇到断电/强杀会留下半截 JSON,下次启动整份配置或会话就读废了。
function writeJsonAtomic(file, obj) {
  try {
    const { mkdirSync, writeFileSync, renameSync } = require("node:fs")
    mkdirSync(dirname(file), { recursive: true })
    const tmp = `${file}.tmp`
    writeFileSync(tmp, JSON.stringify(obj, null, 2), "utf8")
    renameSync(tmp, file)
  } catch (e) {
    console.error(`[wb-studio] 落盘失败(${basename(file)}):`, e.message)
  }
}

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
  retryMax: 2, // 模型无响应自动重试次数(0=关闭);只在「没有任何产出」时重试
  retryDelaySec: 3, // 首次退避秒数,之后翻倍(3s → 6s → 12s,上限 30s)
  strictVerify: false, // 严格校验:warning 一律视为失败(wb.cmd 自动加 --strict,须修到零警告)
  contextLimit: 128000, // 上下文容量(tokens):模型上下文窗口大小,用于底部容量显示
  uiFont: "default", // 界面字体预设 id(见页面 FONT_PRESETS)
  uiFontSize: 100, // 界面字号百分比(85~130),用 CSS zoom 整体缩放
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

// ---------- 许愿式派工:把小白的裸愿望包装成结构化任务书 ----------
// 小白只会说大白话愿望;裸愿望直接丢给 Agent 会漏掉流程(选模板/scaffold/verify/汇报格式)。
// 这里做确定性的「愿望→任务书」包装:零额外模型调用、零延迟。
// Build 模式:直接开工(有歧义选最常见理解,绝不反问)。
// Plan 模式:引导式提问——一次一个小问题(附可点候选答案),追到真的懂了才出方案,最多 3 问。
// 会话里存的仍是用户原话(text),任务书只给引擎看。

// 统计本会话「当前访谈」已问几个问题:从末尾往前数连续的 [[WB-Q]] 提问,
// 一旦遇到不带提问标记的助手消息(方案/普通回复),说明访谈已结束,计数清零
function planAskCount(session) {
  let n = 0
  const ms = (session && session.messages) || []
  for (let i = ms.length - 1; i >= 0; i--) {
    const m = ms[i]
    if (m.role === "assistant" && /\[\[WB-Q\]\]/.test(String(m.content || ""))) n++
    else if (m.role === "assistant") break
  }
  return n
}

// ---------- 上下文自动压缩 ----------
// 引擎侧会话会一直累积历史,窗口塞满后要么被服务商拒绝(context_length_exceeded),
// 要么悄悄截断导致"忘了前面说过啥"。这里在接近上限时把较早的对话压成摘要:
//   摘要注入下一轮提示词 + 重置引擎会话 id,既腾出空间又不丢关键信息。
const COMPACT_RATIO = Math.min(0.95, Math.max(0.3, Number(process.env.WB_COMPACT_RATIO) || 0.75))
const COMPACT_KEEP_RECENT = 4 // 最近的 N 条原文保留,不压进摘要
const COMPACT_SUMMARY_MAX = 1800 // 摘要字符上限(约 1.2k tokens)

/** 粗略 token 估算:中文按 1 字 1 token,英文数字按 3.5 字符 1 token */
function estTokens(text) {
  const s = String(text || "")
  if (!s) return 0
  const cjk = (s.match(/[\u4e00-\u9fff]/g) || []).length
  return Math.ceil(cjk + (s.length - cjk) / 3.5)
}

/** 会话已用上下文:优先最近一轮引擎回传的 usage,否则按"压缩后真正还要带上的内容"估算 */
function sessionTokens(s) {
  const ms = (s && s.messages) || []
  const from = (s && s.summarizedUpTo) || 0 // 已压进摘要的部分不再计入(引擎也不会再看到它们)
  for (let i = ms.length - 1; i >= from; i--) {
    const u = ms[i].usage
    if (u && ((u.input || 0) + (u.output || 0)) > 0) return (u.input || 0) + (u.output || 0) + (u.reasoning || 0)
  }
  let n = s && s.summary ? estTokens(s.summary) : 0
  for (let i = from; i < ms.length; i++) n += estTokens(ms[i].content) + 8
  return n
}

/** 规则摘要:不依赖模型,保证"用户要过什么、做出来了什么"这类硬信息一定留下 */
function ruleDigest(s, oldMsgs) {
  const wishes = []
  const notes = []
  const files = new Set()
  const arts = new Set()
  for (const m of oldMsgs) {
    const t = String(m.content || "").replace(/\s+/g, " ").trim()
    if (m.role === "user") {
      if (t) wishes.push(t.length > 150 ? t.slice(0, 150) + "…" : t)
    } else if (t) {
      if (t.length > 30 && notes.length < 3) notes.push(t.length > 480 ? t.slice(0, 480) + "…" : t)
      for (const f of m.modifiedFiles || []) files.add(String(f))
      for (const a of m.artifacts || []) arts.add(String(a).split(/[\\/]/).pop())
    }
  }
  const uniq = [...new Set(wishes)].slice(-10)
  const lines = []
  if (s && s.summary) lines.push("此前已压缩的摘要:\n" + s.summary)
  if (uniq.length) lines.push("用户先后提出(按时间):\n" + uniq.map((w, i) => `${i + 1}. ${w}`).join("\n"))
  if (notes.length) lines.push("助手最近的关键结论:\n- " + notes.join("\n- "))
  if (arts.size) lines.push("已生成的产物:" + [...arts].slice(0, 8).join("、"))
  if (files.size) lines.push("已修改的文件:" + [...files].slice(0, 12).join("、"))
  const out = lines.join("\n\n")
  return out.length > COMPACT_SUMMARY_MAX ? out.slice(0, COMPACT_SUMMARY_MAX) + "…" : out
}

/** 一次非流式对话(只给摘要这种小任务用);任何异常都返回 null,绝不打断主流程 */
async function chatOnce(messages, maxTokens = 900, timeoutMs = 10000) {
  const a = enabledApi()
  if (!a) return null
  const protocol = a.protocol || "openai"
  const proto = protocolInfo(protocol)
  const key = a.apiKey
  const modelID = a.modelID
  let url, headers, body
  if (protocol === "anthropic") {
    const base = (a.baseURL || "https://api.anthropic.com").replace(/\/+$/, "").replace(/\/v1$/, "")
    url = `${base}/v1/messages`
    headers = { "content-type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01" }
    body = { model: modelID, max_tokens: maxTokens, system: COMPACT_SYSTEM_PROMPT, messages }
  } else if (protocol === "google") {
    const base = (a.baseURL || "https://generativelanguage.googleapis.com").replace(/\/+$/, "")
    url = `${base}/v1beta/models/${encodeURIComponent(modelID)}:generateContent?key=${encodeURIComponent(key)}`
    headers = { "content-type": "application/json" }
    body = {
      systemInstruction: { parts: [{ text: COMPACT_SYSTEM_PROMPT }] },
      contents: messages.map((m) => ({ role: m.role === "assistant" ? "model" : "user", parts: [{ text: m.content }] })),
      generationConfig: { maxOutputTokens: maxTokens, temperature: 0.2 },
    }
  } else {
    const base = (a.baseURL || proto.defaultBaseURL || "").replace(/\/+$/, "")
    url = `${base}/chat/completions`
    headers = { "content-type": "application/json", authorization: `Bearer ${key}` }
    body = { model: modelID, max_tokens: maxTokens, temperature: 0.2, messages: [{ role: "system", content: COMPACT_SYSTEM_PROMPT }, ...messages] }
  }
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), timeoutMs)
  try {
    const res = await fetch(url, { method: "POST", signal: ctrl.signal, headers, body: JSON.stringify(body) })
    if (!res.ok) return null
    const j = await res.json()
    return j?.choices?.[0]?.message?.content || j?.content?.[0]?.text || j?.candidates?.[0]?.content?.parts?.[0]?.text || null
  } catch {
    return null
  } finally {
    clearTimeout(timer)
  }
}

const COMPACT_SYSTEM_PROMPT = [
  "你在为一个「插件开发助手」的会话做记忆压缩。用户是零基础小白。",
  "请把给出的对话记录压成一段中文摘要,要求:",
  "1. 必须保留:用户想要什么插件、功能与触发方式、已经确认的选择、插件名称与产物文件、还没做完的事、任何数字/时间/名称约定。",
  "2. 丢弃:寒暄、重复解释、反复试错的中间过程(只留结论)。",
  "3. 用短段落或要点列表,不超过 600 字,只写记录里真实出现的内容,不要编造。",
  "4. 直接输出摘要正文,不要写「以下是摘要」之类的开场白。",
].join("\n")

/** 把会话较早的消息压成摘要;返回 null 表示没到阈值,不需要压缩 */
async function compactSession(s, { force = false, reason = "auto" } = {}) {
  const limit = effectiveContext().effectiveLimit || config.contextLimit
  const used = sessionTokens(s)
  const ratio = limit > 0 ? used / limit : 0
  if (!force && ratio < COMPACT_RATIO) return null
  const ms = s.messages || []
  const already = s.summarizedUpTo || 0
  let cut = ms.length - COMPACT_KEEP_RECENT
  if (force && cut <= already) cut = ms.length - 1 // 手动压缩:至少压一条,留最新一条
  if (cut <= already) return null
  const oldMsgs = ms.slice(already, cut)
  if (!oldMsgs.length) return null
  const base = ruleDigest(s, oldMsgs)
  if (!base.trim()) return null
  // 先让模型润色;失败/超时就退回规则摘要(压缩本身绝不能成为新的故障点)
  let summary = base
  let source = "rule"
  const transcript = oldMsgs
    .map((m) => `${m.role === "user" ? "用户" : "助手"}:${String(m.content || "").replace(/\s+/g, " ").slice(0, 700)}`)
    .join("\n")
    .slice(0, 8000)
  const polished = await chatOnce([
    { role: "user", content: `已有摘要(可能为空,请合并):\n${s.summary || "(无)"}\n\n对话记录:\n${transcript}` },
  ])
  if (polished && polished.trim().length >= 40) {
    summary = polished.trim().slice(0, COMPACT_SUMMARY_MAX)
    source = "model"
  }
  s.summary = summary
  s.summarySource = source
  s.summaryAt = Date.now()
  s.summarizedUpTo = cut
  s.compactCount = (s.compactCount || 0) + 1
  // 重置引擎会话:旧会话在引擎侧已经很大,继续用它等于没压缩;摘要已通过提示词续上
  s.engineSessionId = null
  s.lastCompact = { reason, before: used, after: estTokens(summary), ratio: Number(ratio.toFixed(2)), at: s.summaryAt }
  saveSessions()
  console.log(`[wb-studio] 会话已压缩(${reason}): ${used} → ~${estTokens(summary)} tokens,来源=${source}`)
  return s.lastCompact
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

// ---------- 常见服务商预设(小白一键填) ----------
// 只填服务商地址——模型 ID 不代填(各人账号可用模型不同,填错反被连接测试拦下);
// 模型名照官网文档填,协议保持默认(OpenAI 兼容)即可,页面有引导
// 地址/协议是各服务商公开稳定值;keyUrl 供用户去官网领密钥、查模型名
const PROVIDER_PRESETS = [
  { id: "deepseek", label: "DeepSeek", protocol: "openai", baseURL: "https://api.deepseek.com/v1", keyUrl: "https://platform.deepseek.com/api_keys", keyHost: "platform.deepseek.com" },
  { id: "zhipu", label: "智谱 GLM", protocol: "openai", baseURL: "https://open.bigmodel.cn/api/paas/v4", keyUrl: "https://open.bigmodel.cn/usercenter/apikeys", keyHost: "open.bigmodel.cn" },
  { id: "moonshot", label: "Kimi 月之暗面", protocol: "openai", baseURL: "https://api.moonshot.cn/v1", keyUrl: "https://platform.moonshot.cn/console/api-keys", keyHost: "platform.moonshot.cn" },
  { id: "qwen", label: "通义千问", protocol: "openai", baseURL: "https://dashscope.aliyuncs.com/compatible-mode/v1", keyUrl: "https://bailian.console.aliyun.com/?apiKey=1", keyHost: "bailian.console.aliyun.com" },
  { id: "siliconflow", label: "硅基流动", protocol: "openai", baseURL: "https://api.siliconflow.cn/v1", keyUrl: "https://cloud.siliconflow.cn/account/ak", keyHost: "cloud.siliconflow.cn" },
  { id: "openrouter", label: "OpenRouter", protocol: "openrouter", baseURL: "https://openrouter.ai/api/v1", keyUrl: "https://openrouter.ai/keys", keyHost: "openrouter.ai" },
]

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
    if (typeof s.retryMax === "number" && s.retryMax >= 0) config.retryMax = Math.min(5, Math.floor(s.retryMax))
    if (typeof s.retryDelaySec === "number" && s.retryDelaySec > 0) config.retryDelaySec = Math.min(60, Math.floor(s.retryDelaySec))
    if (typeof s.timeoutSec === "number" && s.timeoutSec > 0) config.timeoutSec = Math.min(7200, Math.max(60, Math.floor(s.timeoutSec)))
    if (typeof s.stallTimeoutSec === "number" && s.stallTimeoutSec > 0) config.stallTimeoutSec = Math.min(1800, Math.max(30, Math.floor(s.stallTimeoutSec)))
    if (typeof s.uiFont === "string" && s.uiFont) config.uiFont = s.uiFont
    if (typeof s.uiFontSize === "number" && s.uiFontSize > 0) config.uiFontSize = Math.min(130, Math.max(85, Math.floor(s.uiFontSize)))
  } catch {}
}
function saveUserSettings() {
  writeJsonAtomic(SETTINGS_FILE, {
    strictVerify: !!config.strictVerify,
    contextLimit: config.contextLimit,
    retryMax: config.retryMax,
    retryDelaySec: config.retryDelaySec,
    timeoutSec: config.timeoutSec,
    stallTimeoutSec: config.stallTimeoutSec,
    uiFont: config.uiFont,
    uiFontSize: config.uiFontSize,
  })
}
loadUserSettings()

// ---------- 用户长期记忆(runtime\user-memory.json) ----------
// 用户自己写的个性化设计偏好(配色、命名、交互习惯…),每轮任务都注入提示词,跨会话生效。
// 放 runtime 而不是 settings:升级安装包时 runtime 会被保留,记忆不会丢。
const MEMORY_FILE = join(__dirname, "runtime", "user-memory.json")
const MEMORY_MAX_ITEMS = 30
const MEMORY_MAX_TEXT = 500
const MEMORY_MAX_CHARS = 2000 // 注入提示词的字数上限,防止偏好写太长挤掉正文
let userMemory = { enabled: true, items: [] }
function loadUserMemory() {
  try {
    const raw = JSON.parse(readFileSync(MEMORY_FILE, "utf8"))
    userMemory.enabled = raw.enabled !== false
    userMemory.items = (Array.isArray(raw.items) ? raw.items : [])
      .filter((x) => x && typeof x.text === "string" && x.text.trim())
      .slice(0, MEMORY_MAX_ITEMS)
      .map((x) => ({ id: String(x.id || `m_${Math.random().toString(36).slice(2, 10)}`), text: String(x.text).slice(0, MEMORY_MAX_TEXT), at: Number(x.at) || Date.now() }))
  } catch (e) {
    if (existsSync(MEMORY_FILE)) console.warn("[wb-studio] 长期记忆文件读取失败,已按空记忆启动:", e.message)
  }
}
function saveUserMemory() {
  writeJsonAtomic(MEMORY_FILE, userMemory)
}
function userMemoryPrompt() {
  if (!userMemory.enabled) return ""
  return formatUserMemory(userMemory.items, { maxChars: MEMORY_MAX_CHARS })
}
loadUserMemory()

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
  writeJsonAtomic(APIS_FILE, apis)
}
function enabledApi() {
  return apis.items.find((a) => a.id === apis.enabledId) || null
}
/** 每套 API 可单独设上下文窗口;没设就跟随全局(设置面板里的「默认上下文容量」) */
const CTX_MIN = 1000
const CTX_MAX = 10_000_000
function normCtxLimit(v) {
  const n = Math.floor(Number(v))
  if (!Number.isFinite(n) || n < CTX_MIN || n > CTX_MAX) return null
  return n
}
function apiCtxLimit(a) {
  const own = a && normCtxLimit(a.contextLimit)
  return own || config.contextLimit
}
/** 当前生效的容量:启用的那套 API 自己设了就用自己的,否则用全局 */
function effectiveContext() {
  const a = enabledApi()
  const own = a && normCtxLimit(a.contextLimit)
  return {
    contextLimit: config.contextLimit,          // 全局默认
    effectiveLimit: own || config.contextLimit, // 实际刻度
    source: own ? "api" : "global",
    apiName: a ? a.name || a.modelID : "",
  }
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

/** 发一次非流式探测请求并判定结果(供连接测试复用) */
async function requestAndJudge(url, headers, body, timeoutMs, modelID) {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), timeoutMs)
  try {
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

/** 流式探测:部分中转网关(如 OpenCSG)只支持流式请求,非流式会一直挂住不响应。
 *  这里发 stream:true 并按 SSE 解析;max_tokens 很小,拿到首个内容块即可判定连通。
 *  若服务商忽略 stream 参数直接回普通 JSON,也能兜底识别。 */
async function testProviderStream({ url, headers, body }, timeoutMs) {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), timeoutMs)
  try {
    const res = await fetch(url, {
      method: "POST",
      signal: ctrl.signal,
      headers,
      body: JSON.stringify({ ...body, stream: true }),
    })
    const raw = await res.text()
    if (!res.ok) {
      let j = null
      try { j = JSON.parse(raw) } catch {}
      const errMsg = j?.error?.message || j?.error?.type || ""
      return { ok: false, status: res.status, error: friendlyApiError(res.status, String(errMsg || raw.slice(0, 200) || "空响应")) }
    }
    let content = "", model = null, sawSSE = false
    for (const line of raw.split("\n")) {
      const t = line.trim()
      if (!t.startsWith("data:")) continue
      sawSSE = true
      const payload = t.slice(5).trim()
      if (!payload || payload === "[DONE]") continue
      let j = null
      try { j = JSON.parse(payload) } catch { continue }
      if (!model && j?.model) model = j.model
      // 推理模型可能只先吐 reasoning_content,也算连通
      const delta = j?.choices?.[0]?.delta
      if (delta?.content) content += delta.content
      else if (delta?.reasoning_content) content += delta.reasoning_content
      if (j?.error) {
        return { ok: false, status: res.status, error: friendlyApiError(res.status, String(j.error?.message || j.error?.type || "流内错误")) }
      }
      if (content) break
    }
    if (!sawSSE) {
      // 没有 SSE 帧:服务商忽略了 stream 参数,按普通 JSON 兜底
      let j = null
      try { j = JSON.parse(raw) } catch {}
      const replyText = j?.choices?.[0]?.message?.content || ""
      if (j && (replyText || j.model)) return { ok: true, model: j?.model || model }
    }
    if (content || model) return { ok: true, model: model || undefined }
    return { ok: false, status: res.status, error: "流式响应为空:服务商未返回任何内容" }
  } catch (e) {
    return {
      ok: false,
      status: e.name === "AbortError" ? 0 : undefined,
      error: e.name === "AbortError"
        ? "连接测试超时(流式也无响应)"
        : `连接失败:${e.message}`,
    }
  } finally {
    clearTimeout(timer)
  }
}

/** 用真实请求测试一套 API 配置能否出对话;任何失败都给出人话原因
 *  两级探测:先非流式(正常服务商 1–2 秒就回),若挂住/空响应再按流式探测一次
 *  ——部分中转网关(如 OpenCSG)只支持流式,非流式会一直不响应 */
async function testProvider({ baseURL, apiKey, modelID, protocol }, timeoutMs = 15000) {
  const proto = protocolInfo(protocol)
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
  const started = Date.now()
  // 第一轮:非流式,只给 6s(正常服务商秒回),避免只认流式的网关干等
  const first = await requestAndJudge(url, headers, body, Math.min(timeoutMs, 6000), modelID)
  if (first.ok) return first
  // 第二轮:只支持流式的中转网关(非流式超时/空响应),改用流式再探一次
  const streamable = protocol !== "anthropic" && protocol !== "google"
  if (streamable && (first.status === 0 || first.status === undefined || String(first.error || "").includes("空响应"))) {
    const left = Math.max(timeoutMs - (Date.now() - started), 5000)
    const second = await testProviderStream({ url, headers, body }, left)
    if (second.ok) return second
    // 两边都没通:保留描述更具体的那个错误(带 HTTP 状态码的优先)
    return second.status === 0 || second.status === undefined ? first : second
  }
  return first
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
  button.ghost{background:#fff;color:#c93a68;border:2px solid #ffc3d8;margin-top:10px}
  button.ghost:hover{background:#fdeef4;border-color:#e2517f}
  .msg{margin-top:14px;font-size:13px;display:none;padding:10px 14px;border-radius:10px}
  .msg.bad{display:block;background:#fdf3f2;color:#c0392b;border:1px solid #f3b0af}
  .msg.good{display:block;background:#f4fbf5;color:#2d7a3a;border:1px solid #9fd8a8}
  details{margin-top:18px;font-size:12.5px;color:#8a7480}
  summary{cursor:pointer;font-weight:700}
  ol{padding-left:18px;line-height:1.9;margin:8px 0 0}
  .pchip{display:inline-block;border:1px solid #f3b0c4;background:#fff;border-radius:999px;padding:6px 13px;font-size:12.5px;cursor:pointer;user-select:none;color:#8a4a63}
  .pchip:hover{background:#fdeef4;border-color:#e2517f;color:#c93a68}
</style></head><body>
<div class="card">
  <h1>🐱 欢迎来到插件工坊</h1>
  <div class="sub">第一次使用,先用 1 分钟告诉哈基米怎么连模型服务</div>
  <div class="tip" style="margin-top:14px">💡 常用服务商,点一下自动填好服务地址(密钥和模型名称去官网查):</div>
  <div id="presets" style="display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 2px"></div>
  <div class="tip" id="presetTip" style="display:none"></div>
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
  <button id="try" class="ghost">🔌 先测一下能不能连上</button>
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
const PRESETS=${JSON.stringify(PROVIDER_PRESETS)};
(function(){
  const box=$('presets');
  for(const p of PRESETS){
    const c=document.createElement('span');c.className='pchip';c.textContent=p.label;
    c.onclick=()=>{
      // 只填服务商地址;协议保持用户当前选择(默认 OpenAI 兼容,页面有引导),模型 ID 不代填
      if(p.protocol&&p.protocol!=='openai'&&$('proto').value!==p.protocol){$('proto').value=p.protocol;protoChange();}
      $('u').value=p.baseURL;
      const tip=$('presetTip');tip.style.display='';
      tip.innerHTML='已填好「'+p.label+'」的服务地址。密钥和模型名称请到官网查看 → <a href="'+p.keyUrl+'" target="_blank" rel="noopener" style="color:#e2517f">'+p.keyHost+'</a>(模型名照官网文档填,保存时会自动测连通性,填错会提示)';
    };
    box.appendChild(c);
  }
})();
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
// 主动测试:只测不存,填完随时可以点一下确认能不能连上
$('try').onclick=async()=>{
  const btn=$('try'),msg=$('msg');
  const baseURL=$('u').value.trim(),apiKey=$('k').value.trim(),model=$('m').value.trim(),protocol=$('proto').value;
  if(!baseURL){msg.textContent='😢 先填服务地址(Base URL)再测';msg.className='msg bad';return}
  if(!apiKey){msg.textContent='😢 先填 API 密钥再测';msg.className='msg bad';return}
  if(!model){msg.textContent='😢 先填模型名称再测';msg.className='msg bad';return}
  btn.disabled=true;const old=btn.textContent;btn.textContent='🔌 测试中…(最多 20 秒)';msg.className='msg';
  try{
    const r=await fetch('/api/test',{method:'POST',headers:{'content-type':'application/json'},
      body:JSON.stringify({baseURL,apiKey,model,protocol})});
    const j=await r.json();
    if(j.ok&&j.passed){msg.textContent='✅ 连上了!模型回应正常'+(j.model?'('+j.model+')':'');msg.className='msg good';}
    else{msg.textContent='😢 没连上:'+(j.error||'未知原因');msg.className='msg bad';}
  }catch(e){msg.textContent='😢 连接出错:'+e;msg.className='msg bad';}
  btn.disabled=false;btn.textContent=old;
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
  #compactbtn{margin-top:4px;border:0;background:transparent;color:var(--muted);font-size:12.5px;cursor:pointer;padding:8px 10px;border-radius:9px;font-family:inherit;text-align:left}
  #compactbtn:hover{background:var(--tint);color:var(--pink-d)}
  #compactbtn:disabled{opacity:.6;cursor:wait}
  main{flex:1;min-width:0;min-height:0;display:flex;flex-direction:column;padding:0}
  @media(max-width:760px){#wrap{flex-direction:column}#side{width:100%;max-height:150px}}
  #chat{flex:1;display:flex;flex-direction:column;gap:12px;overflow-y:auto;padding:8px 4px}
  .msg{max-width:86%;padding:12px 16px;border-radius:16px;line-height:1.7;white-space:pre-wrap;word-break:break-word;font-size:14.5px}
  .user{align-self:flex-end;background:var(--pink);color:var(--on-primary);border-bottom-right-radius:4px}
  .bot{align-self:flex-start;background:var(--card);border:1px solid var(--border);border-bottom-left-radius:4px}
  .bot.ok{border-color:var(--border);background:var(--tint);color:var(--ink)}
  .bot.err{border-color:var(--err-border);background:var(--err-bg);color:var(--err-ink)}
  .typing{align-self:flex-start;color:var(--muted);font-size:14px;padding:6px 12px}
  /* 上下文压缩提示:居中细条,不抢戏但能回看 */
  .msg.notice{align-self:center;max-width:92%;background:transparent;border:1px dashed var(--border);color:var(--muted);font-size:12.5px;padding:6px 14px;border-radius:999px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .typing span{animation:blink 1.2s infinite}
  @keyframes blink{0%,100%{opacity:.2}50%{opacity:1}}
  .chip{display:inline-block;margin-top:8px;padding:7px 14px;background:var(--tint);border:1px solid var(--pink);color:var(--pink-d);border-radius:999px;font-size:13px;text-decoration:none}
  .chip:hover{background:var(--pink);color:var(--on-primary)}
  /* 改动文件列表:整块卡片 + 可点击行(点名字在资源管理器里定位),不再铺一堆气泡 */
  .fchg{margin:10px 0 2px;border:1px solid var(--border);border-radius:12px;overflow:hidden;max-width:560px;background:var(--card)}
  .fchg-h{font-size:12.5px;color:var(--muted);background:var(--tint);padding:7px 11px;border-bottom:1px solid var(--border)}
  .fchg-h b{color:var(--pink-d)}
  .fchg-list{list-style:none;margin:0;padding:0;max-height:190px;overflow:auto}
  .fchg-list li+li{border-top:1px dashed var(--border)}
  .fchg-row{display:flex;align-items:center;gap:8px;width:100%;box-sizing:border-box;border:0;background:transparent;padding:7px 11px;font-family:inherit;font-size:13px;color:var(--ink);cursor:pointer;text-align:left}
  .fchg-row:hover{background:var(--tint)}
  .fchg-row.opening{background:var(--tint);opacity:.6}
  .fchg-n{font-weight:700;white-space:nowrap;flex:0 1 auto;overflow:hidden;text-overflow:ellipsis}
  .fchg-d{flex:1 1 auto;min-width:0;color:var(--muted-2);font-size:11.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .fchg-go{flex:0 0 auto;color:var(--pink-d);font-size:12px;opacity:.55}
  /* 长期记忆列表 */
  .memrow{display:flex;align-items:center;gap:10px;padding:9px 11px;border:1px solid var(--border);border-radius:10px;margin-bottom:6px;background:var(--card)}
  .memtxt{flex:1;font-size:13px;color:var(--ink);line-height:1.55;word-break:break-word}
  .memdel{border:0;background:transparent;color:var(--pink-d);font-size:12px;cursor:pointer;padding:2px 6px;flex:0 0 auto}
  .memdel:hover{text-decoration:underline}
  form{display:flex;gap:10px;margin-top:14px}
  textarea{flex:1;resize:none;border:2px solid var(--border-soft);border-radius:14px;padding:12px 14px;font-size:15px;font-family:inherit;outline:none;height:74px;background:var(--card);color:var(--ink)}
  textarea:focus{border-color:var(--pink)}
  button{border:0;background:var(--pink);color:var(--on-primary);border-radius:14px;padding:0 26px;font-size:15px;font-weight:700;cursor:pointer;font-family:inherit}
  button:disabled{opacity:.55;cursor:wait}
  button:hover:not(:disabled){background:var(--pink-d)}
  .hint{color:var(--muted-2);font-size:12.5px;margin-top:8px;line-height:1.8}
  /* 上下文容量环:直径 18px,占用越高环越满,颜色随阈值变(绿→橙→红) */
  .ctxring{display:inline-flex;align-items:center;vertical-align:-4px;margin-left:1px}
  .ctxring svg{display:block;overflow:visible}
  .ctxring circle.bg{stroke:var(--border)}
  .ctxtxt{margin-left:4px;font-variant-numeric:tabular-nums;letter-spacing:.2px}
  .ctxtxt b{font-weight:800}
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
  .apirow .ctxchip{flex-shrink:0;border:1px dashed var(--border);border-radius:999px;padding:3px 9px;font-size:11.5px;color:var(--muted);cursor:pointer;white-space:nowrap;user-select:none}
  .apirow .ctxchip:hover{border-color:var(--pink);color:var(--pink-d);background:var(--card)}
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
  .fontopt{display:inline-flex;align-items:center;gap:6px;padding:9px 16px;border:2px solid var(--border);border-radius:12px;cursor:pointer;font-size:15px;font-weight:700;color:var(--ink);background:var(--card)}
  .fontopt:hover{border-color:var(--muted-2)}
  .fontopt.cur{border-color:var(--pink);background:var(--tint)}
  .fontopt.cur::after{content:'✓';font-size:11px;color:var(--pink-d);font-weight:700}
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
<header><span class="logo">🐱</span><div><h1>N.E.K.O. 插件工坊</h1><small>说一句话,哈基米帮你做好插件</small></div>
  <div class="tools">
    <div id="modeswitch" title="直接做=哈基米立刻生成插件;先出方案=只给思路和计划,不动手">
      <button type="button" id="modeBuild" class="mbtn on">🛠 直接做</button>
      <button type="button" id="modePlan" class="mbtn">📋 先出方案</button>
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
    <button type="button" class="sptab" data-tab="run">🔁 重试</button>
    <button type="button" class="sptab" data-tab="mem">🧠 记忆</button>
    <button type="button" class="sptab" data-tab="update">⬆️ 更新</button>
    <button type="button" id="setclose" class="sp-close">收起 ▴</button>
  </div>
  <div class="sp-body" id="tab-api">
    <div class="sp-head">API 配置 <span class="sp-tip">可添加多套,点击「启用」切换;密钥只显示尾 4 位</span></div>
    <div id="apilist"></div>
    <div class="sp-form">
      <h4>添加新 API</h4>
      <div class="sp-tip" style="margin:0 0 6px">💡 点一下自动填好服务地址(密钥和模型名称去官网查):</div>
      <div id="apiPresets" style="display:flex;flex-wrap:wrap;gap:6px;margin:0 0 10px"></div>
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
      <div class="frow"><label>上下文窗口</label><input id="apiCtx" type="number" min="1" step="1" placeholder="留空 = 跟随全局;填 K 数,如 128"></div>
      <div class="frow2">
        <button type="button" id="apiSave">保存并启用</button>
        <button type="button" id="apiSaveOnly" class="ghost">仅保存</button>
        <button type="button" id="apiTest" class="ghost">🔌 测试连接</button>
        <span id="apiMsg"></span>
      </div>
    </div>
  </div>
  <div class="sp-body" id="tab-run" style="display:none">
    <div class="sp-head">超时与重试 <span class="sp-tip">保存后从下一个任务开始生效;任务总超时是整轮上限,无响应超时是连续多久没收到输出就判卡死</span></div>
    <div>
      <div style="font-size:13.5px;color:var(--ink)"><b>运行参数</b> <span style="color:var(--muted-2);font-size:12px">(超时与自动重试)</span></div>
      <div style="margin-top:10px;display:grid;grid-template-columns:1fr 1fr;gap:10px 14px">
        <label style="font-size:12.5px;color:var(--muted-2)">任务总超时(秒)
          <input id="timeoutSec" type="number" min="60" max="7200" step="10" style="margin-top:4px;width:100%;padding:6px 8px;border-radius:8px;border:1px solid var(--border);background:var(--tint);color:var(--ink);font-size:12.5px">
        </label>
        <label style="font-size:12.5px;color:var(--muted-2)">无响应超时(秒)
          <input id="stallTimeoutSec" type="number" min="30" max="1800" step="10" style="margin-top:4px;width:100%;padding:6px 8px;border-radius:8px;border:1px solid var(--border);background:var(--tint);color:var(--ink);font-size:12.5px">
        </label>
        <label style="font-size:12.5px;color:var(--muted-2)">自动重试次数
          <input id="retryMax" type="number" min="0" max="5" step="1" style="margin-top:4px;width:100%;padding:6px 8px;border-radius:8px;border:1px solid var(--border);background:var(--tint);color:var(--ink);font-size:12.5px">
        </label>
        <label style="font-size:12.5px;color:var(--muted-2)">首次重试间隔(秒)
          <input id="retryDelaySec" type="number" min="1" max="60" step="1" style="margin-top:4px;width:100%;padding:6px 8px;border-radius:8px;border:1px solid var(--border);background:var(--tint);color:var(--ink);font-size:12.5px">
        </label>
      </div>
      <div style="margin-top:10px;display:flex;gap:8px;align-items:center">
        <button type="button" id="tuneSave" style="border:0;background:var(--pink);color:var(--on-primary);border-radius:8px;padding:6px 14px;font-size:12.5px;font-weight:700;cursor:pointer">保存</button>
        <button type="button" id="tuneReset" style="border:1px solid var(--border);background:var(--tint);color:var(--ink);border-radius:8px;padding:6px 12px;font-size:12.5px;cursor:pointer">恢复默认</button>
        <span id="tuneMsg" style="font-size:12px;color:var(--muted)"></span>
      </div>
      <div style="margin-top:8px;color:var(--muted-2);font-size:11.5px">默认 1500 / 360 / 2 次 / 3 秒。任务总超时是整轮上限;无响应超时是"连续多久没收到任何输出就判卡死",必须小于总超时。重试只在本轮还没有任何产出时发生(间隔 3s、6s、12s…翻倍,上限 30s)。</div>
    </div>
  </div>
  <div class="sp-body" id="tab-mem" style="display:none">
    <div class="sp-head">长期记忆 <span class="sp-tip">记住你的个性化设计偏好,新开对话也照做;存在 runtime,升级安装包不丢</span></div>
    <label style="display:flex;align-items:center;gap:10px;cursor:pointer;font-size:13.5px;color:var(--ink)">
      <input type="checkbox" id="memOn" style="width:17px;height:17px;accent-color:var(--pink)">
      <span>启用记忆 <span style="color:var(--muted-2);font-size:12px">(关掉只是不再注入,记录仍保留)</span></span>
    </label>
    <div style="margin-top:12px;display:flex;gap:8px;align-items:stretch">
      <input id="memInput" maxlength="500" placeholder="例如:配色统一用低饱和莫兰迪色;插件名优先用中文;默认带设置面板" style="flex:1;padding:8px 10px;border-radius:10px;border:1px solid var(--border);background:var(--tint);color:var(--ink);font-size:13px">
      <button type="button" id="memAdd" style="border:0;background:var(--pink);color:var(--on-primary);border-radius:10px;padding:0 18px;font-size:13px;font-weight:700;cursor:pointer">添加</button>
    </div>
    <div id="memList" style="margin-top:12px"></div>
    <div style="margin-top:8px;color:var(--muted-2);font-size:11.5px">最多 30 条、单条 500 字;每轮任务都会带上,和本轮需求冲突时以本轮需求为准</div>
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
      <div style="font-size:13.5px;color:var(--ink)"><b>界面字体</b> <span style="color:var(--muted-2);font-size:12px">(点了立即生效;系统没装这个名字会自动落到相近的中文字体)</span></div>
      <div id="fontlist" style="margin-top:10px;display:flex;flex-wrap:wrap;gap:8px">
        <div class="fontopt cur" data-f="default">默认</div>
        <div class="fontopt" data-f="nailao" style="font-family:'奶酪体','幼圆',YouYuan,'Microsoft YaHei',sans-serif">奶酪体</div>
        <div class="fontopt" data-f="cute" style="font-family:'可爱体','幼圆',YouYuan,'Comic Sans MS',sans-serif">可爱体</div>
        <div class="fontopt" data-f="hengshui" style="font-family:'衡水体','楷体',KaiTi,STKaiti,serif">衡水体</div>
        <div class="fontopt" data-f="song" style="font-family:'宋体',SimSun,serif">宋体</div>
      </div>
      <div style="margin-top:14px;display:flex;align-items:center;gap:10px;flex-wrap:wrap">
        <span style="font-size:13px;color:var(--ink)"><b>字号</b></span>
        <input id="fontSize" type="range" min="85" max="130" step="5" value="100" style="width:220px;accent-color:var(--pink)">
        <span id="fontSizeVal" style="font-size:12.5px;color:var(--muted-2);min-width:52px">100%</span>
        <button type="button" id="fontSizeReset" style="border:1px solid var(--border);background:var(--tint);color:var(--ink);border-radius:8px;padding:5px 10px;font-size:12px;cursor:pointer">恢复 100%</button>
      </div>
      <div style="margin-top:6px;color:var(--muted-2);font-size:11.5px">字号是整体缩放(85% ~ 130%),按钮和间距一起变;系统里没装"奶酪体/可爱体/衡水体"时会用幼圆、楷体这些相近字体顶上</div>
    </div>
    <div style="margin-top:16px;border-top:1px dashed var(--border);padding-top:12px">
      <label style="display:flex;align-items:center;gap:10px;cursor:pointer;font-size:13.5px;color:var(--ink)">
        <input type="checkbox" id="strictToggle" style="width:17px;height:17px;accent-color:var(--pink)">
        <span><b>严格校验</b> <span style="color:var(--muted-2);font-size:12px">(warning 也算失败,必须修到零警告才打包)</span></span>
      </label>
    </div>
    <div style="margin-top:16px;border-top:1px dashed var(--border);padding-top:12px">
      <div style="font-size:13.5px;color:var(--ink)"><b>默认上下文容量</b> <span style="color:var(--muted-2);font-size:12px">(没单独设置的 API 都用这个;容量环的满格刻度)</span></div>
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
  <div class="sp-body" id="tab-update" style="display:none">
    <div class="sp-head">版本更新 <span class="sp-tip">检查优先走 GitHub,失败自动改用码云镜像</span></div>
    <div style="margin-top:12px;display:flex;align-items:center;gap:10px;flex-wrap:wrap">
      <span style="font-size:13px;color:var(--ink)">当前版本 <b id="updCur">-</b></span>
      <button type="button" id="updCheck" style="border:1px solid var(--border);background:var(--tint);color:var(--ink);border-radius:8px;padding:6px 14px;font-size:12.5px;font-weight:700;cursor:pointer">检查更新</button>
      <span id="updMsg" style="font-size:12px;color:var(--muted)"></span>
    </div>
    <div id="updBox" style="display:none;margin-top:14px">
      <div style="display:flex;justify-content:space-between;font-size:12.5px;color:var(--muted-2)">
        <span id="updPhase">准备中…</span><span id="updPct"></span>
      </div>
      <div style="margin-top:6px;height:10px;background:var(--tint);border:1px solid var(--border);border-radius:999px;overflow:hidden">
        <div id="updBar" style="height:100%;width:0;background:var(--pink);transition:width .2s"></div>
      </div>
      <div id="updDetail" style="margin-top:6px;font-size:11.5px;color:var(--muted-2)"></div>
    </div>
    <div style="margin-top:14px;color:var(--muted-2);font-size:11.5px">下载完成后工坊会退出,安装器接管并自动重新启动;安装完成后临时文件自动删除。更新不会动 runtime/(密钥、会话、记忆)与 workspace/(插件项目)。</div>
  </div>
</div>
</div>
<div id="updModal" style="display:none;position:fixed;inset:0;z-index:200;background:rgba(0,0,0,.45);align-items:center;justify-content:center">
  <div style="width:min(430px,90vw);background:var(--card);border:1px solid var(--border);border-radius:16px;padding:22px 24px;box-shadow:0 12px 40px var(--shadow)">
    <div id="updTitle" style="font-size:16px;font-weight:800;color:var(--ink)">发现新版本</div>
    <div id="updModalBody" style="margin-top:10px;font-size:13px;color:var(--muted);line-height:1.8"></div>
    <div style="margin-top:18px;display:flex;gap:10px;justify-content:flex-end">
      <button type="button" id="updCancel" style="border:1px solid var(--border);background:var(--tint);color:var(--ink);border-radius:9px;padding:7px 16px;font-size:12.5px;font-weight:700;cursor:pointer">以后再说</button>
      <button type="button" id="updGo" style="border:0;background:var(--pink);color:var(--on-primary);border-radius:9px;padding:7px 18px;font-size:12.5px;font-weight:700;cursor:pointer">立即更新</button>
    </div>
  </div>
</div>
<div id="wrap">
  <aside id="side">
    <h3>对话列表</h3>
    <button id="newbtn">+ 新对话</button>
    <div id="slist"></div>
    <button id="archbtn" title="在会话列表和已归档对话之间切换">🗂 查看已归档</button>
    <button id="compactbtn" title="把较早的对话压成摘要,腾出上下文空间(自动压缩之外的兜底)">🗜 压缩上下文</button>
  </aside>
  <main>
  <div id="chat">
    <div class="msg bot">你好呀!我是哈基米 🐾 把愿望告诉我,我把它做成插件。不知道说什么?点一个试试:<br><div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:8px"><span class="chip" style="cursor:pointer" data-wish="做一个每日提醒喝水的插件,每 45 分钟提醒一次">💧 喝水提醒</span><span class="chip" style="cursor:pointer" data-wish="做一个记事本插件,能存笔记还能搜索">📝 记事本</span><span class="chip" style="cursor:pointer" data-wish="做一个番茄钟插件,每 25 分钟提醒我休息一次">🍅 番茄钟</span><span class="chip" style="cursor:pointer" data-wish="做一个每日诗词插件,每天早上给我推一首诗">📜 每日诗词</span></div></div>
  </div>
  <form id="f"><textarea id="t" placeholder="说出你的愿望,比如:每天早上 8 点提醒我喝水…" required></textarea><button id="b">开工!</button></form>
  <div class="hint"><span id="tokbar"></span><span id="ctxbar"></span><span> · 回车发送,Shift+回车换行;完成后可下载 .neko-plugin 导入 N.E.K.O.</span></div>
  </main>
</div>
<script>
const WB_TOKEN="__WB_TOKEN__";
(function(){const _f=fetch.bind(window);window.fetch=(u,o={})=>{const m=(o.method||"GET").toUpperCase();const h=new Headers(o.headers||{});h.set("x-wb-token",WB_TOKEN);if(m!=="GET"&&!h.has("content-type"))h.set("content-type","application/json");o.headers=h;return _f(u,o)}})();
const chat=document.getElementById('chat'),form=document.getElementById('f'),t=document.getElementById('t'),b=document.getElementById('b');
const slist=document.getElementById('slist'),newbtn=document.getElementById('newbtn');
let curSession=null;
// 上下文容量:环状指示(占用≈最近一轮 usage.input+output),刻度来自「当前 API 自己的设置」或全局默认
let ctxLimit=128000,ctxUsed=0,ctxSource='global',ctxApiName='';
function fmtCtx(n){return n>=1000000?(n/1000000).toFixed(n%1000000?1:0)+'M':n>=1000?(n/1000).toFixed(n%1000?1:0)+'K':''+n}
// 自动重试提示:说清「为什么重试」和「试了几次」,别让用户以为程序卡住
function retryText(list){
  if(!list||!list.length)return '';
  const k={ 'no-response':'无响应','connection':'连接中断','error':'出错' };
  const label=k[list[0].kind]||'出错';
  return '🔄 模型'+label+',已自动重试 '+list.length+' 次';
}
function updateCtxBar(){
  const el=document.getElementById('ctxbar');if(!el)return;
  const pct=ctxLimit>0?Math.min(100,ctxUsed/ctxLimit*100):0;
  const col=pct>=90?'#e53935':pct>=70?'#fb8c00':'#43a047';
  const R=7.5, C=2*Math.PI*R, off=(C*(1-pct/100)).toFixed(2);
  const who=ctxSource==='api'?('本套 API 单独设置'+(ctxApiName?'('+ctxApiName+')':'')):'跟随全局默认';
  const tip='上下文 '+fmtCtx(ctxUsed)+' / '+fmtCtx(ctxLimit)+' · '+Math.round(pct)+'% · '+who;
  el.innerHTML=' · <span class="ctxring" title="'+esc(tip)+'">'
    +'<svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true">'
    +'<circle class="bg" cx="10" cy="10" r="'+R+'" fill="none" stroke-width="3"></circle>'
    +'<circle cx="10" cy="10" r="'+R+'" fill="none" stroke="'+col+'" stroke-width="3" stroke-linecap="round"'
    +' stroke-dasharray="'+C.toFixed(2)+'" stroke-dashoffset="'+off+'" transform="rotate(-90 10 10)"'
    +' style="transition:stroke-dashoffset .45s ease,stroke .3s"></circle>'
    +'</svg></span>'
    +'<span class="ctxtxt">'+fmtCtx(ctxUsed)+'/<b>'+fmtCtx(ctxLimit)+'</b></span>';
}
// 从后端同步容量刻度(切 API / 改设置 / 首次加载都走这里)
async function loadCtxLimit(){
  try{
    const j=await(await fetch('/api/context')).json();
    if(j&&j.ok){
      ctxLimit=j.effectiveLimit||j.contextLimit||128000;
      ctxSource=j.source||'global';
      ctxApiName=j.apiName||'';
      updateCtxBar();
      const inp=document.getElementById('ctxInput');
      if(inp&&document.activeElement!==inp)inp.value=Math.round((j.contextLimit||128000)/1000);
    }
  }catch(e){}
}
function add(cls,html){const d=document.createElement('div');d.className='msg '+cls;d.innerHTML=html;chat.appendChild(d);chat.scrollTop=chat.scrollHeight;return d}
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function fmtTok(n){n=n||0;if(n>=10000)return (n/10000).toFixed(1)+'万';if(n>=1000)return (n/1000).toFixed(1)+'k';return ''+n}
function usageLine(u){if(!u)return'';const out=(u.output||0)+(u.reasoning||0);let s='Token ↑'+fmtTok(u.input)+' ↓'+fmtTok(out);if(u.reasoning)s+=' (含思考 '+fmtTok(u.reasoning)+')';return '<div class="tusage">'+s+'</div>'}
// Build 模式任务收尾:本次已修改的文件(相对路径,时间序;产物另有下载 chip)
// 改动文件 = 一块列表(不是一堆气泡);每行可点,点名字在资源管理器里定位该文件
function fBase(p){const s=String(p||'');const i=Math.max(s.lastIndexOf('/'),s.lastIndexOf('\\\\'));return i>=0?s.slice(i+1):s}
function fDir(p){const s=String(p||'');const i=Math.max(s.lastIndexOf('/'),s.lastIndexOf('\\\\'));return i>0?s.slice(0,i):''}
function modFilesHtml(fs){
  if(!fs||!fs.length)return '';
  const rows=fs.map(f=>'<li><button type="button" class="fchg-row" data-reveal="'+esca(f)+'" title="在资源管理器中定位:'+esc(f)+'">'
    +'<span class="fchg-n">'+esc(fBase(f))+'</span>'
    +(fDir(f)?'<span class="fchg-d">'+esc(fDir(f))+'</span>':'')
    +'<span class="fchg-go">↗</span></button></li>').join('');
  return '<div class="fchg"><div class="fchg-h">✏️ 本次改动 <b>'+fs.length+'</b> 个文件 · 点文件名定位</div><ul class="fchg-list">'+rows+'</ul></div>';
}
// ---------- Plan 模式引导提问/方案 标记解析 ----------
// 约定:[[WB-Q]]…[[/WB-Q]] = 可点候选答案(点一下即作为回答发送);[[WB-PLAN]] = 方案(带开工按钮)
function esca(s){return esc(s).replace(/"/g,'&quot;')}
function parseExtras(content){
  let text=String(content==null?'':content),qOpts=null,isPlan=false;
  const qm=text.match(/\\[\\[WB-Q\\]\\]([\\s\\S]*?)\\[\\[\\/WB-Q\\]\\]/);
  if(qm){
    qOpts=qm[1].split(/\\r?\\n/).map(s=>s.trim()).filter(Boolean).slice(0,6);
    text=text.replace(qm[0],'').replace(/\\n{3,}/g,'\\n\\n').trim();
  }
  if(/\\[\\[WB-PLAN\\]\\]/.test(text)){isPlan=true;text=text.replace(/\\[\\[WB-PLAN\\]\\]/g,'').replace(/\\n{3,}/g,'\\n\\n').trim();}
  return {text:text,qOpts:qOpts,isPlan:isPlan};
}
function extrasHtml(x){
  let h='';
  if(x.qOpts&&x.qOpts.length){
    h+='<div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:8px">';
    for(const o of x.qOpts)h+='<span class="chip" style="cursor:pointer" data-wbq="'+esca(o)+'">'+esc(o)+'</span>';
    // 每个提问都必须留「其他」:模型漏写也要兜底补一个(点它不直接发送,只聚焦输入框)
    if(!x.qOpts.some(o=>/其他|自己|自定义/.test(o)))h+='<span class="chip" data-wbother="1" style="cursor:pointer;border-style:dashed">✍️ 其他(自己说)</span>';
    h+='</div>';
  }
  if(x.isPlan)h+='<div style="margin-top:8px"><button type="button" class="chip" data-wbplan="'+esca(x.text)+'" style="cursor:pointer;border:0;background:var(--pink);color:#fff;font-weight:700;padding:8px 14px;font-size:13px">🛠 开工,按这个方案做</button></div>';
  return h;
}

// ---------- 会话列表 ----------
let showArchived=false;
try{showArchived=localStorage.getItem('wb_showarch')==='1'}catch(e){}
const archbtn=document.getElementById('archbtn');
function renderArchBtn(){archbtn.textContent=showArchived?'← 返回对话列表':'🗂 查看已归档';archbtn.classList.toggle('on',showArchived);archbtn.title=showArchived?'返回未归档的对话列表':'查看已归档的对话';}
archbtn.onclick=()=>{showArchived=!showArchived;try{localStorage.setItem('wb_showarch',showArchived?'1':'0')}catch(e){};renderArchBtn();refreshList();};
renderArchBtn();
// 手动压缩上下文:阈值之外的兜底入口(会话很长但还没触发自动压缩时,用户可主动腾空间)
document.getElementById('compactbtn').onclick=async()=>{
  const btn=document.getElementById('compactbtn');
  if(!curSession||btn.disabled)return;
  btn.disabled=true;const old=btn.textContent;btn.textContent='🗜 压缩中…';
  try{
    const r=await fetch('/api/sessions/'+curSession+'/compact',{method:'POST'});
    const j=await r.json();
    if(j.ok&&j.compact){await loadCurrent();}
    else if(j.ok){add('notice','🗜 这个会话还不够长,暂时不用压缩');}
    else{alert(j.error||'压缩失败');}
  }catch(e){alert('压缩失败:'+e);}
  btn.disabled=false;btn.textContent=old;
};
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
  chat.innerHTML='<div class="msg bot">你好呀!我是哈基米 🐾 把愿望告诉我,我把它做成插件。不知道说什么?点一个试试:<br><div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:8px"><span class="chip" style="cursor:pointer" data-wish="做一个每日提醒喝水的插件,每 45 分钟提醒一次">💧 喝水提醒</span><span class="chip" style="cursor:pointer" data-wish="做一个记事本插件,能存笔记还能搜索">📝 记事本</span><span class="chip" style="cursor:pointer" data-wish="做一个番茄钟插件,每 25 分钟提醒我休息一次">🍅 番茄钟</span><span class="chip" style="cursor:pointer" data-wish="做一个每日诗词插件,每天早上给我推一首诗">📜 每日诗词</span></div></div>';
  if(s.title&&s.title!=='新对话'){const h=document.createElement('div');h.className='typing';h.style.fontWeight='700';h.textContent=s.title;chat.appendChild(h);}
  for(const m of s.messages||[]){
    if(m.notice==='compact'){
      const c=m.compact||{};
      add('notice','🗜 上下文已压缩'+(c.before?'('+fmtCtx(c.before)+' → ~'+fmtCtx(c.after)+' tokens)':'')+' · 摘要已保留,对话可继续');
      continue;
    }
    if(m.notice==='retry'){
      add('notice',retryText(m.retries));
      continue;
    }
    if(m.role==='user')add('user',esc(m.content));
    else{
      if(m.error&&!m.content)add('err','😢 '+esc(m.error));
      else{let html='';if(m.thinking)html+='<details class="think"><summary>💭 思考过程</summary><div class="tbody">'+esc(m.thinking)+'</div></details>';const _x=parseExtras(m.content);html+=esc(_x.text);html+=modFilesHtml(m.modifiedFiles);if(m.artifacts&&m.artifacts.length){html+='<br>';for(const a of m.artifacts){const name=a.split('\\\\').pop();html+='<a class="chip" href="/api/download?f='+encodeURIComponent(name)+'">⬇ 下载 '+esc(name)+'</a>';}}html+=extrasHtml(_x);html+=usageLine(m.usage);add(m.error?'bot err':'bot ok',html);}
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
  add('user',esc(text)+(workMode==='plan'?' <span style="opacity:.75;font-size:11px">[先出方案]</span>':''));t.value='';b.disabled=true;stopbtn.style.display='';
  const ty=document.createElement('div');ty.className='typing';ty.innerHTML='哈基米正在'+(workMode==='plan'?'思考方案':'写插件')+'<span>🐱…</span>';chat.appendChild(ty);chat.scrollTop=chat.scrollHeight;
  // 实时思考过程:任务期间每 0.5s 轮询引擎(2s 会明显滞后),思考流式展开;任务结束后移除(由最终消息里的折叠版接管)
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
  },500);
  try{
    const r=await fetch('/api/task',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({text,sessionId:sentTo,mode:workMode})});
    const j=await r.json();
    clearInterval(pollTimer);ty.remove();
    if(liveThink){liveThink.remove();liveThink=null;}
    refreshList();
    // 只有用户仍停留在发起任务的会话时才把结果渲染进当前聊天区;
    // 若已切走,结果服务端早已存进归属会话,切回时自然可见——绝不能拽回 curSession 或串台渲染(实战踩过:侧边栏与聊天区错位,像"切换失灵")
    if(curSession!==sentTo)return;
    if(j.compact){const c=j.compact;add('notice','🗜 上下文接近上限,已自动压缩('+fmtCtx(c.before)+' → ~'+fmtCtx(c.after)+' tokens) · 摘要已保留');}
    if(j.retries&&j.retries.length){add('notice',retryText(j.retries)+(j.ok?'并恢复':'仍未成功'));}
    if(j.stopped){add('err','⏹ 已停止');}
    else if(!j.ok){add('err','😢 咪呀,没成功:<br>'+esc(j.error||'未知错误'));if(j.reply)add('bot err',esc(j.reply));}
    else{
      let html='';
      if(j.thinking)html+='<details class="think"><summary>💭 思考过程</summary><div class="tbody">'+esc(j.thinking)+'</div></details>';
      const _x=parseExtras(j.reply||'完成!');
      html+=esc(_x.text);
      html+=modFilesHtml(workMode==='build'?j.modifiedFiles:null);
      if(j.artifacts&&j.artifacts.length){html+='<br>';for(const a of j.artifacts){const name=a.split('\\\\').pop();html+='<a class="chip" href="/api/download?f='+encodeURIComponent(name)+'">⬇ 下载 '+esc(name)+'</a>';}
        html+='<div style="margin-top:8px;font-size:12.5px;background:var(--tint);border-radius:10px;padding:8px 10px;line-height:1.8">📦 <b>装进 N.E.K.O. 三步:</b>① 点上面下载 .neko-plugin → ② 打开 N.E.K.O. 插件管理器,用「导入安装包」选中它 → ③ 启动插件,完成!</div>';}
      html+=extrasHtml(_x);
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
// 愿望卡片:点击自动填入输入框(事件委托,重新渲染的欢迎语里同样生效)
chat.addEventListener('click',e=>{const c=e.target.closest('[data-wish]');if(!c||b.disabled)return;t.value=c.getAttribute('data-wish');t.focus();});
// 改动文件列表:点一行 → 后端在资源管理器里定位该文件(不执行、不打开内容,只跳转)
chat.addEventListener('click',async e=>{
  const row=e.target.closest('[data-reveal]');if(!row)return;
  e.preventDefault();
  row.classList.add('opening');
  try{
    const r=await fetch('/api/reveal',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({path:row.getAttribute('data-reveal')})});
    const j=await r.json();
    if(!j.ok)alert('定位失败:'+(j.error||'未知错误'));
  }catch(err){alert('定位失败:'+err)}
  setTimeout(()=>row.classList.remove('opening'),600);
});
// 「其他(自己说)」:不直接发送,把光标送回输入框让用户自己写
chat.addEventListener('click',e=>{const c=e.target.closest('[data-wbother]');if(!c||b.disabled)return;t.focus();t.placeholder='用你自己的话说说看…';});
// Plan 追问候选答案:点一下即作为这一轮的回答发送
chat.addEventListener('click',e=>{const c=e.target.closest('[data-wbq]');if(!c||b.disabled)return;t.value=c.getAttribute('data-wbq')||'';if(!t.value.trim())return;form.requestSubmit();});
// 方案「开工」按钮:切到直接做模式,把方案正文作为任务提交
chat.addEventListener('click',e=>{
  const c=e.target.closest('[data-wbplan]');if(!c||b.disabled)return;
  workMode='build';document.getElementById('modeBuild').classList.add('on');document.getElementById('modePlan').classList.remove('on');
  t.value=c.getAttribute('data-wbplan')||'';
  if(!t.value.trim())return;
  form.requestSubmit();
});

// ---------- 设置面板 ----------
const PRESETS=${JSON.stringify(PROVIDER_PRESETS)};
const setpanel=document.getElementById('setpanel'),apilist=document.getElementById('apilist');
document.getElementById('setbtn').onclick=()=>{setpanel.classList.toggle('open');if(setpanel.classList.contains('open'))loadApis();};
document.getElementById('setclose').onclick=()=>setpanel.classList.remove('open');
// 分类标签:API / 外观
document.querySelectorAll('.sptab').forEach(tb=>{tb.onclick=()=>{
  document.querySelectorAll('.sptab').forEach(x=>x.classList.remove('on'));
  tb.classList.add('on');
  document.getElementById('tab-api').style.display=tb.dataset.tab==='api'?'':'none';
  document.getElementById('tab-look').style.display=tb.dataset.tab==='look'?'':'none';
  document.getElementById('tab-run').style.display=tb.dataset.tab==='run'?'':'none';
  document.getElementById('tab-mem').style.display=tb.dataset.tab==='mem'?'':'none';
  if(tb.dataset.tab==='mem')loadMem();
  if(tb.dataset.tab==='run')loadTuning();
  document.getElementById('tab-update').style.display=tb.dataset.tab==='update'?'':'none';
  if(tb.dataset.tab==='update')updLoad();
};});
// 长期记忆:读 → 渲染 → 增 / 删 / 启用开关(存服务端 runtime/user-memory.json)
let memState={enabled:true,items:[]};
async function loadMem(){
  try{
    const j=await(await fetch('/api/memory')).json();
    if(j&&j.ok){memState.enabled=j.enabled!==false;memState.items=j.items||[];renderMem();const on=document.getElementById('memOn');if(on)on.checked=memState.enabled;}
  }catch(e){}
}
function renderMem(){
  const box=document.getElementById('memList');if(!box)return;
  if(!memState.items.length){
    box.innerHTML='<div style="padding:10px 12px;border:1px dashed var(--border);border-radius:10px;color:var(--muted-2);font-size:12.5px">还没有记录。把希望长期遵守的偏好写在上面的框里,比如配色、命名、交互习惯。</div>';
    return;
  }
  box.innerHTML=memState.items.map(it=>'<div class="memrow"><div class="memtxt">'+esc(it.text)+'</div><button type="button" class="memdel" data-memdel="'+esca(it.id)+'">删除</button></div>').join('');
}
async function memPost(payload){
  try{
    const r=await fetch('/api/memory',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(payload)});
    const j=await r.json();
    if(j&&j.ok){memState.enabled=j.enabled!==false;memState.items=j.items||[];renderMem();}
    else if(j&&j.error)alert(j.error);
    return j;
  }catch(err){alert('保存失败:'+err);return null;}
}
document.getElementById('memAdd').onclick=async()=>{
  const inp=document.getElementById('memInput');const v=(inp.value||'').trim();if(!v)return;
  const j=await memPost({action:'add',text:v});if(j&&j.ok)inp.value='';
};
document.getElementById('memInput').addEventListener('keydown',e=>{
  if(e.key==='Enter'&&!e.isComposing){e.preventDefault();document.getElementById('memAdd').click();}
});
document.getElementById('memOn').onchange=e=>memPost({action:'toggle',enabled:e.target.checked});
document.getElementById('memList').addEventListener('click',e=>{
  const d=e.target.closest('[data-memdel]');if(!d)return;
  memPost({action:'delete',id:d.getAttribute('data-memdel')});
});
// 外观:主题切换(立即生效,localStorage 记住;pink 为主题基线,其余挂 theme-* 类)
function applyTheme(t){
  document.body.classList.remove('theme-blue','theme-white','theme-black');
  if(t==='blue'||t==='white'||t==='black')document.body.classList.add('theme-'+t);
  try{localStorage.setItem('wb_theme',t)}catch(e){}
  document.querySelectorAll('.themeopt').forEach(o=>o.classList.toggle('cur',o.dataset.t===t));
}
document.querySelectorAll('.themeopt').forEach(o=>{o.onclick=()=>applyTheme(o.dataset.t);});
// 界面字体与字号:本地先应用(避免闪烁),再从服务端取真值;改动即存 runtime/settings.json
const FONT_PRESETS={
  default:{css:'"Microsoft YaHei",system-ui,sans-serif'},
  nailao:{css:'"奶酪体","NaiLao","幼圆",YouYuan,"Microsoft YaHei",sans-serif'},
  cute:{css:'"可爱体","方正卡通","幼圆",YouYuan,"Comic Sans MS",sans-serif'},
  hengshui:{css:'"衡水体","楷体",KaiTi,STKaiti,"Microsoft YaHei",serif'},
  song:{css:'"宋体",SimSun,serif'},
};
let uiFont='default',uiFontSize=100;
function applyUi(font,size,persist){
  uiFont=FONT_PRESETS[font]?font:'default';
  const n=Number(size);
  uiFontSize=Math.min(130,Math.max(85,Number.isFinite(n)&&n>0?Math.round(n/5)*5:100));
  document.body.style.fontFamily=FONT_PRESETS[uiFont].css;
  document.body.style.zoom=(uiFontSize/100).toFixed(2);
  document.querySelectorAll('.fontopt').forEach(o=>o.classList.toggle('cur',o.dataset.f===uiFont));
  const sl=document.getElementById('fontSize');if(sl)sl.value=uiFontSize;
  const lab=document.getElementById('fontSizeVal');if(lab)lab.textContent=uiFontSize+'%';
  try{localStorage.setItem('wb_ui',JSON.stringify({font:uiFont,size:uiFontSize}))}catch(e){}
  if(persist){
    fetch('/api/ui',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({font:uiFont,size:uiFontSize})}).catch(()=>{});
  }
}
(function initUi(){
  try{const c=JSON.parse(localStorage.getItem('wb_ui')||'null');if(c)applyUi(c.font,c.size,false);}catch(e){}
  fetch('/api/ui').then(r=>r.json()).then(j=>{if(j&&j.ok)applyUi(j.font,j.size,false)}).catch(()=>{});
})();
document.querySelectorAll('.fontopt').forEach(o=>{o.onclick=()=>applyUi(o.dataset.f,uiFontSize,true)});
(function(){
  const sl=document.getElementById('fontSize');
  if(!sl)return;
  sl.addEventListener('input',()=>applyUi(uiFont,Number(sl.value),false));
  sl.addEventListener('change',()=>applyUi(uiFont,Number(sl.value),true));
})();
const fsReset=document.getElementById('fontSizeReset');
if(fsReset)fsReset.onclick=()=>applyUi(uiFont,100,true);
// 版本更新:检查 → 弹窗(无论结果如何都有反馈) → 下载(轮询实时进度) → 自动退出安装
const UPD={timer:null,onOk:null,busy:false};
function updMB(n){return ((Number(n)||0)/1048576).toFixed(1)+' MB'}
function updShowModal(title,html,okText,onOk,cancelText){
  document.getElementById('updTitle').textContent=title;
  document.getElementById('updModalBody').innerHTML=html;
  const go=document.getElementById('updGo'),cancel=document.getElementById('updCancel');
  go.textContent=okText||'知道了';
  cancel.style.display=cancelText?'':'none';
  if(cancelText)cancel.textContent=cancelText;
  UPD.onOk=onOk||null;
  document.getElementById('updModal').style.display='flex';
}
function updCloseModal(){document.getElementById('updModal').style.display='none';UPD.onOk=null;}
// 前端也要有超时:后端最坏 18s,这里给 25s,超时就明确报错,绝不无限"检查中…"
async function updApi(body,timeoutMs){
  const ctrl=new AbortController();
  const timer=setTimeout(()=>ctrl.abort(),timeoutMs||25000);
  try{
    const r=await fetch('/api/update',body?{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body),signal:ctrl.signal}:{signal:ctrl.signal});
    if(!r.ok)throw new Error('HTTP '+r.status);
    return await r.json();
  }catch(e){
    throw e&&e.name==='AbortError'?new Error('请求超时(网络无响应)'):e;
  }finally{clearTimeout(timer)}
}
function updRender(s){
  if(!s)return;
  const cur=document.getElementById('updCur');
  if(cur&&s.current)cur.textContent='v'+s.current;
  const box=document.getElementById('updBox'),bar=document.getElementById('updBar'),pct=document.getElementById('updPct'),
        ph=document.getElementById('updPhase'),det=document.getElementById('updDetail'),msg=document.getElementById('updMsg');
  const busy=['downloading','merging','verifying','extracting','applying'].includes(s.phase);
  box.style.display=busy?'':'none';
  if(busy){
    bar.style.width=(s.percent||0)+'%';
    pct.textContent=(s.percent||0)+'%';
    const src=s.source==='github'?'GitHub':s.source==='gitee'?'码云镜像':'测试源';
    ph.textContent={downloading:'下载中('+src+')',merging:'合并分卷…',verifying:'校验安装包…',extracting:'解压安装中,工坊即将重启…',applying:'正在安装,工坊即将退出…'}[s.phase]||'处理中…';
    det.textContent=s.total?updMB(s.received)+' / '+updMB(s.total)+(s.speed?' · '+updMB(s.speed)+'/s':''):'';
    msg.textContent='';
  }
  if(s.phase==='error'){msg.style.color='#c0392b';msg.textContent='更新失败:'+(s.error||'未知错误');}
  else if(s.phase==='done'){msg.style.color='';msg.textContent='已是最新版本';}
}
async function updLoad(){try{updRender(await updApi(null,8000))}catch(e){}}
function updSetBusy(on){
  UPD.busy=on;
  const btn=document.getElementById('updCheck');
  btn.disabled=on;
  btn.style.opacity=on?'.55':'1';
  btn.style.cursor=on?'default':'pointer';
  btn.textContent=on?'检查中…':'检查更新';
}
async function updCheck(){
  if(UPD.busy)return;
  const msg=document.getElementById('updMsg');msg.style.color='';msg.textContent='检查中…';
  updSetBusy(true);
  try{
    const j=await updApi({action:'check'},25000);
    updRender(j);
    if(!j.ok||j.phase==='error'){
      const why=j.error||'未知错误';
      msg.style.color='#c0392b';msg.textContent='检查失败:'+why;
      updShowModal('检查更新失败','<span style="color:#c0392b">'+why+'</span><br><span style="color:var(--muted-2);font-size:12px">常见原因:网络不通、代理拦截、GitHub 与码云都不可达。可以稍后重试。</span>','重试',()=>setTimeout(updCheck,50),'关闭');
      return;
    }
    const srcName=j.source==='github'?'GitHub':j.source==='gitee'?'码云镜像':'本地测试源';
    if(j.hasUpdate){
      msg.textContent='发现新版本 v'+j.latest;
      updShowModal('发现新版本',
        '当前版本 v'+j.current+' → 最新版本 <b>v'+j.latest+'</b><br>来源:'+srcName+
        '<br><span style="color:var(--muted-2);font-size:12px">更新时工坊会自动退出并重启;runtime(密钥/会话/记忆)与 workspace(插件项目)都会保留。</span>',
        '立即更新',updStart,'以后再说');
    }else{
      msg.textContent='已是最新版本(v'+j.current+')';
      updShowModal('已是最新版本',
        '当前 v'+j.current+',已是最新。<br>来源:'+srcName+' · 检查时间 '+new Date().toLocaleTimeString()+
        '<br><span style="color:var(--muted-2);font-size:12px">有新版本发布时,这里会提示并可以一键升级。</span>',
        '知道了',null);
    }
  }catch(e){
    const why=e.message||'检查失败';
    msg.style.color='#c0392b';msg.textContent='检查失败:'+why;
    updShowModal('检查更新失败','<span style="color:#c0392b">'+why+'</span><br><span style="color:var(--muted-2);font-size:12px">请检查网络后重试。</span>','重试',()=>setTimeout(updCheck,50),'关闭');
  }finally{
    updSetBusy(false);
  }
}
function updStart(){
  updCloseModal();
  updApi({action:'start'},15000).then(updRender).catch(()=>{});
  if(UPD.timer)clearInterval(UPD.timer);
  UPD.timer=setInterval(async()=>{
    try{
      const j=await updApi(null,5000);updRender(j);
      if(!['downloading','merging','verifying','extracting','applying'].includes(j.phase)){clearInterval(UPD.timer);UPD.timer=null;}
    }catch(e){/* 安装时进程退出,轮询自然失败 */}
  },600);
}
updLoad();
document.getElementById('updCheck').onclick=updCheck;
document.getElementById('updGo').onclick=()=>{const fn=UPD.onOk;updCloseModal();if(fn)fn();};
document.getElementById('updCancel').onclick=updCloseModal;
document.getElementById('updModal').onclick=e=>{if(e.target.id==='updModal')updCloseModal();};
// 严格校验开关(读后端状态,切换立即生效)
(async()=>{try{const j=await(await fetch('/api/strict')).json();document.getElementById('strictToggle').checked=!!j.strictVerify;}catch(e){}})();
document.getElementById('strictToggle').onchange=async e=>{
  try{
    const r=await fetch('/api/strict',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({strictVerify:e.target.checked})});
    const j=await r.json();
    if(j.ok){e.target.checked=j.strictVerify;}
  }catch(err){e.target.checked=!e.target.checked;}
};
// 运行参数(任务超时 / 无响应超时 / 重试次数 / 重试间隔):读后端 → 填输入;保存或恢复默认即生效
const TUNE_DEFAULTS={retryMax:2,retryDelaySec:3,timeoutSec:1500,stallTimeoutSec:360};
const TUNE_KEYS=['timeoutSec','stallTimeoutSec','retryMax','retryDelaySec'];
async function loadTuning(){
  try{
    const j=await(await fetch('/api/retry')).json();
    if(j&&j.ok){for(const k of TUNE_KEYS){const el=document.getElementById(k);if(el&&j[k]!==undefined)el.value=j[k];}}
  }catch(e){}
}
loadTuning();
async function saveTuning(payload,okText){
  const msg=document.getElementById('tuneMsg');
  try{
    const r=await fetch('/api/retry',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(payload)});
    const j=await r.json();
    if(j&&j.ok){
      for(const k of TUNE_KEYS){const el=document.getElementById(k);if(el&&j[k]!==undefined)el.value=j[k];}
      msg.textContent=okText||'已保存';setTimeout(()=>{if(msg.textContent===(okText||'已保存'))msg.textContent='';},1800);
    }else msg.textContent=(j&&j.error)||'保存失败';
  }catch(e){msg.textContent='连接出错';}
}
document.getElementById('tuneSave').onclick=()=>{
  const v={};for(const k of TUNE_KEYS)v[k]=Number(document.getElementById(k).value);
  saveTuning(v);
};
document.getElementById('tuneReset').onclick=()=>saveTuning(TUNE_DEFAULTS,'已恢复默认');
// 上下文容量设置:读后端设置 → 填 K 数;预设/保存即生效,底部容量环跟随
loadCtxLimit();
async function saveCtx(){
  const k=Number(document.getElementById('ctxInput').value);
  if(!k||k<=0)return;
  try{
    const r=await fetch('/api/context',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({contextLimit:Math.round(k*1000)})});
    const j=await r.json();
    if(j.ok){ctxLimit=j.effectiveLimit||j.contextLimit;ctxSource=j.source||'global';ctxApiName=j.apiName||'';updateCtxBar();}
  }catch(e){}
}
document.getElementById('ctxSave').onclick=saveCtx;
document.querySelectorAll('.ctxpreset').forEach(btn=>{btn.onclick=()=>{document.getElementById('ctxInput').value=Math.round(Number(btn.dataset.ctx)/1000);saveCtx();};});
// 服务商预设 chips(设置面板):点一下填好添加表单
(function(){
  const box=document.getElementById('apiPresets');if(!box)return;
  for(const p of PRESETS){
    const c=document.createElement('span');c.className='chip';c.style.cursor='pointer';c.textContent=p.label;
    c.onclick=()=>{
      // 只填服务商地址;协议保持当前选择(默认 OpenAI 兼容),模型 ID 不代填
      if(p.protocol&&p.protocol!=='openai'&&document.getElementById('apiProto').value!==p.protocol)document.getElementById('apiProto').value=p.protocol;
      document.getElementById('apiURL').value=p.baseURL;
      if(!document.getElementById('apiName').value.trim())document.getElementById('apiName').value=p.label;
      const m=document.getElementById('apiMsg');m.className='';m.textContent='已填好「'+p.label+'」的服务地址;密钥和模型名称请到 '+p.keyHost+' 查看(模型名照官网文档填,保存并启用时会测连通性)';
    };
    box.appendChild(c);
  }
})();
try{applyTheme(localStorage.getItem('wb_theme')||'pink')}catch(e){}
async function loadApis(){
  const j=await(await fetch('/api/apis')).json();apilist.innerHTML='';
  if(!j.items.length){apilist.innerHTML='<div class="apirow"><span class="ad">还没有配置,在下方添加第一套 API</span></div>';return}
  for(const a of j.items){
    const d=document.createElement('div');d.className='apirow'+(a.id===j.enabledId?' cur':'');
    const ownCtx=a.contextLimit?fmtCtx(a.contextLimit):('跟随全局 '+fmtCtx(j.globalContextLimit||128000));
    d.innerHTML='<span class="an">'+esc(a.name||a.modelID)+'</span><span class="ad">'+esc(a.protocol&&a.protocol!=='openai'?a.protocol+' · ':'')+esc(a.baseURL)+' · '+esc(a.modelID)+' · 密钥…'+esc(a.keyTail)+'</span>'
      +'<span class="ctxchip" title="点击设置这套 API 的上下文窗口">🧠 '+esc(ownCtx)+'</span>'
      +(a.id===j.enabledId?'<button disabled>使用中</button>':'<button class="use">启用</button>')
      +'<button class="del">删除</button>';
    d.querySelector('.ctxchip')?.addEventListener('click',async e=>{
      e.stopPropagation();
      const cur=a.contextLimit?Math.round(a.contextLimit/1000):'';
      const input=prompt('这套 API 的上下文窗口(K tokens);留空 = 跟随全局默认 '+Math.round((j.globalContextLimit||128000)/1000)+'K:',cur);
      if(input===null)return;
      const k=Number(String(input).trim());
      const body=String(input).trim()===''?{contextLimit:null}:{contextLimit:Math.round(k*1000)};
      if(String(input).trim()!==''&&(!k||k<=0)){alert('填 K 数,比如 128 或 200;留空表示跟随全局');return}
      try{
        const r=await fetch('/api/apis/'+a.id+'/context',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});
        const jj=await r.json();
        const msg=document.getElementById('apiMsg');
        if(jj.ok){msg.className='';msg.textContent='✅ 上下文窗口已更新';loadApis();loadCtxLimit();}
        else{msg.className='bad';msg.textContent=jj.error||'设置失败'}
      }catch(err){alert('设置失败:'+err)}
    });
    d.querySelector('.use')?.addEventListener('click',async e=>{
      const btn=e.target;btn.disabled=true;btn.textContent='切换中…';
      const msg=document.getElementById('apiMsg');
      try{
        const r=await fetch('/api/apis/'+a.id,{method:'POST'});
        const j=await r.json();
        if(j.ok){msg.className='';msg.textContent='✅ 已启用'+(j.warning?'(注意:'+j.warning+')':'');loadApis();loadCtxLimit();}
        else{msg.className='bad';msg.textContent=j.error||'切换失败';btn.disabled=false;btn.textContent='启用';}
      }catch(err){msg.className='bad';msg.textContent='连接出错:'+err;btn.disabled=false;btn.textContent='启用';}
    });
    d.querySelector('.del').addEventListener('click',async()=>{if(!confirm('删除「'+(a.name||a.modelID)+'」?'))return;await fetch('/api/apis/'+a.id,{method:'DELETE'});loadApis();loadCtxLimit();});
    apilist.appendChild(d);
  }
}
async function saveApi(enable){
  const msg=document.getElementById('apiMsg');msg.className='';msg.textContent='保存中…';
  try{
    // 上下文窗口按 K 填;留空则不传(跟随全局)
    const ctxK=String(document.getElementById('apiCtx').value||'').trim();
    const payload={name:document.getElementById('apiName').value.trim(),baseURL:document.getElementById('apiURL').value.trim(),apiKey:document.getElementById('apiKey').value.trim(),model:document.getElementById('apiModel').value.trim(),protocol:document.getElementById('apiProto').value,enable};
    if(ctxK!=='')payload.contextLimit=Math.round(Number(ctxK)*1000);
    const r=await fetch('/api/apis',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(payload)});
    const j=await r.json();
    if(j.ok){
      msg.textContent=(enable?('✅ '+(j.warning||'已保存并启用(引擎重启中)')):'✅ 已保存')+(payload.contextLimit?' · 上下文 '+fmtCtx(payload.contextLimit):'');
      loadApis();loadCtxLimit();
      document.getElementById('apiName').value=document.getElementById('apiURL').value=document.getElementById('apiKey').value=document.getElementById('apiModel').value=document.getElementById('apiCtx').value='';
    }
    else{msg.className='bad';msg.textContent=j.error||'保存失败'}
  }catch(e){msg.className='bad';msg.textContent='连接出错:'+e}
}
document.getElementById('apiSave').onclick=()=>saveApi(true);
document.getElementById('apiSaveOnly').onclick=()=>saveApi(false);
// 主动测试:只测不存,填完可以先确认这一套能不能连上
document.getElementById('apiTest').onclick=async()=>{
  const btn=document.getElementById('apiTest'),msg=document.getElementById('apiMsg');
  const baseURL=(document.getElementById('apiURL').value||'').trim();
  const apiKey=(document.getElementById('apiKey').value||'').trim();
  const model=(document.getElementById('apiModel').value||'').trim();
  const protocol=document.getElementById('apiProto').value;
  if(!baseURL||!apiKey||!model){msg.textContent='地址/密钥/模型名称都要填,才能测';msg.className='bad';return}
  btn.disabled=true;const old=btn.textContent;btn.textContent='🔌 测试中…';msg.className='';msg.textContent='';
  try{
    const r=await fetch('/api/test',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({baseURL,apiKey,model,protocol})});
    const j=await r.json();
    if(j.ok&&j.passed){msg.textContent='✅ 连上了'+(j.model?'('+j.model+')':'');msg.className='good';}
    else{msg.textContent='😢 没连上:'+(j.error||'未知原因');msg.className='bad';}
  }catch(e){msg.textContent='😢 连接出错:'+e;msg.className='bad';}
  btn.disabled=false;btn.textContent=old;
};

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
  } catch (e) {
    if (existsSync(SESSIONS_FILE)) console.warn("[wb-studio] 会话文件读取失败,已按空会话启动(文件可能损坏):", e.message)
  }
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
  writeJsonAtomic(SESSIONS_FILE, sessions)
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
let lastRetryRestarted = false // 最近一次「重试前是否已重启过引擎」,避免任务结束再重复重启

// ---------- 模型无响应:自动重试 ----------
// 触发条件与判据都在 planModelRetry 里(无产出才重试;密钥错/额度耗尽/用户停止不重试)。
function retryMaxCount() {
  const env = Math.floor(Number(process.env.WB_RETRY_MAX))
  if (Number.isFinite(env) && env >= 0) return Math.min(5, env)
  const v = Math.floor(Number(config.retryMax))
  return Number.isFinite(v) ? Math.max(0, Math.min(5, v)) : 0
}
function retryBaseDelayMs() {
  const s = Number(config.retryDelaySec)
  return (Number.isFinite(s) && s > 0 ? Math.min(60, s) : 3) * 1000
}
/** 可被「停止」打断的等待:用户点停止时立刻醒来,不用等满退避 */
function sleepAbortable(ms) {
  const signal = currentAbort && currentAbort.signal
  return new Promise((resolve) => {
    if (signal && signal.aborted) return resolve()
    const t = setTimeout(resolve, ms)
    if (signal) signal.addEventListener("abort", () => { clearTimeout(t); resolve() }, { once: true })
  })
}
/** 把异常统一成驱动层的结果形状,后续记账/报错逻辑不用分叉 */
function failureResult(thrown) {
  return {
    ok: false, error: String((thrown && thrown.message) || thrown || "未知错误"),
    sessionId: null, idle: true, stopped: false, warnings: [],
    stats: { permissions: 0, questions: 0 },
    reply: null, thinking: null, usage: null, artifacts: [], modifiedFiles: [],
  }
}

/** 重试用尽且模型一个字都没吐出来时,给一句能看懂的话(不能留空错误,页面会显示空白) */
function noResponseError(retries) {
  const n = (retries || []).length
  return `模型连续 ${n + 1} 次没有返回任何内容(已自动重试 ${n} 次)。可能是接口卡住或额度不足,稍后再试,或到 ⚙ 设置换一个更稳定的服务商`
}

/**
 * 跑一次任务;失败且「没有任何产出」时按策略自动重试。
 * kind="engine": 无响应/连接类失败通常是引擎被毒化,重试前先重启引擎,否则必然再次挂起。
 * kind="cli":    外部 CLI 底座无常驻进程,直接重跑即可。
 */
async function runTaskWithRetry(kind, run) {
  const maxRetries = retryMaxCount()
  const retries = []
  lastRetryRestarted = false
  for (let attempt = 1; ; attempt++) {
    let result = null
    let thrown = null
    try {
      result = await run()
    } catch (e) {
      thrown = e
    }
    const aborted = Boolean(currentAbort && currentAbort.signal.aborted)
    const plan = result && result.ok
      ? null
      : planModelRetry({
          attemptsMade: attempt,
          maxRetries,
          result,
          error: thrown ? String((thrown && thrown.message) || thrown) : undefined,
          aborted,
          baseDelayMs: retryBaseDelayMs(),
        })
    if (!plan) {
      if (!result) result = failureResult(thrown)
      if (retries.length) result.retries = retries
      return result
    }
    if (kind === "engine") {
      const needRestart = plan.kind !== "error" || Boolean(thrown) || !(await engineHealthy())
      if (needRestart) {
        console.log(`[wb-studio] 第 ${attempt} 次任务失败(${plan.kind}),重启引擎后自动重试…`)
        enginePoisoned = false
        lastRetryRestarted = true
        stopEngine()
        await sleepAbortable(1200)
        const eng = await ensureEngine()
        if (eng && eng.error) {
          const r = failureResult(thrown)
          r.error = `模型无响应,自动重试前重启引擎失败:${eng.error}`
          r.retries = retries.concat([{ attempt, kind: plan.kind, reason: plan.reason, waitMs: plan.waitMs }])
          return r
        }
      }
    }
    retries.push({ attempt, kind: plan.kind, reason: plan.reason, waitMs: plan.waitMs })
    console.log(`[wb-studio] 模型无响应(${plan.kind}),${Math.round(plan.waitMs / 1000)}s 后自动重试 ${attempt + 1}/${maxRetries + 1}`)
    await sleepAbortable(plan.waitMs)
    if (currentAbort && currentAbort.signal.aborted) {
      const r = failureResult(new Error("任务已停止"))
      r.stopped = true
      r.error = "任务已停止"
      r.retries = retries
      return r
    }
  }
}

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
    // 没有 body 的写请求(如 curl -X DELETE、POST 建会话)没有 JSON 可解析,不该被 415 挡掉
    // 注意:http.client/curl 发 POST 时会带 content-length: 0,这也算"没有 body"
    const cl = req.headers["content-length"]
    const bodyless = (!cl || cl === "0") && !req.headers["transfer-encoding"]
    if (!bodyless && !ct.startsWith("application/json")) {
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
    // ---------- 主动测试连接(只测不存):给页面上的「测一下」按钮用 ----------
    if (req.method === "POST" && url.pathname === "/api/test") {
      let body = ""
      for await (const ch of req) body += ch
      let b = {}
      try { b = JSON.parse(body) } catch {}
      const baseURL = String(b.baseURL || "").trim().replace(/\/+$/, "")
      const apiKey = String(b.apiKey || "").trim()
      const modelID = String(b.model || "").trim()
      const protocol = String(b.protocol || "openai").trim()
      const bad = (msg) => {
        res.writeHead(400, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify({ ok: false, error: msg }))
      }
      if (!baseURL) return bad("请先填服务地址(Base URL)")
      if (!apiKey || PLACEHOLDER.test(apiKey)) return bad("请先填 API 密钥")
      if (!modelID) return bad("请先填模型名称(Model ID)")
      if (!/^https?:\/\//.test(baseURL)) return bad("API 地址要以 http(s):// 开头")
      const invalid = validateApiFields(baseURL, apiKey, modelID)
      if (invalid) return bad(invalid)
      // 与保存前同一套探测(先非流式,挂住再流式兜底),但完全不落盘
      const test = await testProvider({ baseURL, apiKey, modelID, protocol }, 20_000)
      res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
      res.end(JSON.stringify({
        ok: true,
        passed: !!test.ok,
        error: test.ok ? "" : String(test.error || "连接测试未通过"),
        model: test.model || null,
        hardFail: !test.ok && isHardTestFail(test),
      }))
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
          compactCount: s.compactCount || 0,
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
    // ---------- 手动压缩会话上下文(自动压缩之外的兜底入口) ----------
    let cpm = url.pathname.match(/^\/api\/sessions\/([^/]+)\/compact$/)
    if (req.method === "POST" && cpm) {
      const s = getSession(cpm[1])
      if (!s) { res.writeHead(404, { "content-type": "application/json; charset=utf-8" }); res.end(JSON.stringify({ ok: false, error: "会话不存在" })); return }
      const info = await compactSession(s, { force: true, reason: "manual" })
      if (info) {
        s.messages.push({ role: "assistant", content: "", notice: "compact", at: Date.now(), compact: info })
        saveSessions()
      }
      res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
      res.end(JSON.stringify({
        ok: true,
        compact: info,
        summary: s.summary || "",
        summarySource: s.summarySource || null,
        compactCount: s.compactCount || 0,
        compactRatio: COMPACT_RATIO,
      }))
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
        globalContextLimit: config.contextLimit,
        items: apis.items.map((a) => ({
          id: a.id, name: a.name, baseURL: a.baseURL, modelID: a.modelID,
          protocol: a.protocol || "openai",
          keyTail: a.apiKey ? a.apiKey.slice(-4) : "",
          contextLimit: normCtxLimit(a.contextLimit) || null, // null = 跟随全局
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
      // 上下文窗口:留空(=null)跟随全局默认,填了就用这套 API 自己的
      let ownCtx = null
      if (b.contextLimit !== undefined && b.contextLimit !== null && String(b.contextLimit).trim() !== "") {
        ownCtx = normCtxLimit(b.contextLimit)
        if (!ownCtx) {
          res.writeHead(400, { "content-type": "application/json; charset=utf-8" })
          res.end(JSON.stringify({ ok: false, error: "上下文容量要在 1000 ~ 10000000 tokens 之间(留空则跟随全局)" }))
          return
        }
      }
      apis.items.push({ id, name, baseURL, apiKey, modelID, protocol, ...(ownCtx ? { contextLimit: ownCtx } : {}) })
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
    // 单个 API 的上下文窗口:传 null / 空 表示清除,恢复跟随全局
    let cm = url.pathname.match(/^\/api\/apis\/([^/]+)\/context$/)
    if (req.method === "POST" && cm) {
      const a = apis.items.find((x) => x.id === cm[1])
      if (!a) { res.writeHead(404, { "content-type": "application/json; charset=utf-8" }); res.end(JSON.stringify({ ok: false, error: "配置不存在" })); return }
      let body = ""
      for await (const ch of req) body += ch
      let b = {}
      try { b = JSON.parse(body) } catch {}
      const raw = b.contextLimit
      if (raw === null || raw === undefined || String(raw).trim() === "") {
        delete a.contextLimit
      } else {
        const v = normCtxLimit(raw)
        if (!v) {
          res.writeHead(400, { "content-type": "application/json; charset=utf-8" })
          res.end(JSON.stringify({ ok: false, error: "上下文容量要在 1000 ~ 10000000 tokens 之间(留空则跟随全局)" }))
          return
        }
        a.contextLimit = v
      }
      saveApis()
      res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
      res.end(JSON.stringify({ ok: true, id: a.id, contextLimit: a.contextLimit || null, ...effectiveContext() }))
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
// ---------- 内置更新:检查 → 下载(GitHub 优先,码云兜底) → 安装 ----------
// 说明:官方发布产物是自解压 Setup.exe(支持 --silent)。运行中的 bun.exe 会被占用,
// 所以先落盘,再交给一个等待本进程退出的 cmd 去安装,装完自动启动新版本并删临时文件。
const GH_REPO = "fengdiehualian/neko-plugin-workshop"
const GITEE_REPO = "maple-butterfly-flower-love/neko-plugin-workshop"
const UPDATE_DIR = join(__dirname, "runtime", "update")
const updateState = {
  phase: "idle", // idle|checking|downloading|merging|verifying|extracting|applying|done|error
  kind: "", // exe | zip
  current: APP_VERSION,
  latest: "",
  source: "",
  notes: "",
  percent: 0,
  received: 0,
  total: 0,
  speed: 0,
  error: "",
  checkedAt: 0,
  sources: [],
  startedAt: 0,
}
let updateTimer = null
let updateRunning = false

function cmpVersion(a, b) {
  const pa = String(a).replace(/^v/i, "").split(/[.+-]/).map((x) => parseInt(x, 10) || 0)
  const pb = String(b).replace(/^v/i, "").split(/[.+-]/).map((x) => parseInt(x, 10) || 0)
  for (let i = 0; i < 3; i++) {
    const d = (pa[i] || 0) - (pb[i] || 0)
    if (d) return d > 0 ? 1 : -1
  }
  return 0
}

function updSnapshot() {
  return {
    ok: true,
    pid: process.pid,
    phase: updateState.phase,
    current: updateState.current,
    latest: updateState.latest,
    source: updateState.source,
    notes: updateState.notes,
    percent: updateState.percent,
    received: updateState.received,
    total: updateState.total,
    speed: updateState.speed,
    error: updateState.error,
    checkedAt: updateState.checkedAt,
    hasUpdate: !!updateState.latest && cmpVersion(updateState.latest, updateState.current) > 0,
  }
}

async function fetchJSON(url, timeoutMs = 6000) {
  const headers = { "user-agent": "neko-workshop/" + APP_VERSION, accept: "application/json" }
  // 环境里可能有失效的 HTTP_PROXY(公司代理/加速器残留):先按默认走,失败再显式绕过代理重试一次。
  // 每次尝试都必须有超时——否则代理挂住时 fetch 永远不返回,前端就卡在"检查中…"。
  const attempts = [{ headers, redirect: "follow" }, { headers, redirect: "follow", proxy: "" }]
  let lastErr = null
  for (const opt of attempts) {
    const ctrl = new AbortController()
    const timer = setTimeout(() => ctrl.abort(), timeoutMs)
    try {
      const r = await fetch(url, { ...opt, signal: ctrl.signal })
      if (!r.ok) throw new Error("HTTP " + r.status)
      return await r.json()
    } catch (e) {
      lastErr = e?.name === "AbortError" ? new Error("请求超时") : e
    } finally {
      clearTimeout(timer)
    }
  }
  throw lastErr || new Error("fetch failed")
}

// 返回 [{source:'github'|'gitee'|'feed', version, notes, assets:[{name,url,size}]}]
async function resolveReleaseSources() {
  const out = []
  // 开发者/离线测试:WB_UPDATE_FEED 指向一个本地 JSON(不暴露给 UI)
  if (process.env.WB_UPDATE_FEED) {
    try {
      const j = await fetchJSON(process.env.WB_UPDATE_FEED)
      out.push({ source: "feed", version: String(j.version || "").replace(/^v/i, ""), notes: j.notes || "", assets: j.assets || [] })
    } catch (e) {
      console.warn("[wb-studio] 更新测试源不可用:", e.message)
    }
  }
  try {
    const j = await fetchJSON(`https://api.github.com/repos/${GH_REPO}/releases/latest`)
    // 优先用绿色包 zip(解压即更新),没有 zip 再用自解压 exe
    const zip = (j.assets || []).find((a) => /\.zip$/i.test(a.name) && /payload|setup|workshop/i.test(a.name))
    const asset = zip || (j.assets || []).find((a) => /\.exe$/i.test(a.name))
    if (asset && j.tag_name) {
      out.push({ source: "github", version: String(j.tag_name).replace(/^v/i, ""), notes: j.body || "", assets: [{ name: asset.name, url: asset.browser_download_url, size: asset.size }] })
    }
  } catch (e) {
    console.warn("[wb-studio] GitHub 版本检查失败:", e.message)
  }
  try {
    const j = await fetchJSON(`https://gitee.com/api/v5/repos/${GITEE_REPO}/releases/latest`)
    const assets = (j.assets || []).filter((a) => /\.(zip|exe|part\d+)$/i.test(a.name))
    const parts = assets.filter((a) => /\.part\d+$/i.test(a.name)).sort((x, y) => Number(x.name.match(/part(\d+)/)[1]) - Number(y.name.match(/part(\d+)/)[1]))
    const single = assets.find((a) => /\.zip$/i.test(a.name) && /payload|setup|workshop/i.test(a.name)) || assets.find((a) => /\.exe$/i.test(a.name))
    const list = single ? [single] : parts
    if (list.length && j.tag_name) {
      out.push({
        source: "gitee",
        version: String(j.tag_name).replace(/^v/i, ""),
        notes: j.body || "",
        assets: list.map((a) => ({ name: a.name, url: a.browser_download_url, size: a.size })),
      })
    }
  } catch (e) {
    console.warn("[wb-studio] 码云版本检查失败:", e.message)
  }
  return out
}

async function checkUpdate() {
  updateState.phase = "checking"
  updateState.error = ""
  updateState.percent = 0
  updateState.received = 0
  updateState.total = 0
  updateState.speed = 0
  try {
    // 总预算 18 秒:超时就把错误抛给前端,不让她一直盯着"检查中…"
    const list = await Promise.race([
      resolveReleaseSources(),
      new Promise((_, rej) => setTimeout(() => rej(new Error("检查超时(网络无响应)")), 18000)),
    ])
    updateState.checkedAt = Date.now()
    if (!list.length) throw new Error("GitHub 与码云都没取到版本信息(检查网络或加速器)")
    const best = list.reduce((a, b) => (cmpVersion(b.version, a.version) > 0 ? b : a))
    updateState.latest = best.version
    updateState.source = best.source
    updateState.notes = String(best.notes || "").slice(0, 1500)
    updateState.sources = list
    updateState.phase = "idle"
    return updSnapshot()
  } catch (e) {
    updateState.phase = "error"
    updateState.error = String(e.message || e)
    return updSnapshot()
  }
}

function updateTotalBytes() {
  const src = updateState.sources.find((s) => s.source === updateState.source)
  if (!src) return 0
  return src.assets.reduce((n, a) => n + (Number(a.size) || 0), 0)
}

async function downloadOne(url, dest) {
  const fs = require("node:fs")
  let res = await fetch(url, { headers: { "user-agent": "neko-workshop/" + APP_VERSION }, redirect: "follow" })
  if (!res.ok || !res.body) {
    // 代理挡了直连/大文件时,换绕过代理再试一次
    res = await fetch(url, { headers: { "user-agent": "neko-workshop/" + APP_VERSION }, redirect: "follow", proxy: "" })
  }
  if (!res.ok || !res.body) throw new Error("下载失败 HTTP " + res.status)
  const out = fs.createWriteStream(dest)
  const reader = res.body.getReader()
  let lastAt = Date.now()
  let lastBytes = updateState.received
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      updateState.received += value.length
      updateState.percent = updateState.total ? Math.min(100, Math.round((updateState.received / updateState.total) * 100)) : 0
      const now = Date.now()
      if (now - lastAt > 500) {
        updateState.speed = Math.round(((updateState.received - lastBytes) * 1000) / (now - lastAt))
        lastAt = now
        lastBytes = updateState.received
      }
      if (!out.write(Buffer.from(value))) await new Promise((r) => out.once("drain", r))
    }
  } finally {
    await new Promise((res2) => out.end(res2))
  }
}

async function mergeParts(parts, dest) {
  const fs = require("node:fs")
  const out = fs.createWriteStream(dest)
  try {
    for (const p of parts) {
      await new Promise((res, rej) => {
        const rs = fs.createReadStream(p)
        rs.on("error", rej)
        rs.on("end", res)
        rs.pipe(out, { end: false })
      })
    }
  } finally {
    await new Promise((r) => out.end(r))
  }
}

async function downloadFrom(src) {
  const fs = require("node:fs")
  updateState.source = src.source
  updateState.phase = "downloading"
  updateState.received = 0
  updateState.percent = 0
  updateState.total = src.assets.reduce((n, a) => n + (Number(a.size) || 0), 0)
  fs.rmSync(UPDATE_DIR, { recursive: true, force: true })
  fs.mkdirSync(UPDATE_DIR, { recursive: true })
  const firstName = String((src.assets[0] || {}).name || "")
  const kind = /\.zip$/i.test(firstName) || src.assets.length > 1 ? (/\.zip$/i.test(firstName) ? "zip" : "exe") : "exe"
  updateState.kind = kind
  const exe = join(UPDATE_DIR, kind === "zip" ? "payload.zip" : "setup.exe")
  if (src.assets.length === 1) {
    await downloadOne(src.assets[0].url, exe)
  } else {
    const parts = []
    for (const a of src.assets) {
      const p = join(UPDATE_DIR, String(a.name).replace(/[^\w.-]/g, "_"))
      await downloadOne(a.url, p)
      parts.push(p)
    }
    updateState.phase = "merging"
    await mergeParts(parts, exe)
    for (const p of parts) fs.rmSync(p, { force: true })
  }
  updateState.phase = "verifying"
  const head = fs.readFileSync(exe).subarray(0, 2).toString("latin1")
  if (kind === "zip" ? head !== "PK" : head !== "MZ") throw new Error("下载的更新包无效(可能被代理/镜像截断)")
  if (updateState.total && updateState.received < updateState.total * 0.95) throw new Error("下载不完整")
  return exe
}

function scheduleApply(filePath, kind) {
  const fs = require("node:fs")
  const helper = join(UPDATE_DIR, "apply-update.cmd")
  const sys = "%SystemRoot%\\System32"
  const apply =
    kind === "zip"
      ? [
          // 绿色包:解压覆盖安装目录(tar 是 Windows 10 自带)。全部用绝对路径,
          // 避免 PATH 里混进 Git/MSYS 的同名 find/tar 导致脚本行为异常。
          `cd /d "${__dirname}"`,
          `"${sys}\\tar.exe" -xf "${filePath}" -C "${__dirname}"`,
          `"${sys}\\cmd.exe" /c start "" "${join(__dirname, "start.cmd")}"`,
        ]
      : [`"${sys}\\cmd.exe" /c start "" /wait "${filePath}"`]
  const lines = [
    "@echo off",
    "setlocal",
    "set /a n=0",
    ":wait",
    "set /a n+=1",
    "if %n% GTR 90 goto apply",
    // PID 会被复用:同时按镜像名过滤;找不到 tasklist 就当进程已退出
    `"${sys}\\tasklist.exe" /FI "PID eq ${process.pid}" /FI "IMAGENAME eq bun.exe" 2>nul | "${sys}\\findstr.exe" /I "bun.exe" >nul`,
    "if not errorlevel 1 (",
    "  ping -n 2 127.0.0.1 >nul",
    "  goto wait",
    ")",
    ":apply",
    ...apply,
    `del /f /q "${filePath}" >nul 2>&1`,
    `rd /s /q "${UPDATE_DIR}" >nul 2>&1`,
    "exit /b 0",
  ]
  fs.writeFileSync(helper, lines.join("\r\n") + "\r\n", "ascii")
  const { spawn } = require("node:child_process")
  const child = spawn("cmd.exe", ["/c", helper], { detached: true, stdio: "ignore", cwd: __dirname, windowsHide: true })
  child.unref()
  updateState.phase = kind === "zip" ? "extracting" : "applying"
  console.log("[wb-studio] 更新包已就绪,退出后由安装器接管:", filePath)
  // 给前端留出最后一次状态轮询的时间,再退出(bun.exe 解锁后安装器/解压才能覆盖文件)
  setTimeout(() => {
    try { stopEngine() } catch {}
    try { server.close() } catch {}
    setTimeout(() => process.exit(0), 300)
  }, 1500)
}

async function runUpdate() {
  if (updateRunning) return updSnapshot()
  updateRunning = true
  updateState.phase = "downloading"
  updateState.received = 0
  updateState.percent = 0
  updateState.speed = 0
  updateState.error = ""
  try {
  if (!updateState.sources.length) await checkUpdate()
  console.log("[wb-studio] 开始更新:current=" + updateState.current + " latest=" + updateState.latest + " sources=" + updateState.sources.map((x) => x.source).join(","))
  if (cmpVersion(updateState.latest, updateState.current) <= 0) {
    updateState.phase = "done"
    return updSnapshot()
  }
  updateState.startedAt = Date.now()
  updateState.error = ""
  // 顺序:测试源 > GitHub > 码云。正式环境没有 feed,等价于 GitHub 优先、码云兜底
  const rank = { feed: 0, github: 1, gitee: 2 }
  const ordered = [...updateState.sources].sort((a, b) => (rank[a.source] ?? 9) - (rank[b.source] ?? 9))
  let lastErr = ""
  for (const src of ordered) {
    try {
      const exe = await downloadFrom(src)
      scheduleApply(exe, updateState.kind)
      return updSnapshot()
    } catch (e) {
      lastErr = `${src.source}: ${e.message || e}`
      console.warn("[wb-studio] 更新下载失败,尝试下一个源:", lastErr)
      updateState.speed = 0
    }
  }
  updateState.phase = "error"
  updateState.error = lastErr || "下载失败"
  try { require("node:fs").rmSync(UPDATE_DIR, { recursive: true, force: true }) } catch {}
  return updSnapshot()
  } finally {
    updateRunning = false
  }
}


    // ---------- 内置更新(检查/下载/安装) ----------
    if (url.pathname === "/api/update") {
      const json = (obj) => {
        res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify(obj))
      }
      if (req.method === "GET") return json(updSnapshot())
      if (req.method === "POST") {
        let body = ""
        for await (const ch of req) body += ch
        let b = {}
        try { b = JSON.parse(body || "{}") } catch {}
        const action = String(b.action || "")
        if (action === "check") return json(await checkUpdate())
        if (action === "start") {
          runUpdate().catch((e) => {
            updateState.phase = "error"
            updateState.error = String(e.message || e)
          })
          return json({ ...updSnapshot(), phase: "downloading" })
        }
        return json({ ok: false, error: "未知动作:" + action })
      }
    }
    // ---------- 界面字体与字号 ----------
    if (url.pathname === "/api/ui") {
      const fonts = ["default", "nailao", "cute", "hengshui", "song"]
      const out = () => {
        res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify({ ok: true, font: config.uiFont, size: config.uiFontSize, fonts }))
      }
      if (req.method === "GET") return out()
      if (req.method === "POST") {
        let body = ""
        for await (const ch of req) body += ch
        let b = {}
        try { b = JSON.parse(body) } catch {}
        const bad = (msg) => {
          res.writeHead(400, { "content-type": "application/json; charset=utf-8" })
          res.end(JSON.stringify({ ok: false, error: msg }))
        }
        if (b.font !== undefined) {
          const f = String(b.font)
          if (!fonts.includes(f)) return bad("未知字体:" + f)
          config.uiFont = f
        }
        if (b.size !== undefined) {
          const v = Math.floor(Number(b.size))
          if (!Number.isFinite(v) || v < 85 || v > 130) return bad("字号要在 85 ~ 130 之间")
          config.uiFontSize = v
        }
        saveUserSettings()
        console.log(`[wb-studio] 外观已更新:font=${config.uiFont} size=${config.uiFontSize}%`)
        return out()
      }
    }
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
    // ---------- 模型无响应自动重试(用户偏好,落在 settings.json) ----------
    if (url.pathname === "/api/retry") {
      if (req.method === "GET") {
        res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify({
          ok: true,
          retryMax: retryMaxCount(),
          retryDelaySec: config.retryDelaySec,
          timeoutSec: config.timeoutSec,
          stallTimeoutSec: config.stallTimeoutSec,
          defaults: { retryMax: 2, retryDelaySec: 3, timeoutSec: 1500, stallTimeoutSec: 360 },
        }))
        return
      }
      if (req.method === "POST") {
        let body = ""
        for await (const ch of req) body += ch
        let b = {}
        try { b = JSON.parse(body) } catch {}
        const bad = (msg) => {
          res.writeHead(400, { "content-type": "application/json; charset=utf-8" })
          res.end(JSON.stringify({ ok: false, error: msg }))
        }
        // 四项都允许单独改:先全部校验到"候选值",一处不合法就整体不落地(避免半截生效)
        const has = (k) => b[k] !== undefined && b[k] !== null && b[k] !== ""
        const num = (k) => Math.floor(Number(b[k]))
        const next = {
          retryMax: config.retryMax,
          retryDelaySec: config.retryDelaySec,
          timeoutSec: config.timeoutSec,
          stallTimeoutSec: config.stallTimeoutSec,
        }
        if (has("retryMax")) {
          const v = num("retryMax")
          if (!Number.isFinite(v) || v < 0 || v > 5) return bad("重试次数要在 0 ~ 5 之间(0 = 关闭自动重试)")
          next.retryMax = v
        }
        if (has("retryDelaySec")) {
          const v = num("retryDelaySec")
          if (!Number.isFinite(v) || v < 1 || v > 60) return bad("重试间隔要在 1 ~ 60 秒之间")
          next.retryDelaySec = v
        }
        if (has("timeoutSec")) {
          const v = num("timeoutSec")
          if (!Number.isFinite(v) || v < 60 || v > 7200) return bad("任务超时要在 60 ~ 7200 秒之间(1 ~ 120 分钟)")
          next.timeoutSec = v
        }
        if (has("stallTimeoutSec")) {
          const v = num("stallTimeoutSec")
          if (!Number.isFinite(v) || v < 30 || v > 1800) return bad("无响应超时要在 30 ~ 1800 秒之间")
          next.stallTimeoutSec = v
        }
        if (!["retryMax", "retryDelaySec", "timeoutSec", "stallTimeoutSec"].some(has)) return bad("没有要保存的项")
        if (next.stallTimeoutSec >= next.timeoutSec) {
          return bad(`无响应超时(${next.stallTimeoutSec}s)必须小于任务超时(${next.timeoutSec}s)`)
        }
        Object.assign(config, next)
        saveUserSettings()
        console.log(`[wb-studio] 运行参数已更新:timeout=${config.timeoutSec}s stall=${config.stallTimeoutSec}s retry=${config.retryMax}× delay=${config.retryDelaySec}s`)
        res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify({
          ok: true,
          retryMax: config.retryMax,
          retryDelaySec: config.retryDelaySec,
          timeoutSec: config.timeoutSec,
          stallTimeoutSec: config.stallTimeoutSec,
        }))
        return
      }
    }
    // ---------- 用户长期记忆:跨会话记住个性化设计偏好 ----------
    if (url.pathname === "/api/memory") {
      const out = () => {
        res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify({ ok: true, enabled: userMemory.enabled, items: userMemory.items, maxItems: MEMORY_MAX_ITEMS, maxText: MEMORY_MAX_TEXT }))
      }
      // 长期记忆是用户私人偏好:读也要 token(GET 默认不走全局写入门禁)
      if (String(req.headers["x-wb-token"] || "") !== API_TOKEN) {
        res.writeHead(403, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify({ ok: false, error: "token 校验失败(请从本工作台页面操作)" }))
        return
      }
      if (req.method === "GET") return out()
      if (req.method === "POST") {
        let body = ""
        let tooBig = false
        for await (const ch of req) {
          body += ch
          if (body.length > 64 * 1024) { tooBig = true; break }
        }
        if (tooBig) {
          res.writeHead(413, { "content-type": "application/json; charset=utf-8" })
          res.end(JSON.stringify({ ok: false, error: "请求体过大" }))
          return
        }
        let b = {}
        try { b = JSON.parse(body) } catch {}
        const act = String(b.action || "")
        const id = String(b.id || "").slice(0, 64)
        const text = String(b.text || "").replace(/\s+/g, " ").trim()
        const bad = (msg) => {
          res.writeHead(400, { "content-type": "application/json; charset=utf-8" })
          res.end(JSON.stringify({ ok: false, error: msg }))
        }
        if (act === "add") {
          if (!text) return bad("记忆内容是空的")
          if ([...text].length > MEMORY_MAX_TEXT) return bad(`单条记忆最多 ${MEMORY_MAX_TEXT} 字`)
          if (userMemory.items.length >= MEMORY_MAX_ITEMS) return bad(`最多 ${MEMORY_MAX_ITEMS} 条,先删几条再添加`)
          userMemory.items.push({ id: `m_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`, text, at: Date.now() })
        } else if (act === "update") {
          const it = userMemory.items.find((x) => x.id === id)
          if (!it) return bad("找不到这条记忆")
          if (!text) return bad("记忆内容是空的")
          if ([...text].length > MEMORY_MAX_TEXT) return bad(`单条记忆最多 ${MEMORY_MAX_TEXT} 字`)
          it.text = text
          it.at = Date.now()
        } else if (act === "delete") {
          const before = userMemory.items.length
          userMemory.items = userMemory.items.filter((x) => x.id !== id)
          if (userMemory.items.length === before) return bad("找不到这条记忆")
        } else if (act === "clear") {
          userMemory.items = []
        } else if (act === "toggle") {
          userMemory.enabled = b.enabled !== false
        } else {
          return bad("未知操作:" + act)
        }
        saveUserMemory()
        console.log(`[wb-studio] 长期记忆已更新:${act} → ${userMemory.items.length} 条,启用=${userMemory.enabled}`)
        return out()
      }
    }
    // ---------- 上下文容量(纯显示设置,不动引擎) ----------
    if (url.pathname === "/api/context") {
      if (req.method === "GET") {
        res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
        // contextLimit=全局默认;effectiveLimit=当前启用 API 实际生效值(单独设过就用它)
        res.end(JSON.stringify({ ok: true, compactRatio: COMPACT_RATIO, ...effectiveContext() }))
        return
      }
      if (req.method === "POST") {
        let body = ""
        for await (const ch of req) body += ch
        let b = {}
        try { b = JSON.parse(body) } catch {}
        const v = Math.floor(Number(b.contextLimit))
        if (!Number.isFinite(v) || v < CTX_MIN || v > CTX_MAX) {
          res.writeHead(400, { "content-type": "application/json; charset=utf-8" })
          res.end(JSON.stringify({ ok: false, error: "上下文容量要在 1000 ~ 10000000 tokens 之间" }))
          return
        }
        config.contextLimit = v
        saveUserSettings()
        res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify({ ok: true, compactRatio: COMPACT_RATIO, ...effectiveContext() }))
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
        if (msg.role === "user") md += `## 👤 我\n\n${msg.content}\n\n`
        else if (msg.error && !msg.content) md += `## 🐱 哈基米\n\n> ⚠ ${msg.error}\n\n`
        else {
          md += `## 🐱 哈基米\n\n${msg.content}\n\n`
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
      const askCount = planAskCount(s) // Plan 模式引导提问:本会话已问几个(决定还能问几个)
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
      // 上下文接近/达到上限 → 先把历史压成摘要再开工;压缩失败不影响任务本身
      let compactInfo = null
      try {
        compactInfo = await compactSession(s, { reason: "auto" })
      } catch (e) {
        console.warn("[wb-studio] 上下文压缩失败(继续任务):", e?.message)
      }
      if (compactInfo) {
        s.messages.push({ role: "assistant", content: "", notice: "compact", at: Date.now(), compact: compactInfo })
        saveSessions()
      }
      const memory = s.summary || ""
      const userMem = userMemoryPrompt() // 用户长期偏好:每轮都带,跨会话生效
      const driveOpts = {
        text: buildWishPrompt(text, mode, askCount, memory, userMem), dir: config.workspace, model: config.model,
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
          result = await runTaskWithRetry("cli", () =>
            driveAgentCli({
              text: buildWishPrompt(text, mode, askCount, memory, userMem), dir: config.workspace, cmd: cliEngineCmd(), nekoRepo: config.nekoRepo || undefined,
              timeoutSec: config.timeoutSec, signal: currentAbort.signal,
              sessionID: s.engineSessionId || undefined,
            })
          )
        } finally {
          busy = false
          currentAbort = null
        }
        s.engineSessionId = result.sessionId || s.engineSessionId
        s.updatedAt = Date.now()
        // CLI 底座没有引擎那套错误翻译:零输出且重试过时补一句人话,别把空错误甩给页面
        if (!result.ok && !String(result.error || "").trim() && result.retries && result.retries.length) {
          result.error = noResponseError(result.retries)
        }
        s.messages.push({ role: "user", content: text, at: Date.now(), mode })
        if (result.retries && result.retries.length) {
          s.messages.push({ role: "assistant", content: "", notice: "retry", at: Date.now(), retries: result.retries })
        }
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
        if (compactInfo) result.compact = compactInfo
        res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify(result))
        return
      }
      try {
        // 无响应/连接失败自动重试(重试前按需重启引擎),见 runTaskWithRetry
        const result = await runTaskWithRetry("engine", () => driveAgent(driveOpts))
        // 看门狗中止/模型不可用/模型静默挂起都会毒化引擎:重启自愈(同步完成后才应答,
        // 避免用户立刻重试时撞上引擎重启中途)。重试流程里刚重启过就不再重复重启
        if (!result.ok && !lastRetryRestarted && (result.stopped || /模型服务已|ModelUnavailable|等待超时|没有生成新回复/.test(String(result.error || "")))) {
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
        if (result.retries && result.retries.length) {
          s.messages.push({ role: "assistant", content: "", notice: "retry", at: Date.now(), retries: result.retries })
        }
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
            result.error = result.retries && result.retries.length
              ? noResponseError(result.retries)
              : (result.reply ? "任务中断,见下方哈基米留言" : "未知错误")
          }
          // 重试过就明确告诉用户「不是没管,是自动试过了」,免得以为程序卡住了
          if (result.retries && result.retries.length && !/已自动重试/.test(result.error)) {
            result.error += `(已自动重试 ${result.retries.length} 次)`
          }
          result.sessionId = s.id
        }
        result.sessionId = s.id // 网页层拿到的统一是工坊会话 id
        if (compactInfo) result.compact = compactInfo
        res.writeHead(200, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify(result))
      } finally {
        busy = false
        currentAbort = null
      }
      return
    }
    // ---------- 在资源管理器里定位文件(改动列表点击) ----------
    if (req.method === "POST" && url.pathname === "/api/reveal") {
      let body = ""
      for await (const ch of req) body += ch
      let b = {}
      try { b = JSON.parse(body) } catch {}
      const raw = String(b.path || "").trim()
      const json = (code, obj) => {
        res.writeHead(code, { "content-type": "application/json; charset=utf-8" })
        res.end(JSON.stringify(obj))
      }
      if (!raw) return json(400, { ok: false, error: "缺少文件路径" })
      // 只允许定位工作区内文件:挡住拿本服务当「任意路径探测器」的用法
      const ws = resolve(config.workspace)
      const full = resolve(ws, raw)
      const rel = relative(ws, full)
      if (!rel || rel.startsWith("..") || isAbsolute(rel)) {
        return json(403, { ok: false, error: "只能定位工作区内的文件" })
      }
      if (!existsSync(full)) return json(404, { ok: false, error: "文件不存在(可能已被移动或删除)" })
      try {
        if (process.platform === "win32") {
          const child = spawn("explorer.exe", [`/select,${full}`], { stdio: "ignore", detached: true })
          child.on("error", () => {})
          child.unref()
        } else if (process.platform === "darwin") {
          spawn("open", ["-R", full], { stdio: "ignore", detached: true }).unref()
        } else {
          spawn("xdg-open", [dirname(full)], { stdio: "ignore", detached: true }).unref()
        }
      } catch (e) {
        return json(500, { ok: false, error: "调用资源管理器失败:" + String(e && e.message || e) })
      }
      return json(200, { ok: true, path: full })
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
