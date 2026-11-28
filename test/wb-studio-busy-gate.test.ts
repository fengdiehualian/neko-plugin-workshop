import { describe, expect, test } from "bun:test"
import { spawn } from "node:child_process"
import { copyFileSync, cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { createServer as createTcpServer } from "node:net"
import { tmpdir } from "node:os"
import { basename, dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

// busy 429 门禁的端到端(评审补测:这两条新行为此前无任何自动化覆盖)。
//
// 任务进行中,「会连累在跑任务」的写操作必须被 429 拒下来,而不是真去执行:
//   · POST /api/update {action:"start"} → 429「任务进行中,完成后再更新」
//     (更新链会退出本进程,正在跑的任务会被直接杀掉)
//   · POST /api/model/switch → 429「任务进行中,完成后再切换」
//     (跨 API 切换会重启引擎、同 API 切换会重写引擎配置,都会连累在跑任务)
//   · POST /api/task(再来一个)→ 429「上一个任务还没完成」(同一 busy 家族的既有门禁,顺带钉住)
//
// 怎么让 busy 确定为 true:engineMode "cli" + engineCmd 指向一个会挂 ~60 秒的本机命令
// (cmd /c ping -n 60 127.0.0.1;{stdin} 占位符满足 cliEngineReady 的要求,任务文本走管道,
//  ping 不读 stdin 照常跑)。POST /api/task 发出后轮询 GET /api/health 的 busy 字段,
// 看到 busy=true 才打 429 断言——保证「任务真的在跑」,不拿竞态当通过。
//
// 起实例惯例照抄 wb-studio-model-switch.test.ts:临时目录 + 随机端口 + 预写 runtime/apis.json
// (loadApis 只在模块加载时读一次;有有效密钥才过 /api/task 的配置门),token 落在
// <临时目录>/runtime/studio-token.txt。收尾 POST /api/stop 让 busy 复位,别让 60s 的 ping 拖住实例。
// 不覆盖:idle 状态下 /api/update start 的真实下载链(会发网络请求,归 check-page/发版手测)。

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)))

/** 拿一个空闲端口:先让 OS 分配,立刻释放,再交给被测进程。 */
function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const srv = createTcpServer()
    srv.once("error", reject)
    srv.listen(0, "127.0.0.1", () => {
      const addr = srv.address()
      const port = typeof addr === "object" && addr ? addr.port : 0
      srv.close(() => resolve(port))
    })
  })
}

type Studio = {
  base: string
  dir: string
  token: string
  stop: () => Promise<void>
}

/** 预置一套有效 API(过 /api/task 的 hasValidKey 门);同 API 切模型不做连接测试,假地址即可。 */
const seedApis = () => ({
  enabledId: "api_a",
  items: [
    {
      id: "api_a", name: "主力号", baseURL: "https://api-a.test/v1", apiKey: "sk-aaa1111",
      modelID: "model-a1", models: ["model-a1", "model-a2"], protocol: "openai",
    },
  ],
})

/** 在临时目录起一个实例(CLI 底座=挂 60 秒的 ping),等到 /api/health 能回话。 */
async function startStudio(): Promise<Studio> {
  const dir = mkdtempSync(join(tmpdir(), "wbstudio-busy-"))
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
    // apis.json 必须先于进程启动落盘(loadApis 只在模块加载时读)
    mkdirSync(join(dir, "runtime"), { recursive: true })
    writeFileSync(join(dir, "runtime", "apis.json"), JSON.stringify(seedApis(), null, 2) + "\n", "utf8")

    const port = await freePort()
    writeFileSync(join(dir, "wb-studio.config.json"), JSON.stringify({
      port,
      engineMode: "cli",
      // 会挂 ~60 秒的底座命令:任务文本经 {stdin} 管道进去(ping 不读,照常跑),
      // busy 在任务结束前保持 true——正好是 429 门禁要的「任务进行中」
      engineCmd: ["cmd", "/c", "ping -n 60 127.0.0.1", "{stdin}"],
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

    const token = readFileSync(join(dir, "runtime", "studio-token.txt"), "utf8").trim()
    if (!token) throw new Error("studio-token.txt 为空")

    const stop = async () => {
      try { proc.kill("SIGTERM") } catch {}
      if (!(await waitExit(proc))) { try { proc.kill("SIGKILL") } catch {} }
      await waitExit(proc)
      cleanupDir()
    }

    return { base, dir, token, stop }
  } catch (e) {
    if (child) {
      try { child.kill("SIGKILL") } catch {}
      await waitExit(child)
    }
    cleanupDir()
    throw e
  }
}

/** 带 token 的 POST。 */
const post = (s: Studio, path: string, body: unknown): Promise<Response> =>
  fetch(s.base + path, {
    method: "POST",
    headers: { "content-type": "application/json", "x-wb-token": s.token },
    body: JSON.stringify(body),
  })

const healthBusy = async (s: Studio): Promise<boolean> => {
  const h = await (await fetch(s.base + "/api/health")).json() as Record<string, unknown>
  return Boolean(h.busy)
}

describe("wb-studio busy 门禁(任务进行中,会连累任务的写操作一律 429)", () => {
  test("任务中:update start / model switch / 再发任务都 429;停止后 busy 复位、切换恢复", async () => {
    const s = await startStudio()
    try {
      // 发一个会跑 ~60s 的 CLI 任务;它的 HTTP 应答在任务结束时才回来,这里不等待
      const taskP = post(s, "/api/task", { text: "busy 门禁测试:做一个喝水提醒插件", mode: "build" }).catch(() => null)

      // 以 /api/health 的 busy 字段为准,等任务真的进入「进行中」
      const t0 = Date.now()
      while (!(await healthBusy(s))) {
        if (Date.now() - t0 > 15000) throw new Error("15 秒内任务没进入 busy 状态(输出见实例)")
        await new Promise((r) => setTimeout(r, 200))
      }

      // 1) 更新启动:busy → 429(更新链会退出本进程,在跑任务会被杀)
      const upd = await post(s, "/api/update", { action: "start" })
      expect(upd.status, "任务中启动更新应 429").toBe(429)
      const uj = await upd.json() as Record<string, unknown>
      expect(uj.ok).toBe(false)
      expect(String(uj.error), "错误要说人话").toContain("任务进行中")
      expect(String(uj.error)).toContain("更新")

      // 2) 模型切换:busy → 429(切换会重写引擎配置/重启引擎,连累在跑任务)
      const sw = await post(s, "/api/model/switch", { apiId: "api_a", model: "model-a2" })
      expect(sw.status, "任务中切模型应 429").toBe(429)
      const sj = await sw.json() as Record<string, unknown>
      expect(sj.ok).toBe(false)
      expect(String(sj.error)).toContain("任务进行中")
      expect(String(sj.error)).toContain("切换")
      // 429 在任何校验/落盘之前返回:apis.json 原封不动
      const onDisk = JSON.parse(readFileSync(join(s.dir, "runtime", "apis.json"), "utf8")) as { enabledId: string; items: Array<{ modelID: string }> }
      expect(onDisk.enabledId).toBe("api_a")
      expect(onDisk.items[0].modelID, "429 不该动 modelID").toBe("model-a1")

      // 3) 同一 busy 家族的既有门禁:任务中再发任务 → 429
      const again = await post(s, "/api/task", { text: "第二个任务不该被接受", mode: "build" })
      expect(again.status, "任务中再发任务应 429").toBe(429)
      const aj = await again.json() as Record<string, unknown>
      expect(aj.ok).toBe(false)

      // 三次 429 都没把在跑任务弄死:busy 仍为 true
      expect(await healthBusy(s), "429 探测不该影响在跑任务").toBe(true)

      // 停止任务 → busy 复位(别让 60s 的 ping 拖到测试结束)
      const stop = await post(s, "/api/stop", {})
      expect(stop.status).toBe(200)
      const t1 = Date.now()
      while (await healthBusy(s)) {
        if (Date.now() - t1 > 20000) throw new Error("停止后 20 秒内 busy 没复位")
        await new Promise((r) => setTimeout(r, 300))
      }
      // 被停止的任务,HTTP 应答最终要回来(允许 stopped 形态;15s 兜底防悬挂)
      await Promise.race([taskP, new Promise((r) => setTimeout(r, 15000))])

      // 空闲后:同 API 切模型恢复 200——证明 429 是 busy 门禁,不是永久封锁
      const sw2 = await post(s, "/api/model/switch", { apiId: "api_a", model: "model-a2" })
      expect(sw2.status, "空闲后切模型应恢复 200").toBe(200)
      const sj2 = await sw2.json() as Record<string, unknown>
      expect(sj2.ok).toBe(true)
      expect(sj2.model).toBe("model-a2")
    } finally {
      await s.stop()
    }
  }, 90000)
})
