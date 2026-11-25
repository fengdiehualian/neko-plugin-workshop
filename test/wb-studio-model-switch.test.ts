import { describe, expect, test } from "bun:test"
import { spawn } from "node:child_process"
import { createServer as createTcpServer } from "node:net"
import { createServer as createHttpServer } from "node:http"
import { copyFileSync, cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { basename, dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

// POST /api/model/switch 的端到端:起真实实例(临时 workspace + 随机端口),按真实 HTTP 打接口。
// 起实例的惯例照抄同目录 wb-studio-upload.test.ts:主文件 + wb-agent-lib.mjs + src/ 复制进临时目录,
// engineMode 固定 "cli" 不拉引擎;token 等端口占住后落在 <临时目录>/runtime/studio-token.txt。
//
// 与 upload 测试的差别:
//  · 多 API 状态(apis.json)必须在实例启动前落好盘(loadApis 只在模块加载时读一次),
//    所以 startStudio 先写 runtime/apis.json 再 spawn;
//  · 跨 API 切换会走连接测试(testProvider 12s 硬拦 401/403)。为了让它确定性、不碰外网 DNS,
//    api_b 的 baseURL 指向测试自起的本地假网关(statusServer):回 500 = 软失败(放行+警告),
//    回 401 = 硬拦(未切换)。同 API 分支不发请求,api_a 保持假地址即可。

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

/** 本地假网关:对一切请求回固定状态码 + JSON 错误体(连接测试的确定性靶子)。 */
function statusServer(status: number): Promise<{ url: string; close: () => Promise<void> }> {
  return new Promise((resolve) => {
    const srv = createHttpServer((req, res) => {
      res.writeHead(status, { "content-type": "application/json" })
      res.end(JSON.stringify({ error: { message: "stub gateway says " + status } }))
    })
    srv.listen(0, "127.0.0.1", () => {
      const addr = srv.address()
      const port = typeof addr === "object" && addr ? addr.port : 0
      resolve({
        url: `http://127.0.0.1:${port}/v1`,
        close: () => new Promise<void>((r) => srv.close(() => r())),
      })
    })
  })
}

type Studio = {
  base: string
  dir: string
  token: string
  apisJson: () => unknown
  engineCfg: () => unknown
  stop: () => Promise<void>
}

/** 预置两套 API(主力=api_a 两个模型,启用中;备用=api_b 两个模型,baseURL 可指向假网关)。 */
const seed = (apiBBase = "https://api-b.test/v1") => ({
  enabledId: "api_a",
  items: [
    {
      id: "api_a", name: "主力号", baseURL: "https://api-a.test/v1", apiKey: "sk-aaa1111",
      modelID: "model-a1", models: ["model-a1", "model-a2"], protocol: "openai",
    },
    {
      id: "api_b", name: "备用号", baseURL: apiBBase, apiKey: "sk-bbb2222",
      modelID: "model-b1", models: ["model-b1", "model-b2"], protocol: "openai",
    },
  ],
})

/** 在临时目录起一个实例,等到 /api/health 能回话(或超时抛错)。 */
async function startStudio(seedApis: ReturnType<typeof seed> = seed()): Promise<Studio> {
  const dir = mkdtempSync(join(tmpdir(), "wbstudio-modelsw-"))
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
    // 关键:apis.json 必须先于进程启动落盘(loadApis 只在模块加载时读)
    mkdirSync(join(dir, "runtime"), { recursive: true })
    writeFileSync(join(dir, "runtime", "apis.json"), JSON.stringify(seedApis, null, 2) + "\n", "utf8")

    const port = await freePort()
    writeFileSync(join(dir, "wb-studio.config.json"), JSON.stringify({
      port,
      engineMode: "cli",
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
      apisJson: () => JSON.parse(readFileSync(join(dir, "runtime", "apis.json"), "utf8")),
      engineCfg: () => JSON.parse(readFileSync(join(dir, "runtime", "config", "opencode", "opencode.json"), "utf8")),
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

/** 带 token 的 POST /api/model/switch。 */
function switchModel(s: Studio, apiId: string, model: string, withToken = true): Promise<Response> {
  const headers: Record<string, string> = { "content-type": "application/json" }
  if (withToken) headers["x-wb-token"] = s.token
  return fetch(s.base + "/api/model/switch", {
    method: "POST",
    headers,
    body: JSON.stringify({ apiId, model }),
  })
}

/** GET /api/apis(读接口不走 token 门禁)。 */
async function getApis(s: Studio): Promise<Record<string, unknown>> {
  return await (await fetch(s.base + "/api/apis")).json() as Record<string, unknown>
}

function findItem(j: Record<string, unknown>, id: string): Record<string, unknown> {
  const items = j.items as Array<Record<string, unknown>>
  return items.find((x) => x.id === id) as Record<string, unknown>
}

describe("wb-studio /api/model/switch", () => {
  test("同 API 切模型 → modelID 更新、enabledId 不变、引擎 provider/config.model 跟着变;再切同值 → 幂等应答不动状态", async () => {
    const s = await startStudio()
    try {
      const r = await switchModel(s, "api_a", "model-a2")
      expect(r.status, "同 API 切换应成功").toBe(200)
      const j = await r.json() as Record<string, unknown>
      expect(j.ok, "应回 ok:true").toBe(true)
      expect(j.model, "响应应带新模型").toBe("model-a2")
      expect(String(j.apiName), "响应应带 API 备注名").toBe("主力号")
      expect(typeof j.warning, "响应应带 warning 字段(字符串)").toBe("string")

      // GET /api/apis:enabledId 不变,modelID 已更新
      const g = await getApis(s)
      expect(g.enabledId, "同 API 切换不该换 enabledId").toBe("api_a")
      expect(findItem(g, "api_a").modelID, "api_a 的 modelID 应改为 model-a2").toBe("model-a2")

      // 落盘的 apis.json 同样是新值(saveApis 生效)
      const onDisk = s.apisJson() as { enabledId: string; items: Array<{ id: string; modelID: string }> }
      expect(onDisk.enabledId).toBe("api_a")
      expect(onDisk.items.find((x) => x.id === "api_a")!.modelID).toBe("model-a2")

      // 引擎 provider 配置被重写:config.model=custom/<modelID>(writeProvider 落盘)
      const cfg = s.engineCfg() as { model: string; provider: { custom: { options: { baseURL?: string; apiKey?: string } } } }
      expect(cfg.model, "引擎配置的 model 应为 custom/model-a2").toBe("custom/model-a2")
      expect(cfg.provider.custom.options.baseURL).toBe("https://api-a.test/v1")
      expect(cfg.provider.custom.options.apiKey).toBe("sk-aaa1111")

      // 幂等:切"使用中"的当前模型 → ok 但不动状态、不给重启提示
      const again = await switchModel(s, "api_a", "model-a2")
      expect(again.status, "幂等切换应 200").toBe(200)
      const j2 = await again.json() as Record<string, unknown>
      expect(j2.ok).toBe(true)
      expect(j2.model).toBe("model-a2")
      expect(j2.warning, "幂等切换不该再提示重启/生效").toBe("")
      const g2 = await getApis(s)
      expect(g2.enabledId, "幂等切换不该动 enabledId").toBe("api_a")
      expect(findItem(g2, "api_a").modelID, "幂等切换不该动 modelID").toBe("model-a2")
    } finally {
      await s.stop()
    }
  }, 60000)

  test("跨 API 切(软失败网关 500)→ enabledId 变、provider 写入正确、warning 同时带软警告与引擎重启", async () => {
    const gw = await statusServer(500)
    const s = await startStudio(seed(gw.url))
    try {
      const r = await switchModel(s, "api_b", "model-b2")
      expect(r.status, "跨 API 切换(软失败)应成功").toBe(200)
      const j = await r.json() as Record<string, unknown>
      expect(j.ok).toBe(true)
      expect(j.model).toBe("model-b2")
      expect(String(j.apiName)).toBe("备用号")
      // 软失败放行:warning 先给连接测试提示,再带跨 API 的「引擎重启中」
      expect(String(j.warning), "软失败应附连接测试提示").toContain("连接测试提示")
      expect(String(j.warning), "跨 API 切换的 warning 应提示引擎重启").toContain("重启")

      // enabledId 切到 api_b,且 api_b 的默认模型也改成所选
      const g = await getApis(s)
      expect(g.enabledId, "跨 API 切换应换 enabledId").toBe("api_b")
      expect(findItem(g, "api_b").modelID).toBe("model-b2")
      // 原 api_a 的 modelID 不被牵连
      expect(findItem(g, "api_a").modelID).toBe("model-a1")

      // 引擎 provider 写入的是新 API 的地址/密钥/模型
      const cfg = s.engineCfg() as { model: string; provider: { custom: { options: { baseURL?: string; apiKey?: string } } } }
      expect(cfg.model).toBe("custom/model-b2")
      expect(cfg.provider.custom.options.baseURL).toBe(gw.url)
      expect(cfg.provider.custom.options.apiKey).toBe("sk-bbb2222")

      // 落盘一致
      const onDisk = s.apisJson() as { enabledId: string }
      expect(onDisk.enabledId).toBe("api_b")
    } finally {
      await s.stop()
      await gw.close()
    }
  }, 60000)

  test("跨 API 切且新 API 密钥被拒(401)→ 400 硬拦,不切换、引擎配置不重写", async () => {
    const gw = await statusServer(401)
    const s = await startStudio(seed(gw.url))
    try {
      const r = await switchModel(s, "api_b", "model-b1")
      expect(r.status, "密钥被拒应 400 硬拦(未切换)").toBe(400)
      const j = await r.json() as Record<string, unknown>
      expect(j.ok).toBe(false)
      expect(String(j.error), "错误要说人话并说明未切换").toContain("不可用")
      expect(String(j.error)).toContain("未切换")

      // 状态原封不动:enabledId 仍是 api_a,api_b 的 modelID 没改,引擎配置没被重写
      const g = await getApis(s)
      expect(g.enabledId, "硬拦后不该切换 enabledId").toBe("api_a")
      expect(findItem(g, "api_b").modelID, "硬拦后不该改目标 API 的 modelID").toBe("model-b1")
      const cfg = s.engineCfg() as { model: string }
      expect(cfg.model, "硬拦后引擎配置应保持原样").toBe("custom/model-a1")
    } finally {
      await s.stop()
      await gw.close()
    }
  }, 60000)

  test("非法 model / 非法 apiId → 400,状态不落地", async () => {
    const s = await startStudio()
    try {
      // model 不在该 API 的 models[] 里
      const badModel = await switchModel(s, "api_a", "model-not-exist")
      expect(badModel.status, "非法 model 应 400").toBe(400)
      const j1 = await badModel.json() as Record<string, unknown>
      expect(j1.ok).toBe(false)
      expect(String(j1.error), "错误信息要说人话").toContain("该 API 没有这个模型")

      // apiId 不在 items 里
      const badApi = await switchModel(s, "api_nope", "model-a1")
      expect(badApi.status, "非法 apiId 应 400").toBe(400)
      const j2 = await badApi.json() as Record<string, unknown>
      expect(j2.ok).toBe(false)
      expect(String(j2.error)).toContain("没有这套 API")

      // 两次失败后状态原封不动:enabledId、modelID、引擎配置都没变
      const g = await getApis(s)
      expect(g.enabledId).toBe("api_a")
      expect(findItem(g, "api_a").modelID).toBe("model-a1")
      const cfg = s.engineCfg() as { model: string }
      expect(cfg.model, "失败请求不得改引擎配置").toBe("custom/model-a1")
    } finally {
      await s.stop()
    }
  }, 60000)

  test("无 token → 403(全局写接口门禁)", async () => {
    const s = await startStudio()
    try {
      const r = await switchModel(s, "api_a", "model-a2", false)
      expect(r.status, "无 token 应 403").toBe(403)
      const j = await r.json() as Record<string, unknown>
      expect(j.ok).toBe(false)
      // 门禁先于路由:状态没落地
      const g = await getApis(s)
      expect(g.enabledId).toBe("api_a")
      expect(findItem(g, "api_a").modelID).toBe("model-a1")
    } finally {
      await s.stop()
    }
  }, 60000)

  test("主页服务出来就带 modelChip / modelPop 与切换接线(前端一半也在)", async () => {
    const s = await startStudio()
    try {
      // 预置了有效 API → "/" 服务主页(而不是引导页)
      const home = await fetch(s.base + "/")
      expect(home.status).toBe(200)
      const html = await home.text()
      // chip 在 #thinkrow 里、位于 #thinkVal 之后(行尾,不挤 128px 滑杆)
      const rowAt = html.indexOf('id="thinkrow"')
      const valAt = html.indexOf('id="thinkVal"')
      const chipAt = html.indexOf('id="modelChip"')
      const popAt = html.indexOf('id="modelPop"')
      expect(rowAt, "#thinkrow 应在主页 HTML 里").toBeGreaterThan(0)
      expect(valAt, "#thinkVal 应在 #thinkrow 里").toBeGreaterThan(rowAt)
      expect(chipAt, "#modelChip 应在 #thinkrow 里").toBeGreaterThan(valAt)
      expect(popAt, "#modelPop 应在 #thinkrow 里").toBeGreaterThan(chipAt)
      // 菜单行/组样式与前端切换请求的接线都在服务出来的页面里
      for (const frag of ["mp-item", "mp-g", "fetch('/api/model/switch'", "getElementById('modelChip')", "getElementById('modelPop')"]) {
        expect(html.includes(frag), "主页应包含 " + frag).toBe(true)
      }
      // 服务出来的 chip 初始 title(没数据前),JS 会在拿到 /api/apis 后改写成模型全名
      expect(html.includes('id="modelChip" title="点击切换模型"')).toBe(true)
    } finally {
      await s.stop()
    }
  }, 60000)
})
