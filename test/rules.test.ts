// 把 python 规则用例挂进 bun test(报告指出 py 用例不被 CI 覆盖)。
// Bun.spawnSync 在解释器不存在时抛 ENOENT,而不是返回错误对象,所以整段必须包起来:
// 否则 python 缺失的机器上 bun test 直接红,下面那句"缺失则跳过"永远走不到。
import { expect, test } from "bun:test"

let out = "", err = "", spawnError = ""
try {
  const r = Bun.spawnSync(["python", "-X", "utf8", "test/scan_rules.test.py"],
                          { cwd: import.meta.dir + "/.." })
  const dec = new TextDecoder()
  out = dec.decode(r.stdout); err = dec.decode(r.stderr)
} catch (e: any) {
  spawnError = String(e?.message ?? e)
}

test("scan rules python 自检通过(python 缺失则跳过,不算失败)", () => {
  // 只有"解释器不存在"才算跳过。其它启动失败、以及启动成功但没有任何输出的情况,
  // 一律算失败——否则 python 用例没跑,bun test 仍是绿的。
  if (spawnError !== "") {
    if (/ENOENT|not found|No such file|无法将/i.test(spawnError)) {
      console.warn("python absent, skipped:", spawnError.slice(0, 120)); return
    }
    throw new Error("python 启动失败但不是缺失:" + spawnError)
  }
  console.log(out.trim())
  expect(out).toContain("PASS")
})
