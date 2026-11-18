#!/usr/bin/env node
// 拉取 / 核对 plugin-forge 的钉版语料。
//
// 安装包里没有 market-corpus(锁与语料都是 export-ignore),所以这条入口**不能只靠锁**:
//   有锁(开发工作区) → 逐字段核对 pin 与锁,再按 --locked 拉取;
//   没锁(装机版)     → 按 forge-pin.json 就地生成一条最小的 corpus.lock,再交给 --locked。
// 两条路最终都走 fetch_corpus 的同一套抓取与树哈希,不另写一份。
//
//   node workbench/pull_forge.cjs          # 缺就拉,已有则只核对
//   node workbench/pull_forge.cjs --check  # 只核对,不联网;不符退 1
//
// 退出码:0 一致;1 缺失或与钉版不符;2 pin 与锁口径冲突(两边都写着,但对不上)。
const fs = require("fs")
const path = require("path")
const { spawnSync } = require("child_process")
const locklib = require("./corpus-lock.cjs")

const ROOT = path.resolve(__dirname, "..")
// 与 fetch_corpus 用同一个目录口径:NEKO_CORPUS_DIR 优先,否则 <repo>/market-corpus
const CORPUS = process.env.NEKO_CORPUS_DIR || path.join(ROOT, "market-corpus")
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

const lockPath = path.join(CORPUS, "corpus.lock.json")
const src = path.join(CORPUS, pin.slug, "src")
const measure = () => locklib.treeHash(src)

/** 把 pin 折成一条锁条目。装机版没有 corpus.lock 时用它就地生成。 */
function entryFromPin() {
  return {
    repo: pin.repo, kind: pin.kind, source: pin.source, ref: pin.ref,
    commit: pin.commit, version: pin.version, version_source: pin.version_source,
    archive_format: pin.archive_format, archive_sha256: pin.archive_sha256,
    tree_sha256: pin.tree_sha256, files: pin.files,
    anchors: pin.anchors, updated_at: new Date().toISOString(),
  }
}

/** pin 与锁条目的逐字段比对。只比 commit 等于放过 ref/version 漂移。 */
function mismatch(entry) {
  const keys = ["repo", "kind", "source", "ref", "commit", "version",
    "archive_format", "tree_sha256", "files"]
  for (const k of keys) {
    if (String(entry[k] ?? "") !== String(pin[k] ?? "")) {
      return k + " → 锁 " + String(entry[k]) + " / pin " + String(pin[k])
    }
  }
  return null
}

function report(prefix) {
  const m = measure()
  console.log(prefix + " slug=" + pin.slug + "  version=" + pin.version +
    "  ref=" + pin.ref + "  commit=" + String(pin.commit).slice(0, 12))
  console.log("corpus_root=" + src)
  for (const [k, rel] of Object.entries(pin.entry)) {
    const p = path.join(src, ...String(rel).split("/"))
    console.log("  " + k + "=" + p + (fs.existsSync(p) ? "" : "  (缺)"))
  }
  if (m) console.log("tree=" + m.tree_sha256.slice(0, 16) + "…  files=" + m.files)
  return m
}

// ---- 1. 与锁对齐(有锁时) ----
if (fs.existsSync(lockPath)) {
  let lock
  try {
    lock = locklib.loadLock(CORPUS)
  } catch (e) {
    fail("corpus.lock.json 读不了:" + String((e && e.message) || e), 2)
  }
  const lockEntry = lock.entries[pin.slug]
  if (!lockEntry) fail("锁里没有 " + pin.slug + ",而 forge-pin.json 有;先补锁条目再拉", 2)
  const bad = mismatch(lockEntry)
  if (bad) fail("锁与 pin 对不上(" + bad + ")。两处都写着钉版,先统一再拉", 2)
}

// ---- 2. 量磁盘 ----
const now = measure()
if (now && now.tree_sha256 === pin.tree_sha256) {
  report("OK    ")
  if (!fs.existsSync(lockPath)) {
    console.log("提示:本机没有 corpus.lock(装机版正常现象),本次只按 forge-pin.json 核对。")
  }
  process.exit(0)
}

if (checkOnly) {
  report("BAD   ")
  fail(now
    ? "树哈希与钉版不符:磁盘 " + now.tree_sha256.slice(0, 16) + "… vs pin " + pin.tree_sha256.slice(0, 16) + "…"
    : "语料不存在:" + src + "(用 node workbench/pull_forge.cjs 拉取)", 1)
}

// ---- 3. 缺锁就按 pin 就地生成,再走 fetch_corpus --locked ----
if (!fs.existsSync(lockPath)) {
  fs.mkdirSync(CORPUS, { recursive: true })
  const lock = {
    schema: 1,
    algo: "tree-sha256-v0",
    note: "本机生成:安装包不含 corpus.lock,按 forge-pin.json 落一条。" +
      "开发工作区请用仓库里那份;生成/更新走 workbench/fetch_corpus.cjs。",
    entries: { [pin.slug]: entryFromPin() },
  }
  fs.writeFileSync(lockPath, JSON.stringify(lock, null, 2) + "\n", "utf8")
  console.log("本机没有 corpus.lock,已按 forge-pin.json 生成一条:" + lockPath)
}

console.log("语料缺失或与钉版不符,按 --locked 拉取…")
const r = spawnSync(process.execPath, [path.join(__dirname, "fetch_corpus.cjs"), "--locked"], { stdio: "inherit" })
if (r.status !== 0) fail("fetch_corpus --locked 失败(退出码 " + r.status + ")", r.status || 1)

const after = measure()
if (!after || after.tree_sha256 !== pin.tree_sha256) {
  fail("拉完仍与钉版不符:磁盘 " + (after ? after.tree_sha256.slice(0, 16) + "…" : "(无)") +
    " vs pin " + pin.tree_sha256.slice(0, 16) + "…")
}
report("OK    ")
