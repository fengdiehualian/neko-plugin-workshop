// wb-studio 的配置层:默认值、读取、相对路径解析。
//
// 搬运说明:字段与注释逐字来自 wb-studio.mjs 的「配置」段。
// 唯一的结构变化:原来 `let config = defaultConfig` 之后就地把路径写回 config,
// 而 config 与 defaultConfig 是同一个对象 —— 第一次加载后 defaultConfig 已被改过一遍。
// 现在 loadConfig() 始终返回独立对象,defaultConfig 保持模板不变。
// 这一点对行为没有可见影响(defaultConfig 不再被别处读),但避免了下一次读到被污染的默认值。

import { readFileSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"
import { THINK_LEVELS } from "./util.mjs"

export const CONFIG_FILE = "wb-studio.config.json"

// 便携版:配置里以 "./" 或 ".\\" 开头的路径相对包根(即 root)解析;开发版仍支持绝对路径。
export const defaultConfig = {
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
  thinkingLevel: "auto", // 思考强度:auto/none/low/medium/high/xhigh(见 THINK_LEVELS)
  retryDelaySec: 3, // 首次退避秒数,之后翻倍(3s → 6s → 12s,上限 30s)
  strictVerify: false, // 严格校验:warning 一律视为失败(wb.cmd 自动加 --strict,须修到零警告)
  expertMode: false, // 专家团模式:把 experts/ 专家包(主理人+成员分工+执行纪律)注入任务提示词
  expertDisabled: [], // 用户单独关闭的专家成员 id 列表(设置页可逐个开关)
  expertModelMap: {}, // 每位专家单独指定的调用来源 { 成员id: { apiId, model } };缺省=跟随当前启用的 API/底座
  contextLimit: 128000, // 上下文容量(tokens):模型上下文窗口大小,用于底部容量显示
  uiFont: "default", // 界面字体预设 id(见页面 FONT_PRESETS)
  uiFontSize: 100, // 界面字号百分比(85~130),用 CSS zoom 整体缩放
  openBrowser: true,
  openMode: "app", // "app"=内置窗口(Edge/Chrome --app,关窗即退出);"browser"=系统浏览器(不随窗口退出)
  appWindow: "1200x860",
}

/** 以 "./" / ".\\" / "../" 开头的路径相对 root 解析;其余原样返回。 */
export function resolvePath(p, root) {
  if (!p) return p
  if (/^\.\.[\/\\]|^\.[\/\\]/.test(p)) return join(root, p)
  return p
}

/** 这些键里存的是路径,加载后要相对 root 解析。 */
export const PATH_KEYS = ["workspace", "engineExe", "bunPath", "pythonPath", "opencodeRepo", "nekoRepo"]

/** 读 <root>/wb-studio.config.json 并合并默认值,返回一份独立对象。
 *  文件不存在(=首次启动)按默认值走且**不出声**;只有文件在却读不动/JSON 坏才提示。
 *  这是「读到坏配置必须出声」的唯一例外,故意的:首装时报错会把用户指向错误方向。 */
export function loadConfig(root) {
  let cfg
  try {
    cfg = { ...defaultConfig, ...JSON.parse(readFileSync(join(root, CONFIG_FILE), "utf8")) }
  } catch (e) {
    cfg = { ...defaultConfig }
    // 文件不存在是首次安装的正常状态,不该报成"解析失败";
    // 只有"文件在、但读不动或 JSON 坏"(BOM、手改语法错)才值得提示,
    // 否则用户会以为自己改的端口/路径没生效。
    if (e.code !== "ENOENT") {
      console.error(`[wb-studio] ⚠ 配置文件解析失败,已用默认配置: ${e.message}`)
    }
  }
  if (!THINK_LEVELS.includes(cfg.thinkingLevel)) cfg.thinkingLevel = "auto"
  for (const k of PATH_KEYS) cfg[k] = resolvePath(cfg[k], root)
  // 浅拷贝只隔离了第一层:数组/对象字段仍与 defaultConfig 共享引用,
  // 任何一处 push/赋值都会污染模板,下一次 load 读到的就是被改过的默认值。
  // 这三个是唯一可变的嵌套字段,逐个断开。
  cfg.engineCmd = Array.isArray(cfg.engineCmd) ? [...cfg.engineCmd] : []
  cfg.expertDisabled = Array.isArray(cfg.expertDisabled) ? [...cfg.expertDisabled] : []
  cfg.expertModelMap = cfg.expertModelMap && typeof cfg.expertModelMap === "object"
    ? { ...cfg.expertModelMap }
    : {}
  return cfg
}
