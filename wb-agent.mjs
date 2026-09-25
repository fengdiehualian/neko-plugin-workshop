#!/usr/bin/env bun
/**
 * wb-agent - 无人值守 Agent 驱动器 CLI(N.E.K.O. 附属工作台)
 *
 * 把 opencode server 变成「一句话 → 自动写插件 → check/build 达标」的无人值守引擎。
 * 核心逻辑在 wb-agent-lib.mjs(网页后端 wb-studio.mjs 复用同一实现)。
 *
 * 用法:
 *   bun wb-agent.mjs --text "需求..." [--dir <工作区>] [--model provider/model] [--server http://127.0.0.1:4096] [--timeout <秒>]
 */

import { driveAgent } from "./wb-agent-lib.mjs"

const args = {}
const argv = process.argv.slice(2)
for (let i = 0; i < argv.length; i++) {
  if (argv[i].startsWith("--")) {
    const key = argv[i].slice(2)
    if (i + 1 < argv.length && !argv[i + 1].startsWith("--")) {
      args[key] = argv[++i]
    } else {
      args[key] = true
    }
  }
}

if (!args.text) {
  console.log(JSON.stringify({ ok: false, error: '缺少 --text。用法: bun wb-agent.mjs --text "需求" [--dir ...] [--model ...] [--server ...]' }))
  process.exit(1)
}

try {
  const result = await driveAgent({
    text: String(args.text),
    dir: args.dir || process.env.WB_PROJECTS_DIR || "wb-projects",
    model: args.model || "sensenova/kimi-k3",
    server: args.server || "http://127.0.0.1:4096",
    timeout: Number(args.timeout || 1500),
    heartbeat: Boolean(process.env.WB_HEARTBEAT),
  })
  console.log(JSON.stringify(result, null, 2))
  process.exit(result.ok ? 0 : 1)
} catch (e) {
  console.log(JSON.stringify({ ok: false, error: String(e?.message || e) }))
  process.exit(1)
}
