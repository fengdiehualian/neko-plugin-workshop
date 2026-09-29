#!/usr/bin/env bun
/**
 * neko-plugin-workshop MCP server(stdio,JSON-RPC 2.0)
 *
 * 让任意支持 MCP 的 Agent 底座(Claude Code / Codex / omp / opencode / …)直接驱动工坊闭环:
 *   wb_scaffold      一句话参数生成插件项目 + check(+build)
 *   wb_verify        对已有插件目录 check → build(可选 --strict:warning 视为失败)
 *   wb_install_skill 把 neko-plugin-dev 技能包装进任意 Agent 底座的技能目录
 *
 * 注册(以 Claude Code 为例):
 *   claude mcp add neko-plugin-workshop -- bun <本文件绝对路径>
 * Codex / omp 等同理:mcpServers 配置里 command=bun,args=[<本文件>]
 *
 * 安全边界:stdio 传输,不监听端口、不主动联网;写盘仅限 scaffold 的 out 目录
 * 与 wb_install_skill 指定的技能目录。检查/构建依赖 NEKO 源码(nekoRepo 参数或 WB_NEKO_REPO)。
 */

import { join, resolve } from "node:path"
import { homedir } from "node:os"
import { cpSync, existsSync, mkdirSync } from "node:fs"
import { scaffoldAndVerify, verifyProject } from "./src/index"

const WORKSHOP_ROOT = import.meta.dir
const SKILL_SRC = join(WORKSHOP_ROOT, "packs", "neko-plugin", "skill", "neko-plugin-dev")
const PY_DEFAULT = process.env.WB_PYTHON || "python"
const NEKO_DEFAULT = process.env.WB_NEKO_REPO || ""
const PROJECTS_DEFAULT = process.env.WB_PROJECTS_DIR || join(WORKSHOP_ROOT, "workspace")

const SERVER_INFO = { name: "neko-plugin-workshop", version: "0.1.0" }

/** Agent 底座 → 技能目录约定;generic 必须显式给 targetDir */
function skillTarget(agent, targetDir) {
  switch (agent) {
    case "claude": return { dest: join(homedir(), ".claude", "skills", "neko-plugin-dev"), hint: "Claude Code 全局技能;会话内自动生效" }
    case "codex": return { dest: join(homedir(), ".codex", "skills", "neko-plugin-dev"), hint: "Codex CLI 技能目录;若你的发行版不读取该目录,改用 generic + AGENTS.md 引用" }
    case "opencode": return { dest: join(process.cwd(), ".opencode", "skill", "neko-plugin-dev"), hint: "opencode 项目级技能(与工坊自带部署一致)" }
    case "omp": return { dest: join(homedir(), ".omp", "skills", "neko-plugin-dev"), hint: "omp 技能目录;以 omp 文档为准" }
    default:
      if (!targetDir) throw new Error("generic 底座需要 targetDir(技能将被复制到 <targetDir>/neko-plugin-dev)")
      return { dest: join(resolve(targetDir), "neko-plugin-dev"), hint: "通用技能目录;若底座不自动扫描,请在 AGENTS.md 里写明引用该 SKILL.md" }
  }
}

function toolScaffold(args) {
  const nekoRepoRoot = args.nekoRepo || NEKO_DEFAULT
  if (!nekoRepoRoot) throw new Error("缺少 nekoRepo(参数或环境变量 WB_NEKO_REPO)")
  const out = args.out || PROJECTS_DEFAULT
  return scaffoldAndVerify(
    args.packId || "neko-plugin",
    args.template || "reminder",
    {
      PLUGIN_ID: String(args.pluginId ?? ""),
      PLUGIN_NAME: String(args.pluginName ?? ""),
      CLASS_NAME: String(args.className ?? ""),
    },
    { targetDir: out, nekoRepoRoot, python: args.python || PY_DEFAULT, strict: args.strict === true },
  )
}

async function toolVerify(args) {
  const nekoRepoRoot = args.nekoRepo || NEKO_DEFAULT
  if (!nekoRepoRoot) throw new Error("缺少 nekoRepo(参数或环境变量 WB_NEKO_REPO)")
  if (!args.pluginDir) throw new Error("缺少 pluginDir")
  return verifyProject(nekoRepoRoot, resolve(String(args.pluginDir)), {
    python: args.python || PY_DEFAULT,
    outPath: args.outPath || undefined,
    strict: args.strict === true,
  })
}

function toolInstallSkill(args) {
  if (!existsSync(SKILL_SRC)) throw new Error(`技能包缺失:${SKILL_SRC}`)
  const { dest, hint } = skillTarget(args.agent || "generic", args.targetDir)
  mkdirSync(dest, { recursive: true })
  cpSync(SKILL_SRC, dest, { recursive: true })
  return {
    installed: dest,
    files: "SKILL.md + references/*(15 页官方插件规范 + 市场写法分析)",
    usage: hint,
    pipeline: "让 Agent 读该 SKILL.md 后,用 wb-plugin CLI(或本 MCP 的 wb_scaffold/wb_verify)完成 生成→check→build",
  }
}

const TOOLS = [
  {
    name: "wb_scaffold",
    description: "从模板生成 N.E.K.O. 插件项目并跑 check→build,产出 .neko-plugin(可 strict:warning 视为失败)",
    inputSchema: {
      type: "object",
      properties: {
        pluginId: { type: "string", description: "插件 ID(如 hydration_cat)" },
        pluginName: { type: "string", description: "插件显示名(中文即可)" },
        className: { type: "string", description: "入口类名(如 HydrationCatPlugin)" },
        packId: { type: "string", description: "默认 neko-plugin" },
        template: { type: "string", description: "模板:reminder|notes|hello_world,默认 reminder" },
        out: { type: "string", description: "项目落盘目录,默认工坊 workspace" },
        nekoRepo: { type: "string", description: "N.E.K.O 源码根(check/build 依赖)" },
        python: { type: "string", description: "带 SDK 依赖的解释器" },
        strict: { type: "boolean", description: "true 时 warning 一律视为失败" },
      },
      required: ["pluginId", "pluginName", "className"],
    },
  },
  {
    name: "wb_verify",
    description: "对已有插件目录跑 check→build;strict=true 时 warning 视为失败并跳过 build",
    inputSchema: {
      type: "object",
      properties: {
        pluginDir: { type: "string" },
        nekoRepo: { type: "string" },
        python: { type: "string" },
        outPath: { type: "string", description: ".neko-plugin 产物路径" },
        strict: { type: "boolean" },
      },
      required: ["pluginDir"],
    },
  },
  {
    name: "wb_install_skill",
    description: "把 neko-plugin-dev 技能包安装到任意 Agent 底座(claude|codex|opencode|omp|generic)",
    inputSchema: {
      type: "object",
      properties: {
        agent: { type: "string", enum: ["claude", "codex", "opencode", "omp", "generic"], description: "默认 generic" },
        targetDir: { type: "string", description: "generic 时的技能目标目录" },
      },
    },
  },
]

function ok(id, result) {
  process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id, result }) + "\n")
}
function err(id, code, message) {
  process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id, error: { code, message } }) + "\n")
}

async function dispatch(method, params) {
  if (method === "initialize") {
    return { protocolVersion: params?.protocolVersion || "2024-11-05", capabilities: { tools: {} }, serverInfo: SERVER_INFO }
  }
  if (method === "ping") return {}
  if (method === "tools/list") return { tools: TOOLS }
  if (method === "tools/call") {
    const { name, arguments: args = {} } = params || {}
    let data
    if (name === "wb_scaffold") data = await toolScaffold(args)
    else if (name === "wb_verify") data = await toolVerify(args)
    else if (name === "wb_install_skill") data = toolInstallSkill(args)
    else throw { code: -32602, message: `未知工具:${name}` }
    const slim = (data.files && typeof data.files === "object")
      ? { ...data, files: Object.keys(data.files) } // 文件内容太长,只回文件名
      : data
    return { content: [{ type: "text", text: JSON.stringify({ ok: true, ...slim }, null, 2) }] }
  }
  throw { code: -32601, message: `未知方法:${method}` }
}

let buf = ""
process.stdin.setEncoding("utf8")
process.stdin.on("data", (chunk) => {
  buf += chunk
  let idx
  while ((idx = buf.indexOf("\n")) >= 0) {
    const line = buf.slice(0, idx).trim()
    buf = buf.slice(idx + 1)
    if (!line) continue
    let msg
    try { msg = JSON.parse(line) } catch { err(null, -32700, "解析错误"); continue }
    if (msg.jsonrpc !== "2.0" || typeof msg.method !== "string") continue
    const isNotify = msg.id === undefined || msg.id === null
    dispatch(msg.method, msg.params)
      .then((result) => { if (!isNotify) ok(msg.id, result) })
      .catch((e) => { if (!isNotify) err(msg.id, e?.code ?? -32603, e?.message ?? String(e)) })
  }
})
process.stdin.on("end", () => process.exit(0))
