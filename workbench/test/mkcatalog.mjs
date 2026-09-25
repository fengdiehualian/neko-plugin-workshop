// 为全部 46 个市场插件生成学习卡片目录(market-catalog.md)
import { readFileSync, writeFileSync, existsSync, readdirSync } from "node:fs"

const items = JSON.parse(await Bun.file(process.env.TEMP + "/m_all.json").text())
const analysis = JSON.parse(await Bun.file(process.env.TEMP + "/market_analysis.json").text())
const bySlug = Object.fromEntries(analysis.plugins.map((p) => [p.slug, p]))
const CORPUS = "C:\\Users\\Administrator\\dev\\market-plugins"

function srcInfo(slug) {
  const dir = `${CORPUS}\\${slug}\\src`
  if (!existsSync(dir)) return null
  const p = bySlug[slug]
  return { dir, ...(p || {}) }
}

function entryList(initPath) {
  // 从 __init__.py 抽取 @plugin_entry / @llm_tool / @timer_interval 的 id/name/description
  if (!existsSync(initPath)) return []
  const t = readFileSync(initPath, "utf8")
  const out = []
  for (const m of t.matchAll(/@plugin_entry\(\s*id=["']([\w-]+)["'],\s*name=["']([^"']{0,40})["']/g)) {
    out.push(["entry", m[1], m[2]])
  }
  for (const m of t.matchAll(/@llm_tool\(\s*name=["']([\w-]+)["'],\s*description=["']([^"']{0,60})/g)) {
    out.push(["llm_tool", m[1], m[2]])
  }
  for (const m of t.matchAll(/@timer_interval\(\s*id=["']([\w-]+)["']/g)) {
    out.push(["timer", m[1], ""])
  }
  return out
}

let md = `# 市场插件全量目录(46 个真实上架插件的学习卡片)

> 每张卡片 = 一个真实上架插件。**源码全部在本机**,需要看完整实现直接读对应 src 目录(你有全局权限)。
> 用法:拿到需求 → 在下面按功能找同类插件 → 读它的 src 学结构与写法 → 动手。
> 四大支柱能力的统计与惯用法见 \`market-patterns.md\`。

`

// 按分类分组
const groups = {}
for (const it of items) {
  const cats = (it.categories || []).map((c) => c.name || c).join("/") || "综合"
  ;(groups[cats] = groups[cats] || []).push(it)
}

let total = 0
for (const [cat, list] of Object.entries(groups)) {
  md += `\n## ${cat}(${list.length} 个)\n\n`
  for (const it of list) {
    const info = srcInfo(it.slug)
    if (!info) continue
    total++
    const feats = (info.features || []).join(", ") || "基础"
    const dec = Object.entries(info.decorators || {}).map(([k, v]) => `${k}×${v}`).join(", ") || "-"
    const init = `${info.dir}\\__init__.py`
    md += `### ${it.slug} — ${it.name || it.slug}\n\n`
    md += `- 作者:${it.author_name || "?"} · 装机:${it.installer_count ?? "?"} · 下载:${it.download_count ?? "?"} · 点赞:${it.likes ?? "?"}\n`
    if (it.short_description) md += `- 简介:${it.short_description}\n`
    const rd = (it.readme || "").split("\n").find((l) => l.trim() && !l.startsWith("#"))
    if (rd) md += `- 功能:${rd.trim().slice(0, 100)}\n`
    md += `- 能力:${feats} | 装饰器:${dec}\n`
    const entries = entryList(`${info.dir}\\__init__.py`)
    if (entries.length) {
      md += `- 对外接口:\n`
      for (const [kind, id, name] of entries.slice(0, 12)) {
        md += `  - [${kind}] ${id}${name ? " — " + name : ""}\n`
      }
    }
    md += `- 源码:\`${info.dir}\`\n\n`
  }
}
md = md.replace(`(46 个)`, `(${total} 个)`)
md += `\n## 建议学习路径\n\n- 第一次做「对话触发」功能 → 读 shell_cmd / mini_games / neko_diary(llm_tool 重度用户)\n- 做「联网查询/聚合」→ anysearch / tavily_search / store_search\n- 做「定时提醒/监控」→ sys_monitor / mail_all / catgirl_daily_planner\n- 做「聊天记录/日记/存储」→ neko_diary / data_backup\n- 做「大型多功能」→ neko_arcade / galgame_plugin(多入口组织、router 拆分)\n`

const dst = "C:/Users/Administrator/dev/opencode/packages/workbench/packs/neko-plugin/skill/neko-plugin-dev/references/market-catalog.md"
writeFileSync(dst, md, "utf8")
console.log("catalog written:", dst, Math.round(md.length / 1024) + "KB, plugins:", total)
