import { readFileSync } from "node:fs"

const t = readFileSync("C:/Users/Administrator/dev/opencode/packages/opencode/src/provider/provider.ts", "utf8")
// 找自定义 provider 的 npm 解析逻辑
const idx = t.indexOf("BUNDLED_PROVIDERS")
const tail = t.slice(idx, idx + 12000)
const lines = tail.split("\n")
let show = false
let count = 0
for (const l of lines) {
  if (l.includes("npm") || l.includes("bundle") || l.includes("loader") || l.includes("function")) {
    console.log(l.trim().slice(0, 120))
    count++
    if (count > 25) break
  }
}
