import { describe, expect, test } from "bun:test"
import { spawn } from "node:child_process"
import { copyFileSync, cpSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs"
import { createServer } from "node:net"
import { tmpdir } from "node:os"
import { basename, dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

// wb-studio.mjs 的冒烟:真的起一个实例,打三个入口,确认它能监听并回话。
//
// 为什么不能只做单测:这个文件顶层直接 `boot()`,没有 import 入口,任何一处搬错
// (少一个 import、写错一个路径、初始化顺序变了)都只在**跑起来**的时候才炸。
// 快照测试保「对外面没变」,这条保「变了以后还能启动」。
//
// 隔离办法:把主文件与它的顶层依赖复制到临时目录再跑。
// 所有运行时路径都基于 `__dirname`(runtime/、workspace/、config),换目录即天然隔离,
// 不用为了可测性去改 24 处 `join(__dirname, "runtime", ...)`。
// engineMode 固定 "cli":ensureEngine 在 cli 模式下直接返回,不会去找 175MB 的引擎 exe。

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)))

/** 拿一个空闲端口:先让 OS 分配,立刻释放,再交给被测进程。 */
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

type Smoke = {
  base: string
  child: ReturnType<typeof spawn>
  out: () => string
  stop: () => Promise<void>
}

/** 在临时目录起一个实例,等到 /api/health 能回话(或超时抛错)。 */
async function startStudio(): Promise<Smoke> {
  const dir = mkdtempSync(join(tmpdir(), "wbstudio-smoke-"))
  let child: ReturnType<typeof spawn> | null = null
  let buf = ""
  const cleanupDir = () => {
    // 删目录只是收尾:它失败不许盖掉真正的启动错误
    try { rmSync(dir, { recursive: true, force: true }) } catch {}
  }
  const waitExit = (proc: ReturnType<typeof spawn>) => {
    // 已经退出就直接返回:否则 once("exit") 永远等不到(事件已经发过了),白等满 2 秒
    if (proc.exitCode !== null || proc.signalCode !== null) return Promise.resolve(true)
    return Promise.race([
      new Promise((r) => proc.once("exit", () => r(true))),
      new Promise((r) => setTimeout(() => r(false), 2000)),
    ])
  }

  // setup 也放进 try:mkdtemp 成功之后任何一步抛错,目录都要收掉。
  // 那时调用方还没拿到 stop,只能靠这里清(CodeRabbit PR#35 指出)。
  try {
    // 主文件 + 唯一一个顶层静态依赖;src/experts.ts 是动态 import,缺失只会 warn
    copyFileSync(join(ROOT, "wb-studio.mjs"), join(dir, "wb-studio.mjs"))
    copyFileSync(join(ROOT, "wb-agent-lib.mjs"), join(dir, "wb-agent-lib.mjs"))
    // 拆分后主文件开始 import src/studio/*:整目录复制(源码总共几十 KB),
    // 免得每搬一块就回来改一次这条测试。缺依赖时这条烟测会当场红——这正是它的职责。
    cpSync(join(ROOT, "src"), join(dir, "src"), { recursive: true })
    mkdirSync(join(dir, "workspace"), { recursive: true })

    const port = await freePort()
    writeFileSync(join(dir, "wb-studio.config.json"), JSON.stringify({
      port,
      engineMode: "cli",          // 不启引擎:ensureEngine 在 cli 模式直接返回
      engineCmd: [],
      openBrowser: false,
      openMode: "browser",
      workspace: "./workspace",
      timeoutSec: 120,
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
    let ready = false
    for (;;) {
      if (proc.exitCode !== null || proc.signalCode !== null) {
        throw new Error("实例启动即退出(exit=" + proc.exitCode + ", signal=" + proc.signalCode + "):\n" + buf.slice(-2000))
      }
      try {
        const r = await fetch(base + "/api/health", { signal: AbortSignal.timeout(1200) })
        if (r.ok) {
          // freePort 释放端口到 spawn 之间,端口可能被另一个 smoke 实例抢走。
          // 只有 workspace 指向本次临时目录,才算"这就是我起的那个"(CodeRabbit PR#35)。
          const h = await r.json() as Record<string, unknown>
          if (String(h.workspace || "").includes(basename(dir))) { ready = true; break }
        }
      } catch {}
      if (Date.now() > deadline) {
        throw new Error("25 秒内没等到本实例的 /api/health(端口可能被别的实例占了)。输出:\n" + buf.slice(-2000))
      }
      await new Promise((r) => setTimeout(r, 300))
    }
    if (!ready) throw new Error("readiness 循环意外退出")

    const stop = async () => {
      try { proc.kill("SIGTERM") } catch {}
      // 给 2 秒优雅退出,再强杀;不留下占端口的孤儿
      if (!(await waitExit(proc))) { try { proc.kill("SIGKILL") } catch {} }
      // 强杀后也要等它真退出:Windows 上进程还占着文件时 rm 会失败
      await waitExit(proc)
      cleanupDir()
    }

    return { base, child: proc, out: () => buf, stop }
  } catch (e) {
    // 还没返回 stop,调用方进不了自己的 try/finally:子进程与目录必须在这里收掉,
    // 否则 CI 上会留下占端口的孤儿,下一条用例的失败信息会被带偏。
    if (child) {
      try { child.kill("SIGKILL") } catch {}
      await waitExit(child)
    }
    cleanupDir()
    throw e
  }
}

describe("wb-studio 冒烟", () => {
  test("能启动并回答健康检查、首页、会话列表", async () => {
    const s = await startStudio()
    try {
      const health = await (await fetch(s.base + "/api/health")).json() as Record<string, unknown>
      expect(health, "health 缺 backend 字段").toHaveProperty("backend")
      expect(health, "health 缺 workspace 字段").toHaveProperty("workspace")
      expect(String(health.workspace), "workspace 应指向临时目录").toContain("wbstudio-smoke-")
      expect(health.backend, "cli 底座下 backend 不该是 zcode").not.toBe("zcode")

      const home = await fetch(s.base + "/")
      expect(home.status).toBe(200)
      expect(home.headers.get("content-type") || "").toContain("text/html")
      const html = await home.text()
      expect(html.length, "首页为空").toBeGreaterThan(1000)

      const sessions = await fetch(s.base + "/api/sessions")
      expect(sessions.status).toBe(200)
      const sj = await sessions.json() as Record<string, unknown>
      // 服务端回的是 { ok, current, items }。这里不用 `?? []` 兜底:
      // 回一个 `{}` 也能被兜成空数组,那这条回归就形同虚设(CodeRabbit PR#35 指出)。
      expect(sj, "会话列表响应缺 items 字段").toHaveProperty("items")
      expect(Array.isArray(sj.items), "items 应是数组").toBe(true)

      // 进程还活着 = 上面三次请求没有把服务打崩
      expect(s.child.exitCode).toBeNull()
    } finally {
      await s.stop()
    }
  }, 60000)

  test("未知路径返回 JSON 错误而不是崩溃", async () => {
    const s = await startStudio()
    try {
      const r = await fetch(s.base + "/api/definitely-not-a-route")
      expect(r.status, "未知 API 应回 404").toBe(404)
      // 服务仍然可用
      const again = await fetch(s.base + "/api/health")
      expect(again.status).toBe(200)
    } finally {
      await s.stop()
    }
  }, 60000)
})
