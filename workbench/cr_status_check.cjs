// 查 PR #5 的 CodeRabbit 相关现状(只读 API):评论里有没有机器人动静、分支上有没有 .coderabbit 配置
const { execFileSync } = require("child_process")
const fs = require("fs")
const path = require("path")
const GIT = process.env.GIT_BIN || path.join(require("os").homedir(), ".workbuddy", "binaries", "PortableGit", "versions", "1.3.0", "cmd", "git.exe")
const REPO = path.resolve(__dirname, "..")
const OWNER = "fengdiehualian", NAME = "neko-plugin-workshop"

function cred(host) {
  const o = execFileSync(GIT, ["credential", "fill"], { input: "protocol=https\nhost=" + host + "\n\n", encoding: "latin1", cwd: REPO, timeout: 60000 })
  return (/password=(.*)/.exec(o) || [, ""])[1].trim()
}
async function api(p, tok) {
  const r = await fetch("https://api.github.com" + p, {
    headers: { "user-agent": "neko", ...(tok ? { authorization: "token " + tok } : {}), accept: "application/vnd.github+json" },
    signal: AbortSignal.timeout(40000),
  })
  const t = await r.text()
  let j = null; try { j = JSON.parse(t) } catch {}
  return { status: r.status, j, t }
}

;(async () => {
  const tok = cred("github.com")
  const pr = await api(`/repos/${OWNER}/${NAME}/pulls/5`, tok)
  if (!pr.j) { console.log("PR#5 取不到:", pr.status, pr.t.slice(0, 200)); process.exit(1) }
  console.log("PR #5:", pr.j.state, "| 标题:", pr.j.title)
  console.log("  head:", pr.j.head.sha.slice(0, 7), "分支", pr.j.head.ref, "| mergeable_state:", pr.j.mergeable_state)
  console.log("  评论数:", pr.j.comments, "审查评论:", pr.j.review_comments, "| 更新:", pr.j.updated_at)

  const ic = await api(`/repos/${OWNER}/${NAME}/issues/5/comments?per_page=30`, tok)
  const list = ic.j || []
  console.log("\n== 普通评论 " + list.length + " 条(作者:摘要) ==")
  for (const c of list) console.log("  [" + (c.created_at || "").slice(0, 16) + "] " + (c.user && c.user.login) + ": " + String(c.body || "").replace(/\s+/g, " ").slice(0, 150))

  const rc = await api(`/repos/${OWNER}/${NAME}/pulls/5/comments?per_page=30`, tok)
  console.log("\n== 行级审查评论 " + ((rc.j || []).length) + " 条 ==")
  for (const c of rc.j || []) console.log("  [" + (c.created_at || "").slice(0, 16) + "] " + (c.user && c.user.login) + " @" + c.path + ":" + c.line)

  const rv = await api(`/repos/${OWNER}/${NAME}/pulls/5/reviews`, tok)
  console.log("\n== reviews " + ((rv.j || []).length) + " 条 ==")
  for (const v of rv.j || []) console.log("  " + (v.user && v.user.login) + " " + v.state + " " + (v.submitted_at || "").slice(0, 16))

  // 分支上有没有 CodeRabbit 配置文件
  for (const f of [".coderabbit.yaml", ".coderabbit.yml"]) {
    const c = await api(`/repos/${OWNER}/${NAME}/contents/${f}?ref=${pr.j.head.sha}`, tok)
    console.log("\n" + f + ":", c.status === 200 ? "存在(" + Buffer.from(c.j.content || "", "base64").toString("utf8").slice(0, 300).replace(/\s+/g, " ") + "…)" : `不存在(HTTP ${c.status})`)
  }
  // main 上有没有
  const m = await api(`/repos/${OWNER}/${NAME}/contents/.coderabbit.yaml?ref=main`, tok)
  console.log("main 分支 .coderabbit.yaml:", m.status === 200 ? "存在" : "不存在")
  // 最近提交里有没有 coderabbit 相关 check
  const sts = await api(`/repos/${OWNER}/${NAME}/commits/${pr.j.head.sha}/check-runs`, tok)
  const names = ((sts.j && sts.j.check_runs) || []).map((x) => x.name + ":" + x.status + "/" + (x.conclusion || "-"))
  console.log("\nhead 提交上的 check runs:", names.length ? names.join(" , ") : "(无)")
})().catch((e) => console.log("出错:", String(e.message || e).slice(0, 200)))
