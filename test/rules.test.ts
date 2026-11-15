// 把 python 规则用例挂进 bun test(报告指出 py 用例不被 CI 覆盖)。
// Bun.spawnSync 在解释器不存在时抛 ENOENT,而不是返回错误对象,所以整段必须包起来:
// 否则 python 缺失的机器上 bun test 直接红,下面那句"缺失则跳过"永远走不到。
import { expect, test } from "bun:test"

let out = "", err = "", spawnError = "", code: number = -1, ran = false
// 两个解释器名都试：只调 python 的机器上（Linux 常见只有 python3）会走「跳过仍绿」。
for (const exe of ["python", "python3"]) {
  try {
    const r = Bun.spawnSync([exe, "-X", "utf8", "test/scan_rules.test.py"],
                            { cwd: import.meta.dir + "/.." })
    const dec = new TextDecoder()
    out = dec.decode(r.stdout); err = dec.decode(r.stderr); code = r.exitCode; ran = true
    break
  } catch (e: any) {
    spawnError = String(e?.message ?? e)
  }
}

test("scan rules python 自检通过(python 缺失则跳过,不算失败)", () => {
  // 只有"解释器不存在"才算跳过。其它启动失败、以及启动成功但没有任何输出的情况,
  // 一律算失败——否则 python 用例没跑,bun test 仍是绿的。
  if (!ran) {
    if (/ENOENT|not found|No such file|无法将/i.test(spawnError)) {
      console.warn("python/python3 both absent, skipped:", spawnError.slice(0, 120)); return
    }
    throw new Error("python 启动失败但不是缺失:" + spawnError)
  }
  console.log(out.trim())
  // 退出码优先：python 用例自己会 exit 1，只看输出里有没有 PASS 会漏掉中途失败。
  if (code !== 0) { throw new Error("scan_rules.test.py 退出码 " + code + " stderr:" + err.slice(0, 200)) }
  expect(out).toContain("PASS")
})
