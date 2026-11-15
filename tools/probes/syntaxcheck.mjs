/**
 * syntaxcheck - 提取工坊页面主脚本并做语法检查,定位解析错误
 */
import { writeFileSync, readFileSync } from "node:fs"

const html = await (await fetch("http://127.0.0.1:5099/")).text()
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1])
console.log("script blocks:", scripts.length)
let i = 0
for (const src of scripts) {
  i++
  const f = process.env.TEMP + `\\wb_page_script_${i}.js`
  writeFileSync(f, src, "utf8")
  try {
    // bun 能解析就是语法 OK
    await import("data:text/javascript," + encodeURIComponent(src))
    console.log(`script #${i}: OK (${src.length} chars)`)
  } catch (e) {
    console.log(`script #${i}: SYNTAX/RUNTIME ERROR -> ${String(e?.message || e).slice(0, 300)}`)
  }
}
