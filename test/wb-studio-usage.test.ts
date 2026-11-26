import { describe, expect, test } from "bun:test"
import { spawn } from "node:child_process"
import { copyFileSync, cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { createServer } from "node:net"
import { tmpdir } from "node:os"
import { basename, dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

// /api/usage/stats 的端到端:起临时实例(惯例照抄 wb-studio-upload.test.ts),
// 启动前预埋 runtime/sessions.json(含已归档会话/多日期/多模型/无 model 的历史消息),
// 断言聚合:总账、按日期(本地时区)、按模型(无 model 归「未记录」)、按对话(含已归档+排序)。
// model 归因的「任务开始时捕获」发生在 /api/task 链路,端到端要驱动真实引擎,不在本测试范围
// (由 /api/usage/stats 对 message.model 的消费方契约覆盖:有就归,没有就归「未记录」)。

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)))

function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const srv = createServer()
    srv.once("error", reject)
    srv.listen(0, "127.0.0.1", () => {
      const addr = srv.address()
      const port = typeof addr === "object" && addr ? addr.port : 0
      srv.close(() => resolve(port))
    })
  })
}

type Studio = { base: string; dir: string; stop: () => Promise<void> }

const pad2 = (n: number) => (n < 10 ? "0" : "") + n
const dayKeyOf = (ts: number) => {
  const d = new Date(ts)
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`
}

/** 起实例;seed 传入时在启动前往 runtime/ 写入 sessions.json(数据全部可控)。 */
async function startStudio(seed?: unknown): Promise<Studio> {
  const dir = mkdtempSync(join(tmpdir(), "wbstudio-usage-"))
  let child: ReturnType<typeof spawn> | null = null
  let buf = ""
  const cleanupDir = () => { try { rmSync(dir, { recursive: true, force: true }) } catch {} }
  const waitExit = (proc: ReturnType<typeof spawn>) => {
    if (proc.exitCode !== null || proc.signalCode !== null) return Promise.resolve(true)
    return Promise.race([
      new Promise((r) => proc.once("exit", () => r(true))),
      new Promise((r) => setTimeout(() => r(false), 2000)),
    ])
  }
  try {
    copyFileSync(join(ROOT, "wb-studio.mjs"), join(dir, "wb-studio.mjs"))
    copyFileSync(join(ROOT, "wb-agent-lib.mjs"), join(dir, "wb-agent-lib.mjs"))
    cpSync(join(ROOT, "src"), join(dir, "src"), { recursive: true })
    mkdirSync(join(dir, "workspace"), { recursive: true })
    if (seed !== undefined) {
      mkdirSync(join(dir, "runtime"), { recursive: true })
      writeFileSync(join(dir, "runtime", "sessions.json"), JSON.stringify(seed), "utf8")
    }

    const port = await freePort()
    writeFileSync(join(dir, "wb-studio.config.json"), JSON.stringify({
      port, engineMode: "cli", engineCmd: [], openBrowser: false, openMode: "browser",
      workspace: "./workspace", timeoutSec: 120,
    }, null, 2) + "\n", "utf8")

    const proc = spawn(process.execPath, [join(dir, "wb-studio.mjs")], {
      cwd: dir,
      env: { ...process.env, WB_STUDIO_NOWINDOW: "1" },
      stdio: ["ignore", "pipe", "pipe"],
    })
    child = proc
    proc.stdout?.on("data", (b) => { buf += String(b) })
    proc.stderr?.on("data", (b) => { buf += String(b) })

    const base = `http://127.0.0.1:${port}`
    const deadline = Date.now() + 25000
    for (;;) {
      if (proc.exitCode !== null || proc.signalCode !== null) {
        throw new Error("实例启动即退出:\n" + buf.slice(-2000))
      }
      try {
        const r = await fetch(base + "/api/health", { signal: AbortSignal.timeout(1200) })
        if (r.ok) break
      } catch {}
      if (Date.now() > deadline) throw new Error("25 秒内没等到 /api/health:\n" + buf.slice(-2000))
      await new Promise((r) => setTimeout(r, 300))
    }
    const stop = async () => {
      try { proc.kill("SIGTERM") } catch {}
      if (!(await waitExit(proc))) { try { proc.kill("SIGKILL") } catch {} }
      await waitExit(proc)
      cleanupDir()
    }
    return { base, dir, stop }
  } catch (e) {
    if (child) { try { child.kill("SIGKILL") } catch {} ; await waitExit(child) }
    cleanupDir()
    throw e
  }
}

const stats = (s: Studio) => fetch(s.base + "/api/usage/stats", { signal: AbortSignal.timeout(5000) })

describe("wb-studio /api/usage/stats", () => {
  test("聚合预埋数据:总账/按日期/按模型(含未记录)/按对话(含已归档,降序)", async () => {
    const day2 = Date.now() - 2 * 86400000
    const day1 = Date.now() - 1 * 86400000
    const seed = {
      current: "s1",
      items: [
        {
          id: "s1", title: "会话一", createdAt: day2, updatedAt: day1,
          messages: [
            { role: "user", content: "hi", at: day2 },
            { role: "assistant", content: "a1", at: day2, usage: { input: 100, output: 50, reasoning: 0 }, model: "glm-5.3-flash" },
            { role: "assistant", content: "a2", at: day2, usage: { input: 10, output: 5 } },
            { role: "assistant", content: "无用量消息", at: day1 },
          ],
        },
        {
          id: "s2", title: "会话二(已归档)", createdAt: day1, updatedAt: day1, archived: true,
          messages: [
            { role: "assistant", content: "b1", at: day1, usage: { input: 200, output: 80, reasoning: 20 }, model: "deepseek-v4.1" },
          ],
        },
      ],
    }
    const s = await startStudio(seed)
    try {
      const r = await stats(s)
      expect(r.status).toBe(200)
      const j = await r.json() as Record<string, any>
      expect(j.ok).toBe(true)

      const t = j.total
      expect(t.messages).toBe(3)
      expect(t.input).toBe(310)
      expect(t.output).toBe(135)
      expect(t.reasoning).toBe(20)
      expect(t.tokens).toBe(465)

      const byDate = j.byDate as { day: string, tokens: number }[]
      expect(byDate.length).toBe(2)
      expect(byDate[0].day).toBe(dayKeyOf(day2))
      expect(byDate[0].tokens).toBe(165)
      expect(byDate[1].day).toBe(dayKeyOf(day1))
      expect(byDate[1].tokens).toBe(300)

      const byModel = j.byModel as { model: string, tokens: number }[]
      expect(byModel.map((x) => x.model).sort()).toEqual(["deepseek-v4.1", "glm-5.3-flash", "未记录"])
      expect(byModel.find((x) => x.model === "未记录")?.tokens).toBe(15)
      expect(byModel.find((x) => x.model === "deepseek-v4.1")?.tokens).toBe(300)

      const byConv = j.byConversation as { id: string, tokens: number, archived: boolean }[]
      expect(byConv.length).toBe(2)
      expect(byConv[0].id).toBe("s2")
      expect(byConv[0].tokens).toBe(300)
      expect(byConv[0].archived).toBe(true)
      expect(byConv[1].id).toBe("s1")
      expect(byConv[1].tokens).toBe(165)
      expect(byConv[1].archived).toBe(false)
    } finally {
      await s.stop()
    }
  })

  test("空库:total 全零,各分组空数组,仍 ok:true", async () => {
    const s = await startStudio()
    try {
      const r = await stats(s)
      expect(r.status).toBe(200)
      const j = await r.json() as Record<string, any>
      expect(j.ok).toBe(true)
      expect(j.total.tokens).toBe(0)
      expect(j.total.messages).toBe(0)
      expect((j.byDate as unknown[]).length).toBe(0)
      expect((j.byModel as unknown[]).length).toBe(0)
      expect((j.byConversation as unknown[]).length).toBe(0)
    } finally {
      await s.stop()
    }
  })
})
