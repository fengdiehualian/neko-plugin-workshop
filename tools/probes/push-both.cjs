// 双推:gitee 与 origin(GitHub DNS 会整段抖动,失败就退避重试;一端失败不能挡另一端口)
const { execFileSync } = require("child_process")
const GIT = require("./paths.cjs").gitExe()
const env = Object.assign({}, process.env, { GIT_SSL_NO_VERIFY: "1" })
function git(args) {
  return execFileSync(GIT, ["-c", "http.proxy=", ...args], { env, encoding: "latin1", stdio: "pipe", timeout: 600000 })
}
const targets = ["gitee", "origin"]
const results = {}
for (const t of targets) {
  let last = ""
  for (let i = 1; i <= 12; i++) {
    try {
      git(["push", t, "main", "--force-with-lease"])
      // tag 用 --force:发布前常要把它挪到最新提交(补上工具改动),挪tag是预期操作
      git(["push", "--force", t, "refs/tags/" + (process.env.WB_TAG || "v1.2.63")])
      results[t] = "OK(第 " + i + " 次)"
      break
    } catch (e) {
      last = String(e.stdout || "") + String(e.stderr || "")
      console.log(t + " 第 " + i + " 次失败:" + last.replace(/\r?\n/g, " ").slice(-200))
      const ms = Math.min(30000, 3000 * i)
      const t0 = Date.now()
      while (Date.now() - t0 < ms) {}
    }
  }
  if (!results[t]) results[t] = "FAILED:" + last.replace(/\r?\n/g, " ").slice(-300)
}
console.log(JSON.stringify(results, null, 1))
