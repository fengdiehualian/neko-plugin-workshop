// 把 python 规则用例挂进 bun test(报告指出 py 用例不被 CI 覆盖).
import { expect, test } from "bun:test"
const dec = new TextDecoder()
const r = Bun.spawnSync(["python", "-X", "utf8", "test/scan_rules.test.py"], { cwd: import.meta.dir + "/.." })
const out = dec.decode(r.stdout), err = dec.decode(r.stderr)
test("scan rules python 自检通过(python 缺失则跳过,不算失败)", () => {
  if (!out && /not found|No such file|无法将/i.test(err + out)) { console.warn("python absent, skipped"); return }
  console.log(out.trim())
  expect(out).toContain("PASS")
})
