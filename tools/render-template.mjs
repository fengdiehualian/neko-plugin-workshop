#!/usr/bin/env bun
// 把 packs/neko-plugin/templates/<tpl> 渲染到目标目录,用于一致性检查与本地预览
import { renderTemplate } from "../src/index.ts"
import { mkdir, writeFile } from "node:fs/promises"
import { join, dirname } from "node:path"

const tpl = process.argv[2] || "hello_world"
const out = process.argv[3] || join(process.cwd(), "_scaffold", tpl)
const id = process.argv[4] || tpl
const cls = id.split(/[_-]/).map((s) => s.charAt(0).toUpperCase() + s.slice(1)).join("")

const files = await renderTemplate("neko-plugin", tpl, {
  PLUGIN_ID: id,
  PLUGIN_NAME: `示例插件 ${id}`,
  PLUGIN_AUTHOR: "conformance",
  PLUGIN_DESC: "一致性检查用样例插件",
  CLASS_NAME: cls,
})
for (const [rel, text] of Object.entries(files)) {
  const p = join(out, rel)
  await mkdir(dirname(p), { recursive: true })
  await writeFile(p, text, "utf8")
}
console.log(`rendered ${Object.keys(files).length} files -> ${out}`)
