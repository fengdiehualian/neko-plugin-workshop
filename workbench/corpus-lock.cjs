#!/usr/bin/env node
// 语料锁 corpus.lock（v0 草稿的落地实现）
//
// 锁回答的问题只有一个:仓库里现在这一份语料,到底来自上游哪个 commit、哪一份字节。
// 树哈希按「过滤后实际落盘的文件」算,所以过滤规则(filter)变了也会被看见 ——
// 这正是不能用 zip 哈希代替它的原因。
//
// 树哈希算法(v0):
//   1. 取条目根目录下的全部文件,路径用 `/` 相对条目根;
//   2. 按路径的 UTF-8 字节序排序;
//   3. 每行 `<sha256(文件内容)> <路径>\n` 拼接,对整串再 sha256;
//   4. 一律按字节读,不做 CRLF 转换。
//
// 导出给 fetch_corpus.cjs 与测试用。
const fs = require("fs")
const path = require("path")
const crypto = require("crypto")

const LOCK_FILE = "corpus.lock.json"
const SCHEMA = 2
const SCHEMA_V1 = 1
const ALGO = "tree-sha256-v0"

// 与 fetch_corpus.cjs 的过滤口径保持一致;改这里必须同步跑 --update-all,
// 否则锁里的 computedHash 会与磁盘上的语料对不上(这是有意的失败,不是噪音)。
const MAX_FILE = 1500 * 1024
const KEEP_EXT = new Set([".py", ".toml", ".md", ".json", ".yaml", ".yml", ".txt", ".ts", ".tsx", ".js", ".jsx", ".css", ".html", ".sql", ".sh", ".ps1"])
// `.github` 也跳过:发布流水线对「读上游源码」没有价值,而 workflow 里常有
// token / PAT / 部署目标的形态,留在语料里只会让许可与密钥扫描到处误报。
// 这条与已提交语料的实际内容一致(历史抓取也没有 .github),所以加进来不产生 diff。
const SKIP_DIRS = new Set([".git", ".github", "node_modules", "__pycache__", ".venv", "dist", "build", ".vscode"])
const KEEP_NAME = new Set([
  "license", "licence", "copying", "notice", "readme",
  "license-mit", "licence-mit", "license-apache", "unlicense",
])

const sha256buf = (b) => crypto.createHash("sha256").update(b).digest("hex")

/** 该文件是否属于语料(扩展名白名单 / 许可说明类无扩展名文件 / 体积上限)。 */
function keepFile(name, size) {
  const lower = name.toLowerCase()
  const ext = path.extname(lower)
  if (ext === "") return KEEP_NAME.has(lower) && size <= MAX_FILE
  return KEEP_EXT.has(ext) && size <= MAX_FILE
}

/** 列出目录下所有属于语料的文件,返回 POSIX 相对路径(升序,字节序)。 */
function listCorpusFiles(root, rel = "", out = []) {
  for (const e of fs.readdirSync(path.join(root, rel), { withFileTypes: true })) {
    const childRel = rel ? rel + "/" + e.name : e.name
    if (e.isDirectory()) {
      if (SKIP_DIRS.has(e.name)) continue
      listCorpusFiles(root, childRel, out)
      continue
    }
    const st = fs.lstatSync(path.join(root, childRel))
    // 只算普通文件:statSync 会跟随符号链接,把链接指向的宿主文件读进哈希;
    // 而抓取侧(copyFilteredTree)也用 lstat 跳过,两边口径必须一致,
    // 否则含 symlink 的上游会出现「抓下来 37 个、算哈希 38 个」。
    if (!st.isFile()) continue
    if (!keepFile(e.name, st.size)) continue
    out.push(childRel)
  }
  return out.sort((a, b) => Buffer.compare(Buffer.from(a, "utf8"), Buffer.from(b, "utf8")))
}

/**
 * 计算树哈希。目录不存在返回 null(调用方据此区分「没抓过」与「抓过但对不上」)。
 * 空目录返回空串的哈希 —— 语料条目为空本身就是异常,交给上层判失败。
 */
function treeHash(root) {
  if (!fs.existsSync(root) || !fs.statSync(root).isDirectory()) return null
  const files = listCorpusFiles(root)
  const lines = files.map((rel) => {
    const abs = path.join(root, ...rel.split("/"))
    return sha256buf(fs.readFileSync(abs)) + " " + rel + "\n"
  })
  return {
    computedHash: sha256buf(Buffer.from(lines.join(""), "utf8")),
    files: files.length,
    paths: files,
  }
}

function lockPath(corpusRoot) {
  return path.join(corpusRoot, LOCK_FILE)
}

/** v1 → v2 的字段迁移(只在读取时做,写回时自然变成 v2)。
 *  v1: repo=仓库, source=传输方式(zip/git), tree_sha256=哈希
 *  v2: source=仓库, transport=传输方式, computedHash=哈希
 *  `source` 在两个版本里语义不同,所以只能靠 schema 判断,不能靠"字段在不在"。 */
function migrateV1(entry) {
  const out = { ...entry }
  // 先迁传输方式:此刻 out.source 还是 "zip"/"git"(仓库名一定带斜杠,用它区分)
  if (typeof out.source === "string" && !out.source.includes("/")) {
    out.transport = out.source
    delete out.source
  }
  if (typeof out.repo === "string") { out.source = out.repo; delete out.repo }
  if (typeof out.tree_sha256 === "string") { out.computedHash = out.tree_sha256; delete out.tree_sha256 }
  return out
}

function loadLock(corpusRoot) {
  const p = lockPath(corpusRoot)
  if (!fs.existsSync(p)) return { schema: SCHEMA, algo: ALGO, entries: {} }
  const raw = JSON.parse(fs.readFileSync(p, "utf8"))
  if (raw.schema !== SCHEMA && raw.schema !== SCHEMA_V1) {
    throw new Error("corpus.lock.json schema 版本不认识:" + raw.schema +
      "(本脚本认 " + SCHEMA_V1 + "(读时迁移)与 " + SCHEMA + ")")
  }
  if (raw.algo !== ALGO) throw new Error("corpus.lock.json 的 algo 不认识:" + raw.algo + "(本脚本只认 " + ALGO + ")")
  if (!raw.entries || typeof raw.entries !== "object") throw new Error("corpus.lock.json 缺少 entries")
  if (raw.schema === SCHEMA_V1) {
    const entries = {}
    for (const [slug, entry] of Object.entries(raw.entries)) entries[slug] = migrateV1(entry)
    return { ...raw, schema: SCHEMA, entries }
  }
  return raw
}

/** 稳定写回:2 空格缩进、键按写入顺序、末尾一个换行。 */
function saveLock(corpusRoot, lock) {
  const p = lockPath(corpusRoot)
  fs.writeFileSync(p, JSON.stringify(lock, null, 2) + "\n", "utf8")
}

const SLUG_RE = /^[a-z0-9_-]+$/
// `__proto__` / `constructor` / `prototype` 都符合 [a-z0-9_-],
// 但拿它们当键写普通对象会改原型而不是加键(迁移时 `entries[slug] = ...` 会静默丢条目)。
const RESERVED_SLUGS = new Set(["__proto__", "constructor", "prototype"])

function assertSlug(slug) {
  if (!SLUG_RE.test(String(slug || ""))) throw new Error("slug 不合法(只允许 [a-z0-9_-]):" + JSON.stringify(slug))
  if (RESERVED_SLUGS.has(String(slug))) throw new Error("slug 不能用保留字:" + JSON.stringify(slug))
  return slug
}

/** 一个条目里可能存在的 SKILL.md:根目录、skills/<name>/、plugins/<name>/(按字节序,可复现)。 */
function skillMdPaths(rootDir) {
  const out = []
  const pushIf = (rel, abs) => { if (fs.existsSync(abs) && fs.statSync(abs).isFile()) out.push(rel) }
  pushIf("SKILL.md", path.join(rootDir, "SKILL.md"))
  for (const parent of ["skills", "plugins"]) {
    const dir = path.join(rootDir, parent)
    if (!fs.existsSync(dir) || !fs.statSync(dir).isDirectory()) continue
    for (const name of fs.readdirSync(dir).sort()) {
      pushIf(parent + "/" + name + "/SKILL.md", path.join(dir, name, "SKILL.md"))
    }
  }
  return out
}

/** 从落盘语料里读版本号,并记下它是从哪个文件读到的。
 *  root 既可以传条目根(<slug>,含 src/),也可以传已过滤的 src 根,两种都认。 */
function readVersion(root) {
  const bases = fs.existsSync(path.join(root, "src")) ? ["", "src/"] : [""]
  const candidates = [
    ["plugin.toml", "plugin.toml", /^\s*version\s*=\s*"([^"]+)"/m],
    ["pyproject.toml", "pyproject.toml", /^\s*version\s*=\s*"([^"]+)"/m],
  ]
  // SKILL.md 不按 slug 硬编码:skill 类条目的目录名各不相同(plugins/<name>、skills/<name>),
  // 写死 reuse-before-build 的路径等于「换个 slug 就读不到版本」。
  for (const base of bases) {
    for (const rel of skillMdPaths(path.join(root, ...(base).split("/").filter(Boolean)))) {
      candidates.push([rel, "SKILL.md", /^\s*version:\s*([^\s]+)/m])
    }
  }
  for (const base of bases) {
    for (const [rel, label, re] of candidates) {
      const p = path.join(root, ...(base + rel).split("/"))
      if (!fs.existsSync(p)) continue
      const m = re.exec(fs.readFileSync(p, "utf8"))
      if (m) return { version: m[1], version_source: label }
    }
  }
  // .claude-plugin/plugin.json:Claude Code 插件清单,版本只写在这。
  // 用 JSON.parse 而不是正则:清单里还有 sdk/keywords 之类的 "version" 字样,
  // 正则取第一个会张冠李戴。优先级排在 plugin.toml / pyproject.toml 之后,
  // 市场插件条目仍以宿主认的那份清单为准。
  for (const base of bases) {
    const p = path.join(root, ...(base + ".claude-plugin/plugin.json").split("/"))
    if (!fs.existsSync(p)) continue
    try {
      const j = JSON.parse(fs.readFileSync(p, "utf8"))
      if (j && typeof j.version === "string" && j.version) {
        return { version: j.version, version_source: "plugin.json" }
      }
    } catch {}
  }
  return { version: null, version_source: null }
}

/** 锚点:树哈希保证「没变」,锚点负责在变的时候说人话。file 相对条目根。
 *  opts.strip 用来核对「还没换到位的 staging 目录」:锁里的 file 带 `src/` 前缀,
 *  而 staging 本身就是未来的 src,所以核对前把这个前缀摘掉。 */
function checkAnchors(entryRoot, anchors = [], opts = {}) {
  const strip = opts.strip ? String(opts.strip).replace(/\/+$/, "") + "/" : ""
  const missing = []
  for (const a of anchors) {
    const rel = strip && String(a.file).startsWith(strip) ? String(a.file).slice(strip.length) : String(a.file)
    const p = path.join(entryRoot, ...rel.split("/"))
    if (!fs.existsSync(p)) { missing.push(a.id + "(" + a.file + " 不存在)"); continue }
    const text = fs.readFileSync(p, "utf8")
    const all = a.all || []
    const any = a.any || []
    const absent = all.filter((s) => !text.includes(s))
    if (absent.length) missing.push(a.id + "(缺 " + absent.join(" / ") + ")")
    if (any.length && !any.some((s) => text.includes(s))) missing.push(a.id + "(any 都不在)")
  }
  return missing
}

module.exports = {
  LOCK_FILE, SCHEMA, ALGO, MAX_FILE, KEEP_EXT, KEEP_NAME, SKIP_DIRS,
  sha256buf, keepFile, listCorpusFiles, treeHash, lockPath, loadLock, saveLock,
  assertSlug, SLUG_RE, readVersion, skillMdPaths, checkAnchors,
}
