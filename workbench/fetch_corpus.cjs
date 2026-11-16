#!/usr/bin/env node
// 补齐本地市场语料(issue #4:安装包不再打包第三方源码)
//
// 为什么单独做这个脚本:`git archive` 以前会把 market-corpus 一起打进 Setup.exe,
// 那是 10000+ 条第三方插件源码 —— 实测 GPL 8 个、AGPL 1 个、许可未声明 108 个。
// THIRD_PARTY_NOTICES.md 明写「未声明的只作本地阅读参考、不打包分发」,
// 所以打包侧用 .gitattributes export-ignore 真的剔掉,分发口径与声明终于一致。
// 需要语料的人在**自己机器上**跑本脚本拉取(本地学习用途),不再由我们代分发。
//
// 用法:
//   node workbench/fetch_corpus.cjs               # 拉全部在架插件
//   node workbench/fetch_corpus.cjs --only shell_cmd,tavily_search
//   node workbench/fetch_corpus.cjs --limit 30    # 先拉 30 个看看
//   node workbench/fetch_corpus.cjs --dry-run
const fs = require("fs")
const os = require("os")
const path = require("path")
const crypto = require("crypto")
const { spawnSync } = require("child_process")

const REPO = path.resolve(__dirname, "..")
const OUT = process.env.NEKO_CORPUS_DIR || path.join(REPO, "market-corpus")
const API = "https://market.project-neko.cn/api/v1/plugins"
const MIRRORS = ["https://ghfast.top/", "https://gh-proxy.com/", ""]
const MAX_FILE = 1500 * 1024          // 单个文件上限:二进制/大资产不进语料
const KEEP_EXT = new Set([".py", ".toml", ".md", ".json", ".yaml", ".yml", ".txt", ".ts", ".tsx", ".js", ".jsx", ".css", ".html", ".sql", ".sh", ".ps1"])
const SKIP_DIRS = new Set([".git", "node_modules", "__pycache__", ".venv", "dist", "build", ".vscode"])

function arg(name, d) {
  const i = process.argv.indexOf("--" + name)
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : d
}
const dryRun = process.argv.includes("--dry-run")

function httpGet(url, timeout = 60000) {
  const r = spawnSync("curl", ["-sSL", "--max-time", String(Math.round(timeout / 1000)), "--ssl-no-revoke", url],
    { encoding: "buffer", maxBuffer: 1 << 28 })
  if (r.status !== 0 || !r.stdout || !r.stdout.length) throw new Error("curl 失败 " + url + ":" + String(r.stderr || "").slice(0, 120))
  return r.stdout
}
function listAll() {
  const out = []
  for (let page = 1; page <= 20; page++) {
    const raw = httpGet(API + "?page=" + page + "&page_size=100")
    let j = null
    try { j = JSON.parse(raw.toString("utf8")) } catch { throw new Error("市场接口返回不是 JSON(第 " + page + " 页)") }
    const items = j.data || j.items || j.plugins || (Array.isArray(j) ? j : [])
    if (!Array.isArray(items) || !items.length) break
    out.push(...items)
    if (items.length < 100) break
  }
  return out
}
// 只保留代码/配置/文档,且跳过超大文件;顺带把绝对路径与密钥形态的内容原样留着但不执行
function extractZip(zipPath, dest) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "wbcorpus-"))
  const r = spawnSync("tar", ["-xf", zipPath, "-C", tmp], { encoding: "latin1" })
  if (r.status !== 0) throw new Error("解压失败:" + (r.stderr || "").slice(0, 160))
  let kept = 0
  const walk = (from, relTo) => {
    for (const e of fs.readdirSync(from, { withFileTypes: true })) {
      if (e.isDirectory() && SKIP_DIRS.has(e.name)) continue
      const a = path.join(from, e.name)
      if (e.isDirectory()) { walk(a, path.join(relTo, e.name)); continue }
      const st = fs.statSync(a)
      if (st.size > MAX_FILE || !KEEP_EXT.has(path.extname(e.name).toLowerCase())) continue
      const target = path.join(dest, relTo, e.name)
      fs.mkdirSync(path.dirname(target), { recursive: true })
      fs.copyFileSync(a, target)
      kept++
    }
  }
  walk(tmp, "")
  fs.rmSync(tmp, { recursive: true, force: true })
  return kept
}

// 出处登记(issue #21):抓的是分支 HEAD,上游一提交内容就变,重抓与哈希锚点对不上是必然的。
// 这里把每次实际抓到的 ref、zip 哈希与时间记进 market-corpus/SOURCES.json,
// 让「当时抓的是哪一份」至少可查;要完全可复现得钉 tag/commit(见每条 note)。
const SOURCES = path.join(OUT, "SOURCES.json")
function loadSources() {
  try { return JSON.parse(fs.readFileSync(SOURCES, "utf8")) } catch { return {} }
}
function saveSources(obj) {
  try { fs.writeFileSync(SOURCES, JSON.stringify(obj, null, 1) + "\n", "utf8") }
  catch (e) { console.warn("SOURCES.json 写入失败:", String((e && e.message) || e).slice(0, 80)) }
}
const sha256buf = (b) => crypto.createHash("sha256").update(b).digest("hex")

;(async () => {
  const only = (arg("only", "") || "").split(",").map((x) => x.trim()).filter(Boolean)
  const limit = Number(arg("limit", "0")) || 0
  console.log("目标目录:" + OUT + (dryRun ? "(试运行)" : ""))
  if (!dryRun) fs.mkdirSync(OUT, { recursive: true })
  let items = listAll()
  console.log("市场在架条目:" + items.length)
  if (only.length) items = items.filter((x) => only.includes(x.slug) || only.includes(x.id) || only.includes(x.name))
  if (limit) items = items.slice(0, limit)
  const sources = loadSources()
  let ok = 0, skipped = 0, failed = []
  for (const it of items) {
    const slug = String(it.slug || it.id || "").trim()
    const repo = String(it.repo_url || it.repoUrl || it.github || "").trim()
    if (!slug || !/^https?:\/\//.test(repo)) { skipped++; continue }
    const dest = path.join(OUT, slug)
    if (!dryRun && fs.existsSync(path.join(dest, "src"))) { if (!sources[slug]) sources[slug] = { repo: repo, note: "本机既有,本次未重抓,来源与哈希未记录" }; skipped++; continue }
    const gh = repo.replace(/\/+$/, "").match(/github\.com\/([^/]+\/[^/]+)/)
    if (!gh) { failed.push([slug, "不是 GitHub 仓库,跳过"]); continue }
    const ref = spawnSync("curl", ["-sSL", "--max-time", "25", "--ssl-no-revoke", "-o", os.devNull, "-w", "%{http_code}",
      "https://codeload.github.com/" + gh[1] + "/zip/refs/heads/main"], { encoding: "latin1" })
    const branch = (ref.stdout || "").trim() === "200" ? "main" : "master"
    let got = false
    for (const m of MIRRORS) {
      // codeload 只认 zip/refs/heads/<branch>；archive/refs/heads/<branch>.zip 是 github.com 的路径，
      // 拼到 codeload 域名下必然 404(上面探测那行一直是对的,只有这行混用了两种形式)
      const url = m + "https://codeload.github.com/" + gh[1] + "/zip/refs/heads/" + branch
      const zipPath = path.join(os.tmpdir(), "wb-" + slug + ".zip")
      try {
        const buf = httpGet(url, 120000)
        if (buf.length < 1000) throw new Error("内容太小,疑似不是压缩包")
        fs.writeFileSync(zipPath, buf)
        if (dryRun) { ok++; console.log("  [dry] " + slug + " ← " + url); got = true; break }
        const kept = extractZip(zipPath, dest)
        sources[slug] = { repo: gh[1], ref: "refs/heads/" + branch, zip_sha256: sha256buf(buf), files_kept: kept, fetchedAt: new Date().toISOString(), mirror: m, note: "抓的是分支 HEAD;要完全可复现请钉到 tag/commit" }
        fs.rmSync(zipPath, { force: true })
        console.log("  " + slug + ":保留 " + kept + " 个文件")
        ok++; got = true; break
      } catch (e) {
        try { fs.rmSync(zipPath, { force: true }) } catch {}
      }
    }
    if (!got) failed.push([slug, "三个镜像都没下到"])
  }
  if (!dryRun) saveSources(sources)
  console.log("\n完成:拉到 " + ok + " 个,跳过 " + skipped + " 个,失败 " + failed.length + " 个")
  if (failed.length) console.log(failed.slice(0, 20).map((f) => "  ✗ " + f.join(":")).join("\n"))
  console.log("\n许可提示:这些是第三方源码,仅本机阅读参考;" +
    "不许复制其代码进生成物,也不打包分发(详见 THIRD_PARTY_NOTICES.md)。")
  process.exit(failed.length && !ok ? 1 : 0)
})().catch((e) => {
  console.log("出错:" + ((e && e.message) || e))
  process.exit(1)
})
