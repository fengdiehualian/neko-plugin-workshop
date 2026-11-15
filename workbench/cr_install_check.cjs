// 验证 CodeRabbit 是否已装到本仓库:App 安装会在仓库上建一条 app 类型的 webhook,
// 这对仓库管理员 PAT 可见(GET /repos/{owner}/{repo}/hooks),不需要 app 凭据。
const { execFileSync } = require("child_process")
const path = require("path")
const os = require("os")
const fs = require("fs")
const GIT = [process.env.GIT_BIN, path.join(os.homedir(), ".workbuddy", "binaries", "PortableGit", "versions", "1.3.0", "cmd", "git.exe")].find((p) => p && fs.existsSync(p)) || "git"
const OWNER = "fengdiehualian", NAME = "neko-plugin-workshop"
const tok = (/password=(.*)/.exec(execFileSync(GIT, ["credential", "fill"], { input: "protocol=https\nhost=github.com\n\n", encoding: "latin1", timeout: 60000 })) || [, ""])[1].trim()

async function api(p) {
  const r = await fetch("https://api.github.com" + p, { headers: { "user-agent": "neko", accept: "application/vnd.github+json", authorization: "token " + tok }, signal: AbortSignal.timeout(30000) })
  const t = await r.text()
  let j = null; try { j = JSON.parse(t) } catch {}
  return { status: r.status, j, t }
}
;(async () => {
  const h = await api(`/repos/${OWNER}/${NAME}/hooks?per_page=30`)
  if (h.status !== 200) { console.log("hooks 查询失败:", h.status, h.t.slice(0, 160)); process.exit(1) }
  console.log("仓库 webhook 共 " + h.j.length + " 条:")
  let cr = false
  for (const x of h.j) {
    const url = String((x.config && x.config.url) || "")
    const who = url.replace(/^https:\/\//, "").split("/")[0]
    const hit = /coderabbit/i.test(url)
    if (hit) cr = true
    console.log("  id=" + x.id + " " + (x.name || "?") + " → " + who + (hit ? "   ← CodeRabbit" : "") +
      " | events:" + (Array.isArray(x.events) ? (x.events.includes("*") ? "*" : x.events.length + " 项") : x.events) + " | active:" + x.active + " | 更新:" + String(x.updated_at).slice(0, 16))
  }
  console.log("\n结论:" + (cr
    ? "CodeRabbit 已安装(webhook 已存在)"
    : "本仓库还没有 CodeRabbit 的 webhook → 安装尚未完成"))
  // 顺带看 PR#5 的 mergeable 状态与 head(装好后要触发评审需要它可合并)
  const pr = await api(`/repos/${OWNER}/${NAME}/pulls/5`)
  if (pr.j) console.log("PR #5: state=" + pr.j.state + " head=" + pr.j.head.sha.slice(0, 7) + " mergeable=" + pr.j.mergeable + " mergeable_state=" + pr.j.mergeable_state)
})()
