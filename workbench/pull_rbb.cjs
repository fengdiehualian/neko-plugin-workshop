#!/usr/bin/env node
// 拉取 / 核对先搜后建(reuse-before-build)的钉版语料。
// 实现见 pull_pinned.cjs(与深度文档、plugin-forge 共用同一套逻辑)。
//
// 正文不进本仓库:锁里只留 source / ref / commit / 树哈希,用时才按 commit 拉一份到
// market-corpus/reuse_before_build/src(该目录 export-ignore,也不进安装包)。
//
//   node workbench/pull_rbb.cjs          # 缺就拉,已有则只核对
//   node workbench/pull_rbb.cjs --check  # 只核对,不联网;不符退 1
const path = require("path")
const { run } = require("./pull_pinned.cjs")

run(path.join(__dirname, "..", "packs", "neko-plugin", "skill", "neko-plugin-dev",
  "references", "rbb-pin.json"), "node workbench/pull_rbb.cjs")
