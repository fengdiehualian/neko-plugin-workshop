import { describe, expect, test } from "bun:test"
import { spawn } from "node:child_process"
import { copyFileSync, cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs"
import { createServer } from "node:net"
import { tmpdir } from "node:os"
import { basename, dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

// /api/upload 的端到端:真的起一个实例(临时 workspace + 随机端口),按真实 HTTP 打上传接口。
// 起实例的惯例照抄同目录 wb-studio-smoke.test.ts:主文件 + wb-agent-lib.mjs + src/ 复制进临时目录,
// engineMode 固定 "cli" 不拉引擎;token 等端口占住后落在 <临时目录>/runtime/studio-token.txt。
//
// 为什么必须端到端:/api/upload 的行为是「全局门禁(415/413/403)→ 路由校验(400)→ 落盘」三层
// 叠出来的,单测拼不出真实状态码。超限那条尤其要注意:MAX_BODY_BYTES(全局请求体 8MB 上限)
// 会先于路由的 6MB 检查生效——6MiB 的文件 base64 后是 8,388,608 字符,加 JSON 信封必然
// 超过 8MB,实测连「正好 6MiB」都回 413。所以超限用例接受 400 或 413(都等于「被拒、没落盘」),
// 路由里自己的 >6MiB→400 分支是防御纵深:万一将来 MAX_BODY_BYTES 放宽,它才是真正的闸门。
// 用户面对的一致承诺是前端预检的「单个附件最大 6MB」(=6000000 字节,见 wb-studio.mjs 上传循环)。

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
  dir: string
  token: string
  uploadsDir: () => string
  stop: () => Promise<void>
}

/** 在临时目录起一个实例,等到 /api/health 能回话(或超时抛错)。 */
async function startStudio(): Promise<Smoke> {
  const dir = mkdtempSync(join(tmpdir(), "wbstudio-upload-"))
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
      uploadsDir: () => join(dir, "workspace", "uploads"),
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

/** 带默认头(content-type + x-wb-token)的 POST,可覆盖/追加头。 */
function post(s: Smoke, body: string, headers: Record<string, string> = {}): Promise<Response> {
  return fetch(s.base + "/api/upload", {
    method: "POST",
    headers: { "content-type": "application/json", "x-wb-token": s.token, ...headers },
    body,
  })
}

const b64 = (s: string) => Buffer.from(s, "utf8").toString("base64")

describe("wb-studio /api/upload", () => {
  test("带 token 上传小文本 → ok:true,文件真实落在 workspace/uploads,内容一致", async () => {
    const s = await startStudio()
    try {
      const content = "你好,工坊!这是附件内容 123"
      const r = await post(s, JSON.stringify({ name: "笔记.txt", b64: b64(content) }))
      expect(r.status, "上传应成功").toBe(200)
      const j = await r.json() as Record<string, unknown>
      expect(j.ok, "应回 ok:true").toBe(true)
      expect(String(j.path), "path 应指向 uploads/ 下").toMatch(/^uploads\/\d{8}-[0-9a-f]{8}-/)
      expect(String(j.name), "name 应保留原扩展名 .txt").toMatch(/\.txt$/)

      // 文件真实落盘且内容一致
      const full = join(s.dir, "workspace", String(j.path))
      expect(existsSync(full), "文件应真实写在 workspace/uploads").toBe(true)
      expect(readFileSync(full, "utf8")).toBe(content)
      // 名字里的原始文件名(防撞前缀之外)也保留着
      expect(String(j.name)).toContain("笔记")
      expect(s.uploadsDir() && readdirSync(s.uploadsDir()).length, "uploads 目录应有 1 个文件").toBe(1)
    } finally {
      await s.stop()
    }
  }, 60000)

  test("name 含 ../ 穿越 → 400,且任何位置都不落盘", async () => {
    const s = await startStudio()
    try {
      for (const name of ["../evil.txt", "..\\evil.txt", "..", "a/../../evil.txt"]) {
        const r = await post(s, JSON.stringify({ name, b64: b64("x") }))
        expect(r.status, name + " 应被拒").toBe(400)
        const j = await r.json() as Record<string, unknown>
        expect(j.ok, name + " 应回 ok:false").toBe(false)
        expect(String(j.error), "错误要说人话").toContain("穿越")
      }
      // 工作区任何地方都没有 evil.txt(uploads 不存在也不该被建出来)
      expect(existsSync(s.uploadsDir()), "uploads 目录不该被建出来").toBe(false)
      expect(existsSync(join(s.dir, "workspace", "evil.txt")), "工作区根不该出现 evil.txt").toBe(false)
    } finally {
      await s.stop()
    }
  }, 60000)

  test("绝对路径名 → 400;0 字节文件(b64 为空)→ 400", async () => {
    const s = await startStudio()
    try {
      // 绝对路径(Windows 与 POSIX 风格)不得借 resolve 逃出 uploads
      for (const name of ["C:\\evil.txt", "/etc/passwd"]) {
        const r = await post(s, JSON.stringify({ name, b64: b64("x") }))
        expect(r.status, name + " 应被拒").toBe(400)
        const j = await r.json() as Record<string, unknown>
        expect(j.ok, name + " 应回 ok:false").toBe(false)
      }
      // 0 字节文件:前端 FileReader 对空文件会发出 b64:""(dataURL 去头后为空串),
      // 走「缺 b64」分支拒收——把这条 UI 真实形状钉进回归网
      const empty = await post(s, JSON.stringify({ name: "empty.txt", b64: "" }))
      expect(empty.status, "0 字节文件应 400").toBe(400)
      const j = await empty.json() as Record<string, unknown>
      expect(j.ok).toBe(false)
      expect(existsSync(s.uploadsDir()) ? readdirSync(s.uploadsDir()).length : 0, "不应有任何落盘").toBe(0)
    } finally {
      await s.stop()
    }
  }, 60000)

  test("同名文件重复上传 → 各自独立防撞前缀,互不覆盖", async () => {
    const s = await startStudio()
    try {
      const r1 = await post(s, JSON.stringify({ name: "notes.txt", b64: b64("第一份") }))
      const r2 = await post(s, JSON.stringify({ name: "notes.txt", b64: b64("第二份内容不同") }))
      expect(r1.status).toBe(200)
      expect(r2.status).toBe(200)
      const j1 = await r1.json() as Record<string, unknown>
      const j2 = await r2.json() as Record<string, unknown>
      expect(String(j1.name), "两次落盘名应不同(随机前缀防撞)").not.toBe(String(j2.name))
      expect(String(j1.name)).toContain("notes.txt")
      expect(String(j2.name)).toContain("notes.txt")
      // 两个文件都在,内容互不覆盖
      const f1 = join(s.dir, "workspace", String(j1.path))
      const f2 = join(s.dir, "workspace", String(j2.path))
      expect(existsSync(f1)).toBe(true)
      expect(existsSync(f2)).toBe(true)
      expect(readFileSync(f1, "utf8")).toBe("第一份")
      expect(readFileSync(f2, "utf8")).toBe("第二份内容不同")
      expect(readdirSync(s.uploadsDir()).length, "uploads 应有 2 个文件").toBe(2)
    } finally {
      await s.stop()
    }
  }, 60000)

  test("超 6MB → 被拒(400/413),uploads/ 不落盘", async () => {
    const s = await startStudio()
    try {
      const big = Buffer.alloc(6 * 1024 * 1024 + 1024, 0x61) // 6MB + 1KB
      const body = JSON.stringify({ name: "big.bin", b64: big.toString("base64") })
      const r = await post(s, body)
      // 见文件头注释:6MB+ 的文件 base64 后(约 8.4MB)必然先撞全局 8MB 请求体上限 → 413;
      // 无论 400 还是 413,语义都是「被拒」。关键断言是没有落盘。
      expect([400, 413]).toContain(r.status)
      if (r.status === 400) {
        const j = await r.json() as Record<string, unknown>
        expect(j.ok, "路由内拒绝应回 ok:false").toBe(false)
      }
      expect(existsSync(s.uploadsDir()) ? readdirSync(s.uploadsDir()).length : 0, "uploads 不该有任何文件").toBe(0)
    } finally {
      await s.stop()
    }
  }, 60000)

  test("无 token → 403;缺 b64 / 非法 base64 → 路由自身 400", async () => {
    const s = await startStudio()
    try {
      // d) 不带 x-wb-token:全局写接口门禁直接 403
      const noTok = await fetch(s.base + "/api/upload", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ name: "a.txt", b64: b64("x") }),
      })
      expect(noTok.status, "无 token 应 403").toBe(403)

      // 路由自身的校验 400:缺 b64、非法 base64
      const noB64 = await post(s, JSON.stringify({ name: "a.txt" }))
      expect(noB64.status, "缺 b64 应 400").toBe(400)
      const j1 = await noB64.json() as Record<string, unknown>
      expect(j1.ok).toBe(false)

      const badB64 = await post(s, JSON.stringify({ name: "a.txt", b64: "这不是base64!!!" }))
      expect(badB64.status, "非法 base64 应 400").toBe(400)
      const j2 = await badB64.json() as Record<string, unknown>
      expect(j2.ok).toBe(false)
      expect(String(j2.error)).toContain("base64")

      // 缺 name 同样 400
      const noName = await post(s, JSON.stringify({ b64: b64("x") }))
      expect(noName.status, "缺 name 应 400").toBe(400)

      // 以上都不得落盘
      expect(existsSync(s.uploadsDir()) ? readdirSync(s.uploadsDir()).length : 0).toBe(0)
    } finally {
      await s.stop()
    }
  }, 60000)
})
