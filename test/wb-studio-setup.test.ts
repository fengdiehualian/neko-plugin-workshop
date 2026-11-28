import { describe, expect, test } from "bun:test"
import { spawn } from "node:child_process"
import { copyFileSync, cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { createServer } from "node:net"
import { tmpdir } from "node:os"
import { basename, dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

// 首次配置页(SETUP_PAGE)的端到端:空临时目录起实例,按真实 HTTP 走完整首配链路。
//
// 覆盖:
//   GET /         → 必须是引导页(空目录没有 runtime\apis.json 与引擎配置,hasValidKey()=false;
//                   engineCmd 留空也让 cliEngineReady()=false,不会短路成主页)
//   id 双保险     → 引导页 JS 引用的 id 在 HTML 全有定义(与 tools/static_check.py 的
//                   $('x') 检查同一套口径;#probeTip 缺失那类「点按钮 null.style 抛错」
//                   的回归要在测试层再被抓住一次)
//   POST /api/setup → 假地址(127.0.0.1:1,连接立刻被拒)属软失败,配置照样落盘
//   GET /         → 配置完成后变主页
//
// 不覆盖:#probeBtn 的真网络点击(要真浏览器,由 tools/check-page.mjs 的引导页场景负责);
// 这里只保证点击所依赖的 DOM(id 全有定义)真实存在。
// 起实例惯例照抄 wb-studio-upload.test.ts:临时目录 + 随机端口 + engineMode "cli" 不拉引擎,
// token 落在 <临时目录>/runtime/studio-token.txt。

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

type Setup = {
  base: string
  dir: string
  token: string
  apisPath: string
  opencodePath: string
  child: ReturnType<typeof spawn>
  stop: () => Promise<void>
}

/** 在空临时目录起一个实例,等到 /api/health 能回话(或超时抛错)。 */
async function startStudio(): Promise<Setup> {
  const dir = mkdtempSync(join(tmpdir(), "wbstudio-setup-"))
  let child: ReturnType<typeof spawn> | null = null
  let buf = ""
  const cleanupDir = () => {
    try { rmSync(dir, { recursive: true, force: true }) } catch {}
  }
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

    const port = await freePort()
    writeFileSync(join(dir, "wb-studio.config.json"), JSON.stringify({
      port,
      engineMode: "cli",          // 不启本地引擎;engineCmd 留空 → cliEngineReady()=false,首页走引导页分支
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
          const h = await r.json() as Record<string, unknown>
          if (String(h.workspace || "").includes(basename(dir))) { ready = true; break }
        }
      } catch {}
      if (Date.now() > deadline) {
        throw new Error("25 秒内没等到本实例的 /api/health。输出:\n" + buf.slice(-2000))
      }
      await new Promise((r) => setTimeout(r, 300))
    }
    if (!ready) throw new Error("readiness 循环意外退出")

    // /api/health 能回话 = srv.listen 已触发 = token 已落盘(publishLocalToken 的时序约定)
    const token = readFileSync(join(dir, "runtime", "studio-token.txt"), "utf8").trim()
    if (!token) throw new Error("studio-token.txt 为空")

    const stop = async () => {
      try { proc.kill("SIGTERM") } catch {}
      if (!(await waitExit(proc))) { try { proc.kill("SIGKILL") } catch {} }
      await waitExit(proc)
      cleanupDir()
    }

    return {
      base,
      dir,
      token,
      apisPath: join(dir, "runtime", "apis.json"),
      opencodePath: join(dir, "runtime", "config", "opencode", "opencode.json"),
      child: proc,
      stop,
    }
  } catch (e) {
    if (child) {
      try { child.kill("SIGKILL") } catch {}
      await waitExit(child)
    }
    cleanupDir()
    throw e
  }
}

/** 与 tools/static_check.py 同一套口径:页面 JS 引用 id 的三种写法。 */
function referencedIds(html: string): { ids: Set<string>; refs: string[] } {
  const ids = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map((m) => m[1]))
  let js = ""
  for (const m of html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)) js += "\n" + m[1]
  const refs: string[] = []
  for (const m of js.matchAll(/getElementById\(\s*['"]([^'"]+)['"]/g)) refs.push(m[1])
  // 引导页自制 $ 简写(const $=id=>document.getElementById(id)):$('x') 不带 # 也是 id 引用
  for (const m of js.matchAll(/\$\(\s*['"]([A-Za-z0-9_-]+)['"]/g)) refs.push(m[1])
  for (const m of js.matchAll(/querySelector(?:All)?\(\s*['"]#([A-Za-z0-9_-]+)['"]/g)) refs.push(m[1])
  return { ids, refs: [...new Set(refs)] }
}

describe("wb-studio 首次配置页", () => {
  test("空目录启动 → 引导页(id 引用全有定义)→ /api/setup 落盘 → 首页变主页", async () => {
    const s = await startStudio()
    try {
      // 1) 空目录首启:GET / 必须是引导页,不是主页
      const home = await fetch(s.base + "/")
      expect(home.status).toBe(200)
      expect(home.headers.get("content-type") || "").toContain("text/html")
      const html = await home.text()
      expect(html, "空目录应返回首次配置页").toContain("首次配置")
      expect(html, "引导页应有服务商预设区").toContain('id="presets"')
      expect(html, "空目录不该直接进主页").not.toContain('id="chat"')
      // 配置还没写,apis.json 不应存在
      expect(existsSync(s.apisPath), "引导页阶段不该有 apis.json").toBe(false)

      // 2) 双保险:引导页 JS 引用的 id 在 HTML 全有定义
      //    (#probeTip 缺失那类问题,static_check 抓静态面,这里抓「真跑起来的页面」)
      const { ids, refs } = referencedIds(html)
      expect(refs.length, "引导页 JS 应至少引用 10 个 id(没解析到 JS 说明提取坏了)").toBeGreaterThanOrEqual(10)
      const missing = refs.filter((r) => !ids.has(r))
      expect(missing, "引导页 JS 引用了 HTML 没有的 id(点按钮会 null 抛错)").toEqual([])

      // 3) POST /api/setup:地址用 127.0.0.1:1(连接立刻被拒)
      //    软失败不拦保存(isHardTestFail 只拦 401/403),配置照样落盘
      const r = await fetch(s.base + "/api/setup", {
        method: "POST",
        headers: { "content-type": "application/json", "x-wb-token": s.token },
        body: JSON.stringify({
          baseURL: "http://127.0.0.1:1/v1",
          apiKey: "sk-setup-e2e-000000",
          model: "setup-e2e-model",
          protocol: "openai",
        }),
      })
      expect(r.status, "假地址属软失败,/api/setup 应放行保存").toBe(200)
      const j = await r.json() as Record<string, unknown>
      expect(j.ok, "保存应回 ok:true").toBe(true)
      expect(String(j.warning || ""), "连接失败应带回 warning 提示").toContain("连接测试提示")

      // 4) 落盘核对:apis.json 有且只有这一套并被启用;引擎配置已写出(writeProvider)
      const apis = JSON.parse(readFileSync(s.apisPath, "utf8")) as {
        enabledId?: string
        items?: Array<{ id?: string, baseURL?: string, modelID?: string, protocol?: string }>
      }
      expect(apis.items?.length, "apis.json 应存入第一套 API").toBe(1)
      expect(apis.enabledId, "这套 API 应被启用").toBe(apis.items?.[0]?.id)
      expect(apis.items?.[0]?.baseURL).toBe("http://127.0.0.1:1/v1")
      expect(apis.items?.[0]?.modelID).toBe("setup-e2e-model")
      expect(existsSync(s.opencodePath), "保存后引擎配置 opencode.json 应被写出").toBe(true)

      // 5) 配置完成:GET / 变主页
      const home2 = await fetch(s.base + "/")
      expect(home2.status).toBe(200)
      const html2 = await home2.text()
      expect(html2, "配置完成后首页应变回主页").toContain('id="chat"')
      expect(html2, "引导页不该再出现").not.toContain('id="presets"')

      // 6) 全程服务没崩
      expect(s.child.exitCode).toBeNull()
    } finally {
      await s.stop()
    }
  }, 90000)
})
