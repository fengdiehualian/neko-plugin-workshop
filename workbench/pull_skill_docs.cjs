#!/usr/bin/env node
// 拉取 / 核对 neko-plugin-dev 深度专题(52 篇 references + 官方 init 示例)的钉版语料。
//
// 正文不进本仓库:锁里只留 source / ref / commit / 树哈希,用时才按 commit 拉一份到
// market-corpus/neko-plugin-skill/src(该目录 export-ignore,也不进安装包)。
// 这样既不改上游一字,也不用把 5 万字正文复制进工坊、再各自漂移。
//
//   node workbench/pull_skill_docs.cjs          # 缺就拉,已有则只核对
//   node workbench/pull_skill_docs.cjs --check  # 只核对,不联网;不符退 1
const path = require("path")
const { run } = require("./pull_pinned.cjs")

run(path.join(__dirname, "..", "packs", "neko-plugin", "skill", "neko-plugin-dev",
  "references", "neko-plugin-dev-pin.json"), "node workbench/pull_skill_docs.cjs")
