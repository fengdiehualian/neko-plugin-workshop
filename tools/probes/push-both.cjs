// 双推:gitee 与 origin(GitHub DNS/连接会整段抖动,失败就退避重试;一端失败绝不挡另一端)
//
// TLS 口径与 workbench/release_tls.py 保持一致(issue #11/#2):
//   默认**校验证书**,CA 用 PortableGit 自带的 ca-bundle.crt + OpenSSL 后端;
//   不再设 GIT_SSL_NO_VERIFY(那等于把凭据在不验证的线路上发),也不再默认强推 tag。
// tag 从 package.json 读,不写死历史版本号。
// 只有显式 --force-tag 才挪动已发布的 tag,并且会打印警告(同名版本换内容对排查是黑洞)。
const fs = require("fs")
const os = require("os")
const path = require("path")
const { execFileSync } = require("child_process")
const P = require("./paths.cjs")

const GIT = P.gitExe()
const REPO = P.REPO
const forceTag = process.argv.includes("--force-tag")
const ver = JSON.parse(fs.readFileSync(path.join(REPO, "package.json"), "utf8")).version
const tag = "v" + ver

function caBundle() {
  for (const p of [
    process.env.NEKO_CA_BUNDLE || "",
    path.join(os.homedir(), ".workbuddy", "binaries", "PortableGit", "versions", "1.3.0", "mingw64", "etc", "ssl", "certs", "ca-bundle.crt"),
    "C:\\Program Files\\Git\\mingw64\\etc\\ssl\\certs\\ca-bundle.crt",
  ]) {
    if (p && fs.existsSync(p)) return p
  }
  return ""
}
const ca = caBundle()
// -c 必须排在子命令之前;http.proxy= 是绕开环境里失效的代理,不是关校验
const TLS = ["-c", "http.proxy=", "-c", "http.sslVerify=true"]
if (ca) TLS.push("-c", "http.sslBackend=openssl", "-c", "http.sslCAInfo=" + ca)
else console.log("! 没找到 CA bundle,沿用系统默认校验(若报 0x80092013,设 NEKO_CA_BUNDLE)")

const env = Object.assign({}, process.env)
delete env.GIT_SSL_NO_VERIFY // 明确不接受这个开关
function git(args, timeout) {
  return execFileSync(GIT, [...TLS, ...args], { cwd: REPO, env, encoding: "latin1", stdio: "pipe", timeout: timeout || 600000 })
}
// 原来是 while(Date.now()) 忙等:退避期间白烧一个核(issue #23)。
// Atomics.wait 在 SharedArrayBuffer 上阻塞等待,既不 spin 也不要把脚本改成 async。
const __sleepBuf = new Int32Array(new SharedArrayBuffer(4))
const rest = (ms) => { try { Atomics.wait(__sleepBuf, 0, 0, ms) } catch { const t = Date.now(); while (Date.now() - t < ms) {} } }

let localTag = ""
try { localTag = git(["rev-parse", tag + "^{}"], 30000).trim() } catch {}
if (!localTag) { console.log("本地没有 tag " + tag + "(先在版本号提交上打 tag)"); process.exit(1) }

const results = {}
for (const remote of ["gitee", "origin"]) {
  // main 与 tag 分开记账:tag 分叉不该把已经推成功的 main 连坐成 FAILED(这正是本轮批评的那类错)
  let mainOk = "", tagNote = ""
  for (let i = 1; i <= 12 && !mainOk; i++) {
    try { git(["push", "--force-with-lease", remote, "main"]); mainOk = "OK(第 " + i + " 次)" }
    catch (e) {
      const msg = String((e && ((e.stdout || "") + (e.stderr || ""))) || (e && e.message) || e).replace(/\r?\n/g, " ").slice(-170)
      console.log(remote + " main 第 " + i + " 次:" + msg)
      rest(Math.min(30000, 3000 * i))
    }
  }
  if (!mainOk) { results[remote] = { main: "FAILED", tag: "跳过(main 没推上去)" } ; continue }
  try {
    let remoteSha = ""
    // ls-remote 对**注解 tag** 返回的是 tag 对象哈希,而本地比的是 peel 之后的提交哈希,
    // 直接比会把"完全一致"读成分叉(v1.2.65 发布后就假报过「远端 9cd972 ≠ 本地 89aa89」)。
    // 同时问 refs/tags/X 与 refs/tags/X^{}:后者由服务端给出 peel 后的提交,以它为准,取不到再退回前者。
    try {
      const out = git(["ls-remote", remote, "refs/tags/" + tag, "refs/tags/" + tag + "^{}"], 180000)
      const rows = out.split(/\r?\n/).map((l) => l.trim().split(/\s+/)).filter((r) => r.length >= 2 && r[0])
      const peeled = rows.find((r) => r[1].endsWith("^{}"))
      remoteSha = ((peeled || rows[0] || [])[0] || "")
    } catch {}
    if (!remoteSha) { git(["push", remote, "refs/tags/" + tag]); tagNote = "已推送(新建)" }
    else if (remoteSha === localTag) tagNote = "已一致 " + remoteSha.slice(0, 7)
    else if (forceTag) { git(["push", "--force", remote, "refs/tags/" + tag]); tagNote = "按 --force-tag 挪动 " + remoteSha.slice(0, 7) + " → " + localTag.slice(0, 7) }
    // 默认不动:已发布的 tag 挪动等于「同名版本换内容」,要动请显式加 --force-tag
    else tagNote = "未挪(远端 " + remoteSha.slice(0, 7) + " ≠ 本地 " + localTag.slice(0, 7) + ";要挪请加 --force-tag)"
  } catch (e) {
    tagNote = "tag 检查失败:" + String((e && (e.stderr || e.message)) || e).replace(/\r?\n/g, " ").slice(0, 120)
  }
  results[remote] = { main: mainOk, tag: tagNote }
}
console.log(JSON.stringify({ tag, results }, null, 1))
// 退出码只看 main:tag 分叉是「需要人来决定」的状态,不该被当成推送失败
process.exit(Object.values(results).some((r) => String(r.main).startsWith("FAILED")) ? 1 : 0)
