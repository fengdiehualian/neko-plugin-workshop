#!/usr/bin/env node
// 按 *-pin.json 拉取 / 核对「钉版但正文不进仓库」的语料。
//
// 为什么不能只靠锁:安装包里没有 market-corpus(锁与语料都是 export-ignore),
// 所以这条入口必须能从 pin 自己站起来:
//   有锁(开发工作区) → 逐字段核对 pin 与锁,再按 --locked 拉取;
//   没锁(装机版)     → 按 pin 就地生成一条最小的 corpus.lock,再交给 --locked。
// 两条路最终都走 fetch_corpus 的同一套抓取与树哈希,不另写一份。
//
// 共用实现:plugin-forge(工具仓)与 neko-plugin-dev(深度文档)的 pin 结构同形,
// 差异只在路径与提示文案;以前是两份几乎一样的脚本,改一处漏一处。
//
//   node workbench/pull_forge.cjs [--check]
//   node workbench/pull_skill_docs.cjs [--check]
//
// 退出码:0 一致;1 缺失或与钉版不符;2 pin 与锁口径冲突(两边都写着,但对不上)。
const fs = require("fs")
const path = require("path")
const { spawnSync } = require("child_process")
const locklib = require("./corpus-lock.cjs")

const ROOT = path.resolve(__dirname, "..")

/** 跑一次:pinPath 绝对路径,command 是给用户看的调用方式(错误提示里用)。 */
function run(pinPath, command) {
  // 与 fetch_corpus 用同一个目录口径:NEKO_CORPUS_DIR 优先,否则 <repo>/market-corpus
  const CORPUS = process.env.NEKO_CORPUS_DIR || path.join(ROOT, "market-corpus")
  const checkOnly = process.argv.includes("--check")

  function fail(msg, code = 1) {
    console.error("FAIL  " + msg)
    process.exit(code)
  }

  let pin
  try {
    pin = JSON.parse(fs.readFileSync(pinPath, "utf8"))
  } catch (e) {
    fail("读不到 " + path.relative(ROOT, pinPath) + "(" + pinPath + "):" + String((e && e.message) || e), 2)
  }

  const lockPath = path.join(CORPUS, "corpus.lock.json")
  const src = path.join(CORPUS, pin.slug, "src")
  const measure = () => locklib.treeHash(src)

  /** 把 pin 折成一条锁条目。装机版没有 corpus.lock 时用它就地生成。 */
  function entryFromPin() {
    return {
      source: pin.source, kind: pin.kind, transport: pin.transport, ref: pin.ref,
      commit: pin.commit, version: pin.version, version_source: pin.version_source,
      archive_format: pin.archive_format, archive_sha256: pin.archive_sha256,
      computedHash: pin.computedHash, files: pin.files,
      anchors: pin.anchors, updated_at: new Date().toISOString(),
      // 文档/技能类正文不进仓库;不带这个字段,装机版生成的锁会让 --verify-only 误判「语料缺失」
      ...(pin.vendored === false ? { vendored: false } : {}),
    }
  }

  /** pin 与锁条目的逐字段比对。只比 commit 等于放过 ref/version 漂移;
   *  anchors 也要比——两边指向不同文件时,"校验通过"的含义已经变了。 */
  function mismatch(entry) {
    const keys = ["source", "kind", "transport", "ref", "commit", "version",
      "archive_format", "computedHash", "files", "vendored"]
    for (const k of keys) {
      if (String(entry[k] ?? "") !== String(pin[k] ?? "")) {
        return k + " → 锁 " + String(entry[k]) + " / pin " + String(pin[k])
      }
    }
    const a = JSON.stringify(entry.anchors ?? [])
    const b = JSON.stringify(pin.anchors ?? [])
    if (a !== b) return "anchors 不一致(锚点指向的文件或断言不同)"
    return null
  }

  function report(prefix) {
    const m = measure()
    console.log(prefix + " slug=" + pin.slug + "  version=" + pin.version +
      "  ref=" + pin.ref + "  commit=" + String(pin.commit).slice(0, 12))
    console.log("corpus_root=" + src)
    for (const [k, rel] of Object.entries(pin.entry || {})) {
      const p = path.join(src, ...String(rel).split("/"))
      console.log("  " + k + "=" + p + (fs.existsSync(p) ? "" : "  (缺)"))
    }
    if (m) console.log("tree=" + m.computedHash.slice(0, 16) + "…  files=" + m.files)
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
    if (!lockEntry) {
      // 装机版没有仓库里那份锁:第一个 pin 拉的会就地生成一条,第二个 pin 再来时
      // 锁里自然没有自己。这不是「口径冲突」,而是本机锁需要追加一条。
      // 仓库里的锁缺条目才是真问题(补锁时漏了),那种情况仍然拒绝。
      const localLock = /本机生成/.test(String(lock.note || ""))
      if (!localLock) fail("锁里没有 " + pin.slug + ",而 " + path.basename(pinPath) + " 有;先补锁条目再拉", 2)
      if (checkOnly) {
        // --check 是只读的,按约定不写任何文件。这里若照常 saveLock,会出现两种坏结果:
        // 锁文件只读时 saveLock 先抛错,语料明明对得上却报未捕获异常;锁可写时,--check
        // 报的树哈希不符,却又把条目留在了磁盘上——「检查」把状态改了,后面的人读到的是假象。
        // 真正的注册留给会落盘的那条路径(下面第 3 步),这里只说明缺这一条。
        console.log("提示:本机生成的锁里没有 " + pin.slug + ",--check 不改文件;拉取时会自动补上")
      } else {
        lock.entries[pin.slug] = entryFromPin()
        locklib.saveLock(CORPUS, lock)
        console.log("本机生成的锁里没有 " + pin.slug + ",已按 " + path.basename(pinPath) + " 追加一条")
      }
    } else {
      const bad = mismatch(lockEntry)
      if (bad) fail("锁与 pin 对不上(" + bad + ")。两处都写着钉版,先统一再拉", 2)
    }
  }

  // ---- 2. 量磁盘 ----
  const now = measure()
  if (now && now.computedHash === pin.computedHash) {
    report("OK    ")
    if (!fs.existsSync(lockPath)) {
      console.log("提示:本机没有 corpus.lock(装机版正常现象),本次只按 " + path.basename(pinPath) + " 核对。")
    }
    process.exit(0)
  }

  if (checkOnly) {
    report("BAD   ")
    fail(now
      ? "树哈希与钉版不符:磁盘 " + now.computedHash.slice(0, 16) + "… vs pin " + pin.computedHash.slice(0, 16) + "…"
      : "语料不存在:" + src + "(用 " + command + " 拉取)", 1)
  }

  // ---- 3. 缺锁就按 pin 就地生成,再走 fetch_corpus --locked ----
  if (!fs.existsSync(lockPath)) {
    fs.mkdirSync(CORPUS, { recursive: true })
    const lock = {
      schema: 2,
      algo: "tree-sha256-v0",
      note: "本机生成:安装包不含 corpus.lock,按 " + path.basename(pinPath) + " 落一条。" +
        "开发工作区请用仓库里那份;生成/更新走 workbench/fetch_corpus.cjs。",
      entries: { [pin.slug]: entryFromPin() },
    }
    // 走 saveLock(原子写):断电/被中断不会留下半截 JSON,否则下次 --locked 连读都读不了
    locklib.saveLock(CORPUS, lock)
    console.log("本机没有 corpus.lock,已按 " + path.basename(pinPath) + " 生成一条:" + lockPath)
  }

  console.log("语料缺失或与钉版不符,按 --locked 拉取…")
  const r = spawnSync(process.execPath, [path.join(__dirname, "fetch_corpus.cjs"),
    "--locked", "--only", pin.slug], { stdio: "inherit" })
  if (r.status !== 0) fail("fetch_corpus --locked 失败(退出码 " + r.status + ")", r.status || 1)

  const after = measure()
  if (!after || after.computedHash !== pin.computedHash) {
    fail("拉完仍与钉版不符:磁盘 " + (after ? after.computedHash.slice(0, 16) + "…" : "(无)") +
      " vs pin " + pin.computedHash.slice(0, 16) + "…")
  }
  report("OK    ")
}

module.exports = { run }
