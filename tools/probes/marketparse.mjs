import { writeFileSync } from "node:fs"

const j = JSON.parse(await Bun.file(process.env.TEMP + "/m1.json").text())
const items = j.items || []
console.log("count:", items.length, " total-registered:", j.total ?? "?")
console.log("item keys:", Object.keys(items[0]).join(", "))
// 全部插件的 slug + repo
const lines = items.map((it) => {
  const dl = it.download_count ?? it.downloads ?? ""
  return `${it.slug}  |  ${(it.name || "").slice(0, 24)}  |  ${it.repo_url || "no-repo"}  | dl=${dl}`
})
console.log(lines.slice(0, 30).join("\n"))
writeFileSync(process.env.TEMP + "/market_slugs.txt", lines.join("\n"), "utf8")
// 看看有没有下载/源码字段
const sample = items.find((x) => x.download_url || x.package_url)
console.log("sample dl fields:", sample ? JSON.stringify(Object.fromEntries(Object.entries(sample).filter(([k]) => /url|download|package/i.test(k))), null, 1) : "NONE in items[0..n]")
