// wb-studio 的纯工具函数。
//
// 这块搬迁的原则是「只搬不改」:函数体逐字来自 wb-studio.mjs,
// 唯一的差异是 `require("node:fs")` 换成模块顶部的 import ——
// 独立模块不再共享父文件的 require 环境,这不是行为改动。
//
// 为什么先搬它们:零模块级状态依赖,搬走之后安全网(对外面快照 + 冒烟)能立刻验证等价性。

import { mkdirSync, renameSync, writeFileSync } from "node:fs"
import { basename, dirname } from "node:path"

export const THINK_LEVELS = ["auto", "none", "low", "medium", "high", "xhigh"]

// 统一的 JSON 原子落盘:先写临时文件再 rename。
// 直接 writeFileSync 覆盖,遇到断电/强杀会留下半截 JSON,下次启动整份配置或会话就读废了。
export function writeJsonAtomic(file, obj) {
  try {
    mkdirSync(dirname(file), { recursive: true })
    const tmp = `${file}.tmp`
    writeFileSync(tmp, JSON.stringify(obj, null, 2), "utf8")
    renameSync(tmp, file)
  } catch (e) {
    console.error(`[wb-studio] 落盘失败(${basename(file)}):`, e.message)
  }
}

/** 思考强度:OpenAI 系只认 minimal/low/medium/high,映射一下;none 用 minimal(最低但不报错) */
export function openaiEffort(level) {
  return level === "none" ? "minimal" : level === "xhigh" ? "high" : level
}

/** 粗略 token 估算:中文按 1 字 1 token,英文数字按 3.5 字符 1 token */
export function estTokens(text) {
  const s = String(text || "")
  if (!s) return 0
  const cjk = (s.match(/[\u4e00-\u9fff]/g) || []).length
  return Math.ceil(cjk + (s.length - cjk) / 3.5)
}

/** 会话已用上下文:优先最近一轮引擎回传的 usage,否则按"压缩后真正还要带上的内容"估算 */
export function sessionTokens(s) {
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
