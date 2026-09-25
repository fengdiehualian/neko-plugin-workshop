/**
 * eventprobe - 抓取 v2 引擎 /api/event 的真实事件结构,诊断看门狗活动匹配
 * 用法: bun test/eventprobe.mjs
 */
const auth = "Basic " + Buffer.from("opencode:neko-studio-pw").toString("base64")
const seen = []
const controller = new AbortController()

const sse = fetch("http://127.0.0.1:4096/api/event", { signal: controller.signal, headers: { authorization: auth } })
  .then(async (res) => {
    console.log("SSE status:", res.status)
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buf = ""
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      let idx
      while ((idx = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, idx).trim()
        buf = buf.slice(idx + 1)
        if (!line.startsWith("data:")) continue
        try {
          const evt = JSON.parse(line.slice(5).trim())
          const d = evt.data || {}
          const shape = {
            type: evt.type,
            dataKeys: Object.keys(d).slice(0, 6).join(","),
            sessionID: d.sessionID || d.part?.sessionID || d.info?.sessionID || d.properties?.sessionID || "-",
          }
          if (seen.length < 40) seen.push(shape)
        } catch {}
      }
    }
  })
  .catch((e) => console.log("SSE error:", e.name))

await new Promise((r) => setTimeout(r, 2000))
// 触发一个 plan 任务(经 studio → driveAgent → 引擎)
const t0 = Date.now()
const task = fetch("http://127.0.0.1:5099/api/task", {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify({ text: "只回复:收到。不要使用任何工具", mode: "plan" }),
}).then((r) => r.json()).catch((e) => ({ err: String(e) }))

const result = await Promise.race([task, new Promise((r) => setTimeout(() => r({ timeout: true }), 120_000))])
console.log("task done in", Math.round((Date.now() - t0) / 1000) + "s:", JSON.stringify(result).slice(0, 200))
await new Promise((r) => setTimeout(r, 1500))
controller.abort()
await sse.catch(() => {})
console.log("=== captured events ===")
for (const s of seen) console.log(JSON.stringify(s))
