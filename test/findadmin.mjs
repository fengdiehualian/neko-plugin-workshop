import { readFileSync } from "node:fs"

for (const f of [
  "C:/Users/Administrator/dev/opencode/packages/workbench/wb-agent.mjs",
  "C:/Users/Administrator/dev/opencode/packages/workbench/wb-studio.config.json",
]) {
  console.log("===" + f + "===")
  const t = readFileSync(f, "utf8")
  let pos = 0
  while (true) {
    pos = t.indexOf("Administrator", pos)
    if (pos < 0) break
    console.log("  ..." + t.slice(Math.max(0, pos - 70), pos + 80).replace(/\n/g, " | "))
    pos += 13
  }
}
