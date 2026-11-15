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
const rest = (ms) => { const t = Date.now(); while (Date.now() - t < ms) {} }

let localTag = ""
try { localTag = git(["rev-parse", tag + "^{}"], 30000).trim() } catch {}
if (!localTag) { console.log("本地没有 tag " + tag + "(先在版本号提交上打 tag)"); process.exit(1) }

const results = {}
for (const remote of ["gitee", "origin"]) {
  let last = ""
  for (let i = 1; i <= 12; i++) {
    try {
      git(["push", "--force-with-lease", remote, "main"])
      let remoteSha = ""
      try { remoteSha = (git(["ls-remote", remote, "refs/tags/" + tag], 180000).trim().split(/\s+/)[0] || "") } catch {}
      if (remoteSha && remoteSha !== localTag) {
        if (!forceTag) {
          throw new Error(remote + " 上的 " + tag + " 指向 " + remoteSha.slice(0, 7) + ",本地是 " + localTag.slice(0, 7) +
            ";已发布的 tag 默认不挪(要挪加 --force-tag,并接受「同名版本换内容」的后果)")
        }
        console.log("! " + remote + ":按 --force-tag 挪动已发布 tag")
        git(["push", "--force", remote, "refs/tags/" + tag])
      } else if (!remoteSha) {
        git(["push", remote, "refs/tags/" + tag])
      }
      results[remote] = "OK(第 " + i + " 次)"
      break
    } catch (e) {
      last = String((e && ((e.stdout || "") + (e.stderr || ""))) || (e && e.message) || e).replace(/\r?\n/g, " ").slice(-200)
      console.log(remote + " 第 " + i + " 次:" + last)
      rest(Math.min(30000, 3000 * i))
    }
  }
  if (!results[remote]) results[remote] = "FAILED:" + last
}
console.log(JSON.stringify({ tag, results }, null, 1))
process.exit(Object.values(results).some((r) => String(r).startsWith("FAILED")) ? 1 : 0)
