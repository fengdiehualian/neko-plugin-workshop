// 分析 46 个市场插件源码:能力使用统计 + 找范例
import { readFileSync, readdirSync, statSync, writeFileSync } from "node:fs"

const ROOT = "C:/Users/Administrator/dev/market-plugins"
const out = { plugins: [] }

function walk(dir, cb) {
  for (const e of readdirSync(dir)) {
    const p = dir + "/" + e
    const st = statSync(p)
    if (st.isDirectory()) walk(p, cb)
    else cb(p)
  }
}

for (const slug of readdirSync(ROOT)) {
  const dir = ROOT + "/" + slug
  if (!existsSync2(dir + "/.done")) continue
  const src = dir + "/src"
  const info = { slug, files: 0, py: 0, decorators: {}, features: new Set(), lines: 0, initSize: 0, toml: "" }
  try {
    walk(src, (p) => {
      info.files++
      if (p.endsWith(".py")) {
        info.py++
        const t = readFileSync(p, "utf8")
        info.lines += t.split("\n").length
        for (const d of ["plugin_entry", "llm_tool", "timer_interval", "lifecycle", "message", "neko_plugin"]) {
          const c = (t.match(new RegExp("@" + d + "\\b", "g")) || []).length
          if (c) info.decorators[d] = (info.decorators[d] || 0) + c
        }
        if (t.includes("PluginSettings")) info.features.add("settings")
        if (t.includes("push_message")) info.features.add("push_message")
        if (t.includes("register_dynamic_entry")) info.features.add("dynamic_entry")
        if (t.includes("self.store")) info.features.add("store")
        if (t.includes("register_static_ui")) info.features.add("static_ui")
        if (t.includes("data_path")) info.features.add("data_path")
        if (t.includes("cache_path")) info.features.add("cache_path")
        if (t.includes("include_router") || t.includes("PluginRouter")) info.features.add("router")
        if (t.includes("httpx") || t.includes("aiohttp") || t.includes("requests.")) info.features.add("http")
        if (t.includes("ai_behavior")) info.features.add("push_v2")
        if (t.includes("set_list_action")) info.features.add("list_actions")
        if (t.includes("report_status")) info.features.add("report_status")
      }
      if (p.endsWith("plugin.toml")) info.toml = readFileSync(p, "utf8")
      if (p.endsWith("__init__.py") && !info.initSize) info.initSize = statSync(p).size
    })
  } catch (e) { info.err = String(e).slice(0, 80) }
  info.features = [...info.features]
  out.plugins.push(info)
}

function existsSync2(p) { try { return statSync(p).isFile() } catch { return false } }

// 统计
const featCount = {}
let withSettings = 0, withLlm = 0, withPush = 0, withStore = 0, withUi = 0, withTimer = 0, withHttp = 0
for (const p of out.plugins) {
  for (const f of p.features) featCount[f] = (featCount[f] || 0) + 1
  if (p.features.includes("settings")) withSettings++
  if (p.decorators.llm_tool) withLlm++
  if (p.features.includes("push_message")) withPush++
  if (p.features.includes("store")) withStore++
  if (p.features.includes("static_ui")) withUi++
  if (p.decorators.timer_interval) withTimer++
  if (p.features.includes("http")) withHttp++
}
console.log("plugins:", out.plugins.length)
console.log("feature usage:", JSON.stringify(featCount, null, 1))
console.log(`settings=${withSettings} llm_tool=${withLlm} push=${withPush} store=${withStore} ui=${withUi} timer=${withTimer} http=${withHttp}`)
// 候选范例:代码量适中、能力丰富
const ranked = out.plugins
  .filter((p) => p.py > 0 && p.lines > 80 && p.lines < 1200)
  .sort((a, b) => b.features.length - a.features.length)
console.log("top feature-rich candidates:")
for (const p of ranked.slice(0, 12)) {
  console.log(`- ${p.slug}: lines=${p.lines} py=${p.py} feats=${p.features.join(",")} dec=${JSON.stringify(p.decorators)}`)
}
writeFileSync(process.env.TEMP + "/market_analysis.json", JSON.stringify(out, null, 1), "utf8")
