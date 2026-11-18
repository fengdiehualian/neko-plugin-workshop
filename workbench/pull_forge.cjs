#!/usr/bin/env node
// 拉取 / 核对 plugin-forge 的钉版语料。
//
// 安装包里不带语料(market-corpus/** 是 export-ignore),Agent 要读 forge 正典时先跑这一条,
// 拿到本机路径;开发工作区里目录已就位,这条只会做核对。
//
//   node workbench/pull_forge.cjs          # 缺就按锁拉取,已有则只核对
//   node workbench/pull_forge.cjs --check  # 只核对,不联网;不符退 1
//
// 退出码:0 一致;1 缺失或与锁不符;2 钉版口径不一致(锁与 forge-pin.json 对不上)。
const fs = require("fs")
const path = require("path")
const { spawnSync } = require("child_process")
const locklib = require("./corpus-lock.cjs")

const ROOT = path.resolve(__dirname, "..")
const CORPUS = path.join(ROOT, "market-corpus")
const PIN_PATH = path.join(ROOT, "packs", "neko-plugin", "skill", "neko-plugin-dev",
  "references", "forge-pin.json")

const checkOnly = process.argv.includes("--check")

function fail(msg, code = 1) {
  console.error("FAIL  " + msg)
  process.exit(code)
}

let pin
try {
  pin = JSON.parse(fs.readFileSync(PIN_PATH, "utf8"))
} catch (e) {
  fail("读不到 forge-pin.json(" + PIN_PATH + "):" + String((e && e.message) || e), 2)
}

let lock
try {
  lock = locklib.loadLock(CORPUS)
} catch (e) {
  fail("读不到 corpus.lock.json:" + String((e && e.message) || e), 2)
}

const entry = lock.entries[pin.slug]
if (!entry) fail("锁里没有 " + pin.slug + " 这一条;先按 market-corpus/README.md 新增条目再拉", 2)
if (entry.commit !== pin.commit) {
  fail("锁里的 commit 与 forge-pin.json 不一致:锁 " + String(entry.commit).slice(0, 12) +
    " vs pin " + String(pin.commit).slice(0, 12) + ";先统一再拉", 2)
}

const src = path.join(CORPUS, pin.slug, "src")
const measure = () => locklib.treeHash(src)

function report(prefix) {
  const m = measure()
  console.log(prefix + " slug=" + pin.slug + "  version=" + entry.version +
    "  ref=" + entry.ref + "  commit=" + String(entry.commit).slice(0, 12))
  console.log("corpus_root=" + src)
  for (const [k, rel] of Object.entries(pin.entry)) {
    const p = path.join(src, ...String(rel).split("/"))
    console.log("  " + k + "=" + p + (fs.existsSync(p) ? "" : "  (缺)"))
  }
  if (m) console.log("tree=" + m.tree_sha256.slice(0, 16) + "…  files=" + m.files)
  return m
}

const now = measure()
if (now && now.tree_sha256 === entry.tree_sha256) {
  report("OK    ")
  process.exit(0)
}

if (checkOnly) {
  report("BAD   ")
  fail(now
    ? "树哈希与锁不符:磁盘 " + now.tree_sha256.slice(0, 16) + "… vs 锁 " + entry.tree_sha256.slice(0, 16) + "…"
    : "语料不存在:" + src, 1)
}

console.log("语料缺失或与锁不符,按 --locked 拉取(已落盘的条目会跳过)…")
const r = spawnSync(process.execPath, [path.join(__dirname, "fetch_corpus.cjs"), "--locked"], { stdio: "inherit" })
if (r.status !== 0) fail("fetch_corpus --locked 失败(退出码 " + r.status + ")", r.status || 1)

const after = measure()
if (!after || after.tree_sha256 !== entry.tree_sha256) {
  fail("拉完仍与锁不符:磁盘 " + (after ? after.tree_sha256.slice(0, 16) + "…" : "(无)") +
    " vs 锁 " + entry.tree_sha256.slice(0, 16) + "…")
}
report("OK    ")
