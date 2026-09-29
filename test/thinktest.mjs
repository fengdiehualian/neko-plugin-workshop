/**
 * thinktest - 用引擎里的真实历史消息验证 reasoning 抓取逻辑
 * (与 wb-agent-lib.mjs 的 driveAgent 轮询完全相同的过滤方式)
 * 用法: bun test/thinktest.mjs
 */
import { readFileSync } from "node:fs"

const auth = "Basic " + Buffer.from("opencode:neko-studio-pw").toString("base64")
const rt = "C:/Users/Administrator/AppData/Local/Programs/NEKOWorkshop/runtime"
const sids = JSON.parse(readFileSync(rt + "/sessions.json", "utf8")).items.map((s) => s.engineSessionId)

let found = 0
for (const sid of sids) {
  if (!sid) continue
  const r = await fetch(`http://127.0.0.1:4096/api/session/${sid}/message`, { headers: { authorization: auth } })
  if (!r.ok) continue
  const j = await r.json()
  const msgs = (j.data || []).filter((m) => m.type === "assistant")
  msgs.sort((a, b) => (a.time?.created || 0) - (b.time?.created || 0))
  // 与 driveAgent 相同:全量扫描本轮全部 assistant 消息,聚合 reasoning
  const thinkAll = []
  for (const m of msgs) {
    const tp = (m.content || []).filter((c) => (c.type === "reasoning" || c.type === "thinking") && c.text).map((c) => c.text)
    if (tp.length) thinkAll.push(tp.join("\n\n"))
  }
  if (thinkAll.length) {
    found++
    if (found === 1) {
      console.log("=== SAMPLE THINKING (from history) ===")
      console.log(thinkAll.join("\n\n").slice(0, 400))
    }
  }
}
console.log(`sessions with reasoning: ${found}/${sids.filter(Boolean).length}`)
