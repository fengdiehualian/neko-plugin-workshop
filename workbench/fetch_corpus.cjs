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
//   node workbench/fetch_corpus.cjs               # 拉全部在架插件(抓分支 HEAD)
//   node workbench/fetch_corpus.cjs --only shell_cmd,tavily_search
//   node workbench/fetch_corpus.cjs --limit 30    # 先拉 30 个看看
//   node workbench/fetch_corpus.cjs --dry-run
//
// 语料锁(见 market-corpus/corpus.lock.json,格式见 workbench/corpus-lock.cjs):
//   node workbench/fetch_corpus.cjs --locked            # 只按锁里的 commit 抓,锁外 slug 跳过并报告
//   node workbench/fetch_corpus.cjs --locked --verify-only  # 只校验磁盘上的语料,不联网(CI 用)
//   node workbench/fetch_corpus.cjs --update reuse_before_build --ref v0.3.22   # 追新版本
//   node workbench/fetch_corpus.cjs --update reuse_before_build                   # 按锁里的 commit 重抓,不追新
//   node workbench/fetch_corpus.cjs --update-all        # 过滤规则改了之后整体重锁
const fs = require("fs")
const os = require("os")
const path = require("path")
const { spawnSync } = require("child_process")
const locklib = require("./corpus-lock.cjs")

const REPO = path.resolve(__dirname, "..")
const OUT = process.env.NEKO_CORPUS_DIR || path.join(REPO, "market-corpus")
const API = "https://market.project-neko.cn/api/v1/plugins"
const MIRRORS = ["https://ghfast.top/", "https://gh-proxy.com/", ""]
const MAX_FILE = locklib.MAX_FILE        // 单个文件上限:二进制/大资产不进语料
const KEEP_EXT = locklib.KEEP_EXT
const SKIP_DIRS = locklib.SKIP_DIRS
// 许可/说明类文件没有扩展名,按 KEEP_EXT 会被整类丢掉;语料里少了 LICENSE,
// THIRD_PARTY_NOTICES 与各接入线文档就指向不存在的路径。
// 带连字符的变体(LICENSE-MIT / LICENSE-APACHE / UNLICENSE)是 collect_notices.cjs
// 的 LIC_NAMES 明确要认的名字,漏了会把它报成「未声明 license」
const KEEP_NAME = locklib.KEEP_NAME

function arg(name, d) {
  const i = process.argv.indexOf("--" + name)
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : d
}
const dryRun = process.argv.includes("--dry-run")
const lockedMode = process.argv.includes("--locked")
const verifyOnly = process.argv.includes("--verify-only")
const updateAll = process.argv.includes("--update-all")
const updateMode = process.argv.includes("--update")
const updateSlug = arg("update", "")
const updateRef = arg("ref", "")
// tag 被移动时,--update 默认拒绝:同一个 ref 指向了新 commit,意味着「版本号没变、内容变了」。
// 确认是上游有意重打 tag 之后再显式加这个开关。
const acceptMoved = process.argv.includes("--accept-moved")

// --ssl-no-revoke 是 Windows Schannel 专有开关:Linux/macOS 的 curl 会把它当未知参数直接失败,
// 所以按平台给。CI/服务器上跑 --update 的时候不能靠它。
const CURL_TLS = process.platform === "win32" ? ["--ssl-no-revoke"] : []
/** api.github.com 未认证只有 60 次/小时;--update-all 或多条重锁会中途 403。
 *  有 GITHUB_TOKEN 就带上,没带也照旧(只是会撞限额,错误信息里会说清)。 */
function githubAuthArgs(url) {
  const token = process.env.GITHUB_TOKEN || process.env.GH_TOKEN || ""
  if (!token || !url.startsWith("https://api.github.com/")) return []
  return ["-H", "Authorization: Bearer " + token, "-H", "Accept: application/vnd.github+json"]
}
function httpGet(url, timeout = 60000) {
  const r = spawnSync("curl", ["-sSL", "--max-time", String(Math.round(timeout / 1000)), ...CURL_TLS, ...githubAuthArgs(url), url],
    { encoding: "buffer", maxBuffer: 1 << 28 })
  if (r.status !== 0 || !r.stdout || !r.stdout.length) {
    const err = String(r.stderr || "").slice(0, 120)
    throw new Error("curl 失败 " + url + ":" + err +
      (url.startsWith("https://api.github.com/") ? "(若是限额,设置 GITHUB_TOKEN 再跑)" : ""))
  }
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
/** 把 from 下的文件按语料白名单复制到 dest。过滤口径只有这一处:
 *  zip 通道与 git 通道共用,否则两条通道会各自漂出一套「什么算语料」。 */
function copyFilteredTree(from, dest) {
  let kept = 0
  const walk = (srcDir, relTo) => {
    for (const e of fs.readdirSync(srcDir, { withFileTypes: true })) {
      if (e.isDirectory() && SKIP_DIRS.has(e.name)) continue
      const a = path.join(srcDir, e.name)
      if (e.isDirectory()) { walk(a, path.join(relTo, e.name)); continue }
      // lstat 而不是 stat:stat 会跟随符号链接,把链接指向的宿主文件(比如 ~/.ssh/id_rsa)
      // 读进语料。git 的 tar 比 zip 更容易带 symlink。设备/管道/socket 同理一律跳过。
      const st = fs.lstatSync(a)
      if (!st.isFile()) continue
      const name = e.name.toLowerCase()
      const ext = path.extname(name)
      const keep = KEEP_EXT.has(ext) || (ext === "" && KEEP_NAME.has(name))
      if (st.size > MAX_FILE || !keep) continue
      const target = path.join(dest, relTo, e.name)
      fs.mkdirSync(path.dirname(target), { recursive: true })
      fs.copyFileSync(a, target)
      kept++
    }
  }
  walk(from, "")
  return kept
}

// 只保留代码/配置/文档,且跳过超大文件;顺带把绝对路径与密钥形态的内容原样留着但不执行
function extractZip(zipPath, dest) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "wbcorpus-"))
  // 解压全失败、或 walk 中途抛错时 tmp 也要清掉,否则每失败一次就在 os.tmpdir() 留一份解包内容
  try {
    // zip 不是 tar:GNU tar 会直接拒绝(Windows/macOS 的 bsdtar 才认),按平台依次退到 unzip / python
    const attempts = [
      ["tar", ["-xf", zipPath, "-C", tmp]],
      ["unzip", ["-q", zipPath, "-d", tmp]],
      ["python", ["-m", "zipfile", "-e", zipPath, tmp]],
    ]
    let unpacked = false
    let lastErr = ""
    for (const [cmd, cmdArgs] of attempts) {
      const r = spawnSync(cmd, cmdArgs, { encoding: "latin1" })
      if (r.status === 0) { unpacked = true; break }
      lastErr = String(r.stderr || (r.error && r.error.message) || "").slice(0, 160)
    }
    if (!unpacked) throw new Error("解压失败(tar/unzip/python 都不行):" + lastErr)
    // zip 顶层是 <repo>-<branch>/:先剥掉这一层,否则语料会变成 dest/<repo>-main/rbb_core.py。
    // 剥完直接落到 dest(src),与"dest/src 存在就跳过"的判断对齐。
    const entries = fs.readdirSync(tmp, { withFileTypes: true })
    const topDirs = entries.filter((e) => e.isDirectory())
    const root = topDirs.length === 1 && entries.length === 1 ? path.join(tmp, topDirs[0].name) : tmp
    return copyFilteredTree(root, dest)
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true })
  }
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
const sha256buf = locklib.sha256buf

// ---- 锁模式公用的抓取原语 -------------------------------------------------

/** 把 ref/tag/分支解析成 commit sha;不依赖 gh CLI,走公开 REST。 */
function resolveCommit(ghRepo, ref) {
  const raw = httpGet("https://api.github.com/repos/" + ghRepo + "/commits/" + encodeURIComponent(ref), 30000)
  let j = null
  try { j = JSON.parse(raw.toString("utf8")) } catch { throw new Error("GitHub 返回不是 JSON:" + ghRepo + "@" + ref) }
  if (!j || !j.sha) throw new Error("解析不到 commit:" + ghRepo + "@" + ref)
  return j.sha
}

/** 按 commit 抓 zip(镜像依次尝试),返回 { buf, mirror }。 */
function downloadAtCommit(ghRepo, commit) {
  let lastErr = ""
  for (const m of MIRRORS) {
    const url = m + "https://codeload.github.com/" + ghRepo + "/zip/" + commit
    try {
      const buf = httpGet(url, 120000)
      if (buf.length < 1000) throw new Error("内容太小,疑似不是压缩包")
      return { buf, mirror: m }
    } catch (e) { lastErr = String((e && e.message) || e).slice(0, 160) }
  }
  throw new Error("三个镜像都没下到 " + ghRepo + "@" + commit + ":" + lastErr)
}

/** 抓一份语料落到 <slug>/src(先写 src.partial,校验通过再换),返回树的实测值。 */
function installAtCommit(slug, ghRepo, commit) {
  const dest = path.join(OUT, slug)
  const staging = path.join(dest, "src.partial")
  const { buf, mirror } = downloadAtCommit(ghRepo, commit)
  fs.rmSync(staging, { recursive: true, force: true })
  fs.mkdirSync(staging, { recursive: true })
  const zipPath = path.join(os.tmpdir(), "wb-" + slug + ".zip")
  try {
    fs.writeFileSync(zipPath, buf)
    const kept = extractZip(zipPath, staging)
    const measured = locklib.treeHash(staging)
    return { kept, measured, archive_sha256: sha256buf(buf), mirror, staging, zipPath, archive_format: "zip" }
  } catch (e) {
    try { fs.rmSync(staging, { recursive: true, force: true }) } catch {}
    throw e
  } finally {
    try { fs.rmSync(zipPath, { force: true }) } catch {}
  }
}

// ---- git 来源 ------------------------------------------------------------
// 工具仓(如 plugin-forge)不是市场插件:没有市场 zip,来源是 git repo + tag/sha。
// 两条硬约束都是实测踩出来的:
//   1 抓取次序:**完整 40 位 sha 优先,失败再退 refs/tags|heads**。
//      实测 40 位 sha 的 `git fetch <url> <sha>` GitHub 接受;7 位缩写会
//      `couldn't find remote ref 722529a`(早期我用缩写测,得出过"裸 sha 不能抓"的错结论)。
//      钉 sha 比钉 tag 稳:tag 可被删或移动,sha 不会。
//   2 `git archive` 的输出受 core.autocrlf / core.eol 影响:同一 commit 在本地仓库与
//      浅克隆出的 tar 字节不同(实测 1,259,520 vs 1,269,760)。不固定这两个参数的话,
//      archive_sha256 只在一台机器上成立。
const GIT_EOL = ["-c", "core.autocrlf=false", "-c", "core.eol=lf"]
// 本机 Windows 的 Schannel 完成不了证书吊销检查时,git 直接报
// CRYPT_E_NO_REVOCATION_CHECK 并拒绝访问;curl 那条路早有 `--ssl-no-revoke` 同理。
//
// 两个开关,免得把用户环境写死:
//   WB_GIT_SSL_BACKEND=openssl  换后端(企业 CA 环境常用);
//   WB_GIT_STRICT_TLS=1         完全不加这两个参数,严格按本机 git 配置走。
// 不加 sslBackend 单独设 schannelCheckRevoke 是无效的——那个选项只属于 schannel 后端。
const GIT_TLS = (() => {
  if (process.platform !== "win32") return []
  if (process.env.WB_GIT_STRICT_TLS === "1") return []
  const backend = process.env.WB_GIT_SSL_BACKEND || "schannel"
  return ["-c", "http.sslBackend=" + backend, "-c", "http.schannelCheckRevoke=false"]
})()

function gitRun(args, opts = {}) {
  const r = spawnSync("git", [...GIT_TLS, ...args], {
    encoding: "utf8",
    maxBuffer: 1 << 28,
    timeout: 180000,                                     // 私有仓/404 时 git 会一直等凭据,必须有上限
    env: { ...process.env, GIT_TERMINAL_PROMPT: "0" },   // 不弹凭据窗口,失败信息里说清
    ...opts,
  })
  if (r.error) {
    const why = r.error.code === "ETIMEDOUT"
      ? "超时 180s(网络不通,或在等凭据)"
      : r.error.message
    throw new Error("git 跑不起来(" + why + ")。这条通道需要本机装 git 且能访问 github.com")
  }
  if (r.status !== 0) {
    const tail = String(r.stderr || r.stdout || "").slice(0, 200)
    const hint = /could not read Username|terminal prompts disabled|Authentication failed/i.test(tail)
      ? "(需要凭据:私有仓要设 GITHUB_TOKEN 或先在命令行登录)"
      : ""
    throw new Error("git 失败(" + args.filter((x) => !x.startsWith("-")).slice(0, 2).join(" ") + "):" + tail + hint)
  }
  return r.stdout
}

/** 按 commit 抓一份 git 语料,落到 <slug>/src.partial,返回与 zip 通道同形的实测值。 */
function installFromGit(slug, ghRepo, ref, expectCommit) {
  const dest = path.join(OUT, slug)
  const staging = path.join(dest, "src.partial")
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "wbgit-"))
  const url = "https://github.com/" + ghRepo + ".git"
  try {
    gitRun(["init", "-q", tmp])
    let sha = ""
    let lastErr = ""
    // 完整 sha 先试,再退 tag/分支。ref 可能是 "HEAD"(latestRef 的回退值),
    // 那种情况下 refs/tags|heads/HEAD 都不存在,只能靠 sha 那条路。
    const specs = []
    if (/^[0-9a-f]{40}$/.test(String(expectCommit || ""))) specs.push(String(expectCommit))
    if (ref) specs.push("refs/tags/" + ref, "refs/heads/" + ref)
    for (const spec of specs) {
      try {
        gitRun(["-C", tmp, "fetch", "-q", "--depth", "1", "--no-tags", url, spec + ":refs/corpus-ref"])
        sha = gitRun(["-C", tmp, "rev-parse", "refs/corpus-ref^{commit}"]).trim()
        break
      } catch (e) { lastErr = String((e && e.message) || e).slice(0, 180) }
    }
    if (!sha) throw new Error("抓不到 " + ghRepo + "@" + (expectCommit || ref) + ":" + lastErr)
    if (expectCommit && sha !== expectCommit) {
      throw new Error("抓到的是 " + sha.slice(0, 12) + ",锁里钉的是 " +
        String(expectCommit).slice(0, 12) + "——ref 被移动过,先确认再改锁")
    }
    const tar = gitRun(["-C", tmp, ...GIT_EOL, "archive", "--format=tar", sha], { encoding: "buffer" })
    const tarPath = path.join(tmp, "corpus.tar")
    fs.writeFileSync(tarPath, tar)
    const raw = path.join(tmp, "raw")
    fs.mkdirSync(raw, { recursive: true })
    // 用相对文件名 + cwd:Git Bash 的 GNU tar 会把 `C:\…` 的冒号当成远程主机
    // (tar 的 host:path 语法),报 "Cannot connect to C:"。zip 通道那条一直靠
    // unzip/python 兜底,这条没有兜底,必须一开始就用相对路径。
    const u = spawnSync("tar", ["-xf", "corpus.tar", "-C", "raw"], { cwd: tmp, encoding: "latin1" })
    if (u.status !== 0) throw new Error("解 tar 失败:" + String(u.stderr || "").slice(0, 160))
    fs.rmSync(staging, { recursive: true, force: true })
    fs.mkdirSync(staging, { recursive: true })
    const kept = copyFilteredTree(raw, staging)
    const measured = locklib.treeHash(staging)
    return { kept, measured, archive_sha256: sha256buf(tar), mirror: "git", staging, commit: sha, archive_format: "tar" }
  } catch (e) {
    try { fs.rmSync(staging, { recursive: true, force: true }) } catch {}
    throw e
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true })
  }
}

function commitInstall(slug, staging) {
  const dest = path.join(OUT, slug)
  fs.rmSync(path.join(dest, "src"), { recursive: true, force: true })
  fs.renameSync(staging, path.join(dest, "src"))
  // 锁取代 .done:同一目录两套"抓完了"的判断标准,迟早会有一个说谎
  fs.rmSync(path.join(dest, ".done"), { force: true })
}

// ---- --locked / --verify-only ---------------------------------------------

/** 锁条目形状检查,返回问题描述(没问题返回 null)。
 *  以前 runLocked 直接 `entry.tree_sha256.slice(...)` / `path.join(OUT, slug)`:
 *  条目缺字段时抛 TypeError 整个扫描中止;slug 写 `../x` 还会越出语料目录。
 *  这里统一拦住,让「这条不合规」而不是「程序崩了」。 */
function entryProblem(slug, entry) {
  try { locklib.assertSlug(slug) } catch (e) { return String((e && e.message) || e) }
  // 锁里写成 `"x": null` 时,下面每个 entry.xxx 都会抛 TypeError 把整轮扫描打断
  if (!entry || typeof entry !== "object") return "条目不是对象:" + JSON.stringify(entry)
  if (!/^[\w.-]+\/[\w.-]+$/.test(String(entry.source || ""))) return "source 不是 owner/repo:" + entry.source
  if (!/^[0-9a-f]{40}$/.test(String(entry.commit || ""))) return "commit 不是 40 位十六进制:" + String(entry.commit || "").slice(0, 24)
  if (!/^[0-9a-f]{64}$/.test(String(entry.computedHash || ""))) return "缺 computedHash"
  if (typeof entry.files !== "number") return "缺 files"
  if (entry.transport === "git" && !entry.ref) return "git 来源缺 ref(用于识别 tag 被移动;抓取以 commit 为准)"
  return null
}

function runLocked() {
  const lock = locklib.loadLock(OUT)
  const slugs = Object.keys(lock.entries)
  if (!slugs.length) { console.error("FAIL  corpus.lock.json 里没有任何条目"); return 1 }
  let failed = 0, passed = 0, fetched = 0
  for (const slug of slugs) {
    const entry = lock.entries[slug]
    const bad = entryProblem(slug, entry)
    if (bad) { failed++; console.error("FAIL  " + slug + "  锁条目不合规:" + bad); continue }
    const root = path.join(OUT, slug)
    const current = locklib.treeHash(path.join(root, "src"))

    if (current && current.computedHash === entry.computedHash) {
      const missing = locklib.checkAnchors(root, entry.anchors)
      if (missing.length) {
        failed++
        console.error("FAIL  " + slug + "  树哈希一致但锚点缺失:" + missing.join(", "))
      } else {
        passed++
        console.log("PASS  " + slug + "  tree=" + current.computedHash.slice(0, 16) + "… files=" + current.files + "  (已落盘,未联网)")
      }
      continue
    }
    if (current) {
      failed++
      console.error("FAIL  " + slug + "  树哈希不符:磁盘 " + current.computedHash.slice(0, 16) + "… files=" + current.files +
        " vs 锁 " + entry.computedHash.slice(0, 16) + "… files=" + entry.files +
        "。要么语料被改过,要么过滤规则变了 —— 确认是故意的之后跑 --update-all 重锁。")
      continue
    }
    if (verifyOnly) {
      failed++
      console.error("FAIL  " + slug + "  语料缺失(" + path.join(root, "src") + " 不存在),--verify-only 不联网")
      continue
    }
    // 锁里有、磁盘没有:按锁里的 commit 抓一份,再逐项核对
    try {
      const r = entry.transport === "git"
        ? installFromGit(slug, entry.source, entry.ref || entry.commit, entry.commit)
        : installAtCommit(slug, entry.source, entry.commit)
      if (r.archive_sha256 !== entry.archive_sha256) {
        console.warn("WARN  " + slug + "  archive sha256 与锁不符(镜像/网络差异,只警告):" + r.archive_sha256.slice(0, 16) + "… vs " + String(entry.archive_sha256).slice(0, 16) + "…")
      }
      if (r.measured.computedHash !== entry.computedHash) {
        fs.rmSync(r.staging, { recursive: true, force: true })
        failed++
        console.error("FAIL  " + slug + "  抓下来的树与锁不符(commit " + entry.commit.slice(0, 12) + "):" +
          "实测 " + r.measured.computedHash.slice(0, 16) + "… files=" + r.measured.files + " vs 锁 " + entry.computedHash.slice(0, 16) + "… files=" + entry.files)
        continue
      }
      commitInstall(slug, r.staging)
      const missing = locklib.checkAnchors(root, entry.anchors)
      if (missing.length) {
        failed++
        console.error("FAIL  " + slug + "  已落盘但锚点缺失:" + missing.join(", "))
      } else {
        fetched++
        console.log("FETCH " + slug + "  ← " + entry.source + "@" + entry.commit.slice(0, 12) + "  tree=" + r.measured.computedHash.slice(0, 16) + "… files=" + r.measured.files)
      }
    } catch (e) {
      failed++
      console.error("FAIL  " + slug + "  抓取失败:" + String((e && e.message) || e).slice(0, 160))
    }
  }

  // 锁外条目:明确报告,不静默抓 HEAD(那会让"锁里的语料"和"没锁的语料"混成一份说不清的目录)
  const onDisk = fs.readdirSync(OUT, { withFileTypes: true }).filter((e) => e.isDirectory()).map((e) => e.name)
  const unlocked = onDisk.filter((s) => !slugs.includes(s) && fs.existsSync(path.join(OUT, s, "src")))
  if (unlocked.length) {
    console.log("\n跳过锁外条目 " + unlocked.length + " 个(未在锁里登记,本次不抓):" + unlocked.slice(0, 8).join(", ") + (unlocked.length > 8 ? " …" : ""))
  }
  console.log("\n锁校验:通过 " + passed + " 个,新抓 " + fetched + " 个,失败 " + failed + " 个")
  return failed ? 1 : 0
}

// ---- --update / --update-all ---------------------------------------------

function runUpdate() {
  const lock = locklib.loadLock(OUT)
  // 用法错误必须走退出码 2:assertSlug("") 抛异常的话会掉到最外层的 catch,
  // 变成 exit 1 的「出错」,和「校验失败」混成一个码
  if (!updateAll && !updateSlug) {
    console.error("--update 需要 slug:node workbench/fetch_corpus.cjs --update reuse_before_build [--ref v0.3.22]")
    return 2
  }
  let targets = []
  if (updateAll) targets = Object.keys(lock.entries)
  else {
    try { targets = [locklib.assertSlug(updateSlug)] }
    catch (e) { console.error(String((e && e.message) || e)); return 2 }
  }
  if (!targets.length) {
    console.error("corpus.lock.json 里没有条目,没有可更新/重锁的对象")
    return 2
  }
  let failed = 0, changed = 0
  for (const slug of targets) {
    const entry = lock.entries[slug]
    if (!entry) { console.error("FAIL  " + slug + "  不在锁里。新条目请先手工加一条,再 --update。"); failed++; continue }
    const ghRepo = entry.source
    // 钉 SHA 优先:没显式给 --ref 时,锁里的 commit 就是权威,不回头问 tag 现在指哪。
    // 追新版本必须显式给 --ref <tag 或 sha>——"自动跟随 tag"正是要避免的那件事。
    const explicitRef = updateRef || ""
    const ref = explicitRef || entry.ref || ""
    try {
      const commit = explicitRef
        ? resolveCommit(ghRepo, explicitRef)
        : (entry.commit || resolveCommit(ghRepo, ref || "HEAD"))
      // 只有显式给 --ref 时才可能"ref 指向了新 commit";没给 ref 时 commit 就是锁里的值
      if (explicitRef && entry.ref === explicitRef && entry.commit && commit !== entry.commit && !acceptMoved) {
        failed++
        console.error("FAIL  " + slug + "  ref " + ref + " 现在指向 " + commit.slice(0, 12) +
          ",锁里钉的是 " + entry.commit.slice(0, 12) +
          "——ref 没变但内容变了(tag 被移动)。确认上游是有意重打后再加 --accept-moved 重跑。")
        continue
      }
      const root = path.join(OUT, slug)
      const current = locklib.treeHash(path.join(root, "src"))
      if (current && entry.source && commit === entry.commit && current.computedHash === entry.computedHash) {
        console.log("SAME  " + slug + "  " + ref + "@" + commit.slice(0, 12) + "  已是这一份,跳过")
        continue
      }
      const r = entry.transport === "git"
        ? installFromGit(slug, ghRepo, ref, commit)
        : installAtCommit(slug, ghRepo, commit)
      const v = locklib.readVersion(r.staging)
      // 重锁前先按锁里的锚点核对一遍新内容:上游把 plugin.toml / SKILL.md 改名或挪走时,
      // 树哈希照样能算、锁照样会写成功,坏掉的是下一次 --verify 的解释力
      const anchorsMissing = locklib.checkAnchors(r.staging, entry.anchors || [], { strip: "src/" })
      if (anchorsMissing.length) {
        fs.rmSync(r.staging, { recursive: true, force: true })
        failed++
        console.error("FAIL  " + slug + "  新内容不满足锁里的锚点:" + anchorsMissing.join(", ") +
          "。上游结构变了,先改 corpus.lock.json 的 anchors 再重锁。")
        continue
      }
      const dry = dryRun
      console.log((dry ? "[dry] " : "") + "UPDATE " + slug + "  " + ref + "@" + commit.slice(0, 12) +
        "\n  tree   " + String(entry.computedHash).slice(0, 16) + "… files=" + entry.files + "  →  " + r.measured.computedHash.slice(0, 16) + "… files=" + r.measured.files +
        "\n  archive " + String(entry.archive_sha256).slice(0, 16) + "…  →  " + r.archive_sha256.slice(0, 16) + "…" +
        "\n  version " + (entry.version || "?") + " (" + (entry.version_source || "?") + ")  →  " + (v.version || "?") + " (" + (v.version_source || "?") + ")" +
        "\n  mirror  " + (r.mirror || "直连"))
      if (dry) {
        fs.rmSync(r.staging, { recursive: true, force: true })
        continue
      }
      commitInstall(slug, r.staging)
      if (!v.version) console.warn("WARN  " + slug + "  新内容里读不到版本号,保留锁里的旧值 " + (entry.version || "?") + "(结构可能变了)")
      lock.entries[slug] = {
        ...entry,
        ref,
        commit,
        version: v.version || entry.version || null,
        version_source: v.version ? v.version_source : (entry.version_source || null),
        archive_format: r.archive_format || entry.archive_format || "zip",
        archive_sha256: r.archive_sha256,
        computedHash: r.measured.computedHash,
        files: r.measured.files,
        updated_at: new Date().toISOString(),
      }
      changed++
    } catch (e) {
      failed++
      console.error("FAIL  " + slug + "  " + updateRef + " 更新失败:" + String((e && e.message) || e).slice(0, 160))
    }
  }
  if (changed && !dryRun) locklib.saveLock(OUT, lock)
  console.log("\n更新:改动 " + changed + " 条,失败 " + failed + " 条" + (changed ? "(已写回 " + locklib.LOCK_FILE + ",记得 review diff 再提交)" : ""))
  return failed ? 1 : 0
}

;(async () => {
  // 分派必须看「有没有 --update 这个开关」,不能看 slug 是否非空:
  // `--update` 后面忘了写 slug 时,原先会掉进 legacy 全量抓取(等于静默跑一次 129 条 HEAD),
  // 而不是报用法错误。
  if (updateAll || updateMode) return runUpdate()
  if (lockedMode || verifyOnly) return runLocked()

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
    const ref = spawnSync("curl", ["-sSL", "--max-time", "25", ...CURL_TLS, "-o", os.devNull, "-w", "%{http_code}",
      "https://codeload.github.com/" + gh[1] + "/zip/refs/heads/main"], { encoding: "latin1" })
    const branch = (ref.stdout || "").trim() === "200" ? "main" : "master"
    let got = false
    for (const m of MIRRORS) {
      // codeload 只认 zip/refs/heads/<branch>；archive/refs/heads/<branch>.zip 是 github.com 的路径，
      // 拼到 codeload 域名下必然 404(上面探测那行一直是对的,只有这行混用了两种形式)
      const url = m + "https://codeload.github.com/" + gh[1] + "/zip/refs/heads/" + branch
      const zipPath = path.join(os.tmpdir(), "wb-" + slug + ".zip")
      // 先写进 src.partial,成功后再改名成 src:否则 ENOSPC 之类中途失败会留下半成品,
      // 而"dest/src 存在就跳过"会把半成品当成抓完了,之后再也补不上
      const staging = path.join(dest, "src.partial")
      try {
        const buf = httpGet(url, 120000)
        if (buf.length < 1000) throw new Error("内容太小,疑似不是压缩包")
        fs.writeFileSync(zipPath, buf)
        if (dryRun) { ok++; console.log("  [dry] " + slug + " ← " + url); got = true; break }
        fs.rmSync(staging, { recursive: true, force: true })
        fs.mkdirSync(staging, { recursive: true })
        const kept = extractZip(zipPath, staging)
        fs.renameSync(staging, path.join(dest, "src"))
        sources[slug] = { repo: gh[1], ref: "refs/heads/" + branch, zip_sha256: sha256buf(buf), files_kept: kept, fetchedAt: new Date().toISOString(), mirror: m, note: "抓的是分支 HEAD;要完全可复现请钉到 tag/commit" }
        fs.rmSync(zipPath, { force: true })
        console.log("  " + slug + ":保留 " + kept + " 个文件")
        ok++; got = true; break
      } catch (e) {
        try { fs.rmSync(zipPath, { force: true }) } catch {}
        try { fs.rmSync(staging, { recursive: true, force: true }) } catch {}
      }
    }
    if (!got) failed.push([slug, "三个镜像都没下到"])
  }
  if (!dryRun) saveSources(sources)
  console.log("\n完成:拉到 " + ok + " 个,跳过 " + skipped + " 个,失败 " + failed.length + " 个")
  if (failed.length) console.log(failed.slice(0, 20).map((f) => "  ✗ " + f.join(":")).join("\n"))
  console.log("\n许可提示:这些是第三方源码,仅本机阅读参考;" +
    "不许复制其代码进生成物,也不打包分发(详见 THIRD_PARTY_NOTICES.md)。")
  return (failed.length && !ok) ? 1 : 0
})().then((code) => process.exit(code || 0)).catch((e) => {
  console.log("出错:" + ((e && e.message) || e))
  process.exit(1)
})
