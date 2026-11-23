#!/usr/bin/env node
// 运行时语料统计:目录数 / 带 plugin.toml 的份数 / 按需条目 / 非市场条目。
//
// 文档不再写死这些数字(CodeRabbit PR #49 review)。需要引用数量时运行:
//   node workbench/corpus_stats.cjs
// 或设置 NEKO_CORPUS_DIR 指向另一份 corpus 根目录。
const fs = require("fs")
const path = require("path")
const locklib = require("./corpus-lock.cjs")

const ROOT = path.resolve(__dirname, "..")

function corpusRoot(env = process.env) {
  return env.NEKO_CORPUS_DIR ? path.resolve(env.NEKO_CORPUS_DIR) : path.join(ROOT, "market-corpus")
}

function hasPluginToml(corpusDir, slug) {
  return fs.existsSync(path.join(corpusDir, slug, "plugin.toml")) ||
    fs.existsSync(path.join(corpusDir, slug, "src", "plugin.toml")) ||
    fs.existsSync(path.join(corpusDir, slug, "plugin", "plugin.toml"))
}

function computeStats(corpusDir = corpusRoot()) {
  const lock = locklib.loadLock(corpusDir)
  const entries = lock.entries || {}
  const onDemand = Object.entries(entries)
    .filter(([, v]) => v && v.vendored === false)
    .map(([slug]) => slug)
  const nonPluginKind = Object.entries(entries)
    .filter(([, v]) => v && v.kind && v.kind !== "plugin")
    .map(([slug]) => slug)
  const nonMarket = new Set([...onDemand, ...nonPluginKind])
  const allDirs = fs.readdirSync(corpusDir, { withFileTypes: true })
    .filter((d) => d.isDirectory())
    .map((d) => d.name)
  const market = allDirs.filter((slug) => !nonMarket.has(slug))
  return {
    corpus_dir: corpusDir,
    dirs: market.length,
    withPluginToml: market.filter((slug) => hasPluginToml(corpusDir, slug)).length,
    onDemand,
    nonPluginKind,
  }
}

if (require.main === module) {
  console.log(JSON.stringify(computeStats(), null, 2))
}

module.exports = { computeStats, corpusRoot }
