// 批量下载市场插件源码(带超时+跳过已完成)
import { mkdirSync, writeFileSync, existsSync } from "node:fs"

const items = JSON.parse(await Bun.file(process.env.TEMP + "/m_all.json").text())
console.log("total:", items.length)

const OUT = "C:/Users/Administrator/dev/market-plugins"
mkdirSync(OUT, { recursive: true })

let ok = 0, fail = 0
const failed = []
for (const it of items) {
  const slug = it.slug
  const dir = OUT + "/" + slug
  if (existsSync(dir + "/.done")) { ok++; continue }
  const repo = (it.repo_url || "").replace("https://github.com/", "").replace(/\/$/, "")
  if (!repo) { failed.push(slug + " (no repo)"); continue }
  let got = false
  for (const br of ["main", "master", "dev"]) {
    try {
      const r = await fetch(`https://codeload.github.com/${repo}/zip/refs/heads/${br}`, {
        headers: { "User-Agent": "Mozilla/5.0" },
        signal: AbortSignal.timeout(20000),
      })
      if (!r.ok) continue
      const buf = await r.arrayBuffer()
      if (buf.byteLength < 1000) continue
      mkdirSync(dir, { recursive: true })
      writeFileSync(dir + "/src.zip", buf)
      Bun.spawnSync(["powershell", "-NoProfile", "-Command",
        `Expand-Archive -Path '${dir}/src.zip' -DestinationPath '${dir}' -Force; Get-ChildItem '${dir}' -Directory | Where-Object { $_.Name -like '*-${br}' } | Rename-Item -NewName 'src'`])
      if (existsSync(dir + "/src")) { got = true; break }
    } catch (e) { /* timeout etc, try next branch */ }
  }
  if (got) { ok++; writeFileSync(dir + "/.done", "ok"); console.log("OK:", slug) }
  else { fail++; failed.push(slug) }
}
console.log(`done ok=${ok} fail=${fail}`)
if (failed.length) console.log("failed list:", failed.join(", "))
