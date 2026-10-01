import { describe, expect, test } from "bun:test"
import { mkdtempSync, mkdirSync, writeFileSync, utimesSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
// @ts-expect-error js 模块无类型声明
import { scanModifiedFiles, driveAgentCli } from "../wb-agent-lib.mjs"

describe("scanModifiedFiles(Build 模式任务收尾显示已修改文件)", () => {
  test("只收任务开始后新增/改动的文件;排除产物、缓存目录;含子目录", () => {
    const dir = mkdtempSync(join(tmpdir(), "wbmod_"))
    try {
      const old = new Date("2020-01-01T00:00:00Z")
      writeFileSync(join(dir, "old.txt"), "x")
      utimesSync(join(dir, "old.txt"), old, old)
      mkdirSync(join(dir, "sub"))
      writeFileSync(join(dir, "sub", "old.py"), "x")
      utimesSync(join(dir, "sub", "old.py"), old, old)
      // 噪声目录 / 产物 / 日志:应排除
      mkdirSync(join(dir, "__pycache__"))
      writeFileSync(join(dir, "__pycache__", "junk.pyc"), "x")
      mkdirSync(join(dir, ".opencode"))
      writeFileSync(join(dir, ".opencode", "skill.md"), "x")
      writeFileSync(join(dir, "x.neko-plugin"), "x")
      writeFileSync(join(dir, "y.log"), "x")

      const since = Date.now()
      writeFileSync(join(dir, "new.md"), "x")
      writeFileSync(join(dir, "sub", "edited.py"), "x")
      writeFileSync(join(dir, "old.txt"), "changed") // 修改旧文件

      const got = scanModifiedFiles(dir, since)
      expect(got.length).toBe(3)
      expect(new Set(got)).toEqual(new Set(["old.txt", "new.md", "sub/edited.py"]))
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  test("driveAgentCli 端到端:任务真实写出的文件进 modifiedFiles", async () => {
    const dir = mkdtempSync(join(tmpdir(), "wbcli_ws_"))
    const scriptDir = mkdtempSync(join(tmpdir(), "wbcli_s_"))
    try {
      const script = join(scriptDir, "task.js")
      writeFileSync(script, "require('fs').writeFileSync('changed.txt','x');console.log('done')")
      const res = await driveAgentCli({
        text: "demo",
        dir,
        cmd: [process.execPath, script, "{text}"],
        timeoutSec: 30,
      })
      expect(res.ok).toBe(true)
      expect(res.modifiedFiles).toContain("changed.txt")
      expect(res.artifacts).toEqual([])
    } finally {
      rmSync(dir, { recursive: true, force: true })
      rmSync(scriptDir, { recursive: true, force: true })
    }
  }, 30_000)
})
