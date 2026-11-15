import { writeFileSync } from "node:fs"

const res = await fetch("https://market.project-neko.cn/api/v1/plugins?page=1&page_size=200", {
  headers: { "User-Agent": "Mozilla/5.0" },
})
const j = await res.json()
console.log("keys:", Object.keys(j))
const items = j.items || j
console.log("count:", items.length)
if (items.length) {
  console.log("item keys:", Object.keys(items[0]).join(", "))
  for (const it of items.slice(0, 5)) {
    console.log("-", it.slug || it.id, "|", (it.name || "").slice(0, 30), "| by", it.author_name, "| dl:", it.download_count ?? it.downloads ?? "?")
  }
}
writeFileSync(process.env.TEMP + "/market_list.json", JSON.stringify(j, null, 1), "utf8")
