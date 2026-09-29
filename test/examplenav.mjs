import { readdirSync } from "node:fs"

// 打印三个范例插件的结构与关键片段位置
for (const slug of ["shell_cmd", "tavily_search", "sys_monitor"]) {
  const dir = "C:/Users/Administrator/dev/market-plugins/" + slug + "/src"
  console.log("=== " + slug + " ===")
  try {
    walk(dir, "")
  } catch (e) {
    console.log("  ERR", String(e).slice(0, 100))
  }
}

function walk(dir, rel) {
  for (const e of readdirSync(dir + (rel ? "/" + rel : ""))) {
    const r = rel ? rel + "/" + e : e
    const full = dir + "/" + r
    try {
      const st = Bun.file(full).size
      if (st > 200000) { console.log("  " + r + " (" + Math.round(st / 1024) + "KB, skipped)"); continue }
      if (e.endsWith(".py") || e.endsWith(".toml")) console.log("  " + r + " (" + Math.round(st / 1024) + "KB)")
      if (st.isDirectory?.()) continue
    } catch {}
    try {
      if (Bun.file(full).kind === "directory" || false) {}
    } catch {}
    let isDir = false
    try { readdirSync(full); isDir = true } catch {}
    if (isDir) walk(dir, r)
  }
}
