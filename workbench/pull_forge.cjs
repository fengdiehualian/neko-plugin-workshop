#!/usr/bin/env node
// 拉取 / 核对 plugin-forge 的钉版语料。实现见 pull_pinned.cjs(与深度文档共用同一套逻辑)。
//
//   node workbench/pull_forge.cjs          # 缺就拉,已有则只核对
//   node workbench/pull_forge.cjs --check  # 只核对,不联网;不符退 1
const path = require("path")
const { run } = require("./pull_pinned.cjs")

run(path.join(__dirname, "..", "packs", "neko-plugin", "skill", "neko-plugin-dev",
  "references", "forge-pin.json"), "node workbench/pull_forge.cjs")
