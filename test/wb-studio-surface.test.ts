import { describe, expect, test } from "bun:test"
import { existsSync, readFileSync, readdirSync, writeFileSync } from "node:fs"
import { dirname, join, relative, sep } from "node:path"
import { fileURLToPath } from "node:url"

// wb-studio 的对外面快照。
//
// 为什么要有它:主文件 5600+ 行、顶层 `boot()` 直接执行,没有 import 入口。
// 拆它(契约/生成/校验/发布/外部依赖/CLI 入口)时,**对外面必须一个字节都不变**——
// 少一条路由、改一个路径、丢一个配置键,网页或本机工具就在别处坏掉,而单测看不见。
//
// 扫哪些文件:主文件 + `src/studio/**/*.mjs`。拆到第 2 块起,`defaultConfig` 与路由会
// 搬进 src/studio/,只扫主文件的话快照会「变少而红」,而 README 教的 UPDATE_SNAPSHOT
// 又会把它刷掉——网就白织了。
//
// 锁三样「重构中不该动」的东西,分两种粒度:
//   1 路由**集合**(精确 + 模式):跨文件搬动不该改变它 —— 排序后比,搬走/搬回来都不红;
//   2 每个文件内的**出现顺序**:同一文件里的 if 顺序决定谁先匹配,换顺序就是改行为;
//   3 `defaultConfig` 的键(按书写顺序)与默认端口。
//
// 不锁函数清单:拆分的正常结果就是函数换文件换名字,锁了只会逼人无意义地刷新快照。
// 「搬完还能跑」由同目录的烟测保证。
//
// 更新快照:`UPDATE_SNAPSHOT=1 bun test`。它会写完立刻抛错,必须 review diff 后重跑
// 第二次才算数;CI 环境直接拒绝这个变量。

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)))
const SRC_PATH = join(ROOT, "wb-studio.mjs")
const STUDIO_DIR = join(ROOT, "src", "studio")
const SNAP_PATH = join(ROOT, "test", "fixtures", "wb-studio-surface.json")

type FileSurface = {
  exact: string[]
  pattern: string[]
  configKeys: string[]
  defaultPort: string | null
}

type Surface = {
  schema: number
  exactPaths: string[]
  patternPaths: string[]
  configKeys: string[]
  defaultPort: string | null
  perFileOrder: Record<string, { exact: string[]; pattern: string[] }>
}

/** src/studio 下所有 .mjs(递归)。没有这个目录时返回空。 */
function listStudioModules(dir: string, out: string[] = []): string[] {
  if (!existsSync(dir)) return out
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name)
    if (e.isDirectory()) { listStudioModules(p, out); continue }
    if (e.isFile() && e.name.endsWith(".mjs")) out.push(p)
  }
  return out
}

/** 主文件在前,其余按相对路径字典序 —— 保证快照可复现(目录遍历顺序不可靠)。 */
function surfaceFiles(): { rel: string; abs: string }[] {
  const rest = listStudioModules(STUDIO_DIR)
    .map((abs) => ({ rel: relative(ROOT, abs).split(sep).join("/"), abs }))
    .sort((a, b) => (a.rel < b.rel ? -1 : a.rel > b.rel ? 1 : 0))
  return [{ rel: "wb-studio.mjs", abs: SRC_PATH }, ...rest]
}

function extractOne(src: string): FileSurface {
  // 出现顺序即分支判断顺序:先匹配到的路由赢,顺序变了就是行为变了
  const exact = [...src.matchAll(/url\.pathname === "([^"]+)"/g)].map((m) => m[1])
  const pattern = [...src.matchAll(/url\.pathname\.match\(\s*(\/\^[^\n]*?\/[a-z]*)\s*\)/g)]
    .map((m) => m[1])

  let configKeys: string[] = []
  let defaultPort: string | null = null
  const start = src.indexOf("const defaultConfig = {")
  if (start >= 0) {
    const end = src.indexOf("\n}", start)
    if (end < 0) throw new Error("defaultConfig 没有找到结束大括号")
    const seg = src.slice(start, end)
    configKeys = [...seg.matchAll(/^ {2}([A-Za-z][\w]*):/gm)].map((m) => m[1])
    const pm = seg.match(/\bport:\s*(\d+)/)
    defaultPort = pm ? pm[1] : null
  }
  return { exact, pattern, configKeys, defaultPort }
}

function extractSurface(): Surface {
  const exactSet = new Set<string>()
  const patternSet = new Set<string>()
  const configKeys: string[] = []
  const seenKey = new Set<string>()
  const perFileOrder: Record<string, { exact: string[]; pattern: string[] }> = {}
  let defaultPort: string | null = null

  for (const f of surfaceFiles()) {
    const one = extractOne(readFileSync(f.abs, "utf8"))
    one.exact.forEach((p) => exactSet.add(p))
    one.pattern.forEach((p) => patternSet.add(p))
    for (const k of one.configKeys) {
      if (!seenKey.has(k)) { seenKey.add(k); configKeys.push(k) }
    }
    if (one.defaultPort && !defaultPort) defaultPort = one.defaultPort
    // 只记有路由的文件:纯工具模块搬来搬去不该影响快照
    if (one.exact.length || one.pattern.length) {
      perFileOrder[f.rel] = { exact: one.exact, pattern: one.pattern }
    }
  }

  const sorted = (s: Set<string>) => [...s].sort()
  return {
    schema: 2,
    exactPaths: sorted(exactSet),
    patternPaths: sorted(patternSet),
    configKeys,
    defaultPort,
    perFileOrder,
  }
}

describe("wb-studio 对外面快照", () => {
  const actual = extractSurface()

  test("快照与源码一致(拆代码前后必须相同)", () => {
    if (process.env.UPDATE_SNAPSHOT === "1") {
      if (process.env.CI) {
        throw new Error("CI 环境禁止 UPDATE_SNAPSHOT=1:快照只能在本地更新并 review diff")
      }
      writeFileSync(SNAP_PATH, JSON.stringify(actual, null, 2) + "\n", "utf8")
      // 写完就抛:否则"先写再读再比"必然通过,等于自己给自己发通行证
      throw new Error("快照已重写到 " + SNAP_PATH + ":请 review diff 后重跑 bun test")
    }
    const expected = JSON.parse(readFileSync(SNAP_PATH, "utf8")) as Surface

    expect(actual.schema, "快照格式版本变了").toBe(expected.schema)
    // 集合:跨文件搬动不改变它
    expect(actual.exactPaths, "精确路由集合变了").toEqual(expected.exactPaths)
    expect(actual.patternPaths, "模式路由集合变了").toEqual(expected.patternPaths)
    // 配置面:键与默认端口
    expect(actual.configKeys, "defaultConfig 的键变了").toEqual(expected.configKeys)
    expect(actual.defaultPort, "默认端口不该在重构中变化").toBe(expected.defaultPort)
    // 单文件内的顺序:换顺序就是改行为,换文件则只该在 perFileOrder 的键上体现
    expect(Object.keys(actual.perFileOrder).sort(), "有路由的文件集合变了")
      .toEqual(Object.keys(expected.perFileOrder).sort())
    for (const f of Object.keys(expected.perFileOrder)) {
      expect(actual.perFileOrder[f].exact, f + " 的精确路由顺序变了")
        .toEqual(expected.perFileOrder[f].exact)
      expect(actual.perFileOrder[f].pattern, f + " 的模式路由顺序变了")
        .toEqual(expected.perFileOrder[f].pattern)
    }
  })

  test("关键入口必须还在(失败时说人话,不只给一个快照 diff)", () => {
    for (const p of ["/", "/api/health", "/api/sessions", "/api/backend", "/api/setup"]) {
      expect(actual.exactPaths, `入口路由 ${p} 不见了`).toContain(p)
    }
    // 模式路由在源码里是正则字面量,字符串里带反斜杠转义,所以按片段名判
    for (const frag of ["sessions", "apis"]) {
      expect(actual.patternPaths.some((p) => p.includes(frag)),
        `带 id 的 ${frag} 模式路由不见了`).toBe(true)
    }
  })

  test("配置面必须覆盖启动所需的最小集合", () => {
    for (const k of ["port", "workspace", "engineMode", "openBrowser", "openMode",
                     "timeoutSec", "stallTimeoutSec", "contextLimit"]) {
      expect(actual.configKeys, `配置键 ${k} 不见了`).toContain(k)
    }
    // 取完整数字再比:只判断包含 "5099" 时,50990 也会"通过"
    expect(actual.defaultPort, "默认端口不该在重构中变化").toBe("5099")
  })

  test("源码里没有残留的占位/半成品标记", () => {
    for (const f of surfaceFiles()) {
      const src = readFileSync(f.abs, "utf8")
      for (const bad of ["TODO: wire", "FIXME(move)", "<<<<<<<", ">>>>>>>"]) {
        expect(src.includes(bad), f.rel + " 里残留 " + bad).toBe(false)
      }
    }
  })
})
