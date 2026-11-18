import { describe, expect, test } from "bun:test"
import { readFileSync, writeFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

// wb-studio.mjs 的对外面快照。
//
// 为什么要有它:这个文件 5600+ 行、单文件、顶层 `boot()` 直接执行,没有任何 import 入口。
// 拆它(契约/生成/校验/发布/外部依赖/CLI 入口)时,**对外面必须一个字节都不变**——
// 少一条路由、改一个路径、丢一个配置键,网页或本机工具就在别处坏掉,而单测看不见。
//
// 快照只锁三样「重构中不该动」的东西:
//   1. `url.pathname === "..."` 的精确路由(按出现顺序,重复保留——重复本身也是行为)
//   2. `url.pathname.match(/^\/api\/.../)` 的模式路由(原样保留正则文本)
//   3. `defaultConfig` 的键集合(顺序即默认值的书写顺序)
//
// 不锁函数清单:拆分的正常结果就是函数换文件、换名字,锁了只会逼人无意义地更新快照。
// 「搬完还能跑」由同目录的烟测与既有 20 个测试保证。
//
// 什么时候该更新:确实是**有意**改变对外面时,跑 `UPDATE_SNAPSHOT=1 bun test`,
// 并在 PR 描述里写清改了哪条路由/哪个键、为什么。

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)))
const SRC_PATH = join(ROOT, "wb-studio.mjs")
const SNAP_PATH = join(ROOT, "test", "fixtures", "wb-studio-surface.json")

type Surface = {
  exactPaths: string[]
  patternPaths: string[]
  configKeys: string[]
}

function extractSurface(src: string): Surface {
  // 出现顺序即分支判断顺序:先匹配到的路由赢,顺序变了就是行为变了
  const exactPaths = [...src.matchAll(/url\.pathname === "([^"]+)"/g)].map((m) => m[1])
  const patternPaths = [...src.matchAll(/url\.pathname\.match\(\s*(\/\^[^\n]*?\/[a-z]*)\s*\)/g)]
    .map((m) => m[1])

  const start = src.indexOf("const defaultConfig = {")
  if (start < 0) throw new Error("wb-studio.mjs 里找不到 defaultConfig,提取器该跟着改")
  const end = src.indexOf("\n}", start)
  if (end < 0) throw new Error("defaultConfig 没有找到结束大括号")
  const configKeys = [...src.slice(start, end).matchAll(/^ {2}([A-Za-z][\w]*):/gm)].map((m) => m[1])

  return { exactPaths, patternPaths, configKeys }
}

describe("wb-studio 对外面快照", () => {
  const src = readFileSync(SRC_PATH, "utf8")
  const actual = extractSurface(src)

  test("快照与源码一致(拆代码前后必须相同)", () => {
    if (process.env.UPDATE_SNAPSHOT === "1") {
      writeFileSync(SNAP_PATH, JSON.stringify(actual, null, 2) + "\n", "utf8")
    }
    const expected = JSON.parse(readFileSync(SNAP_PATH, "utf8")) as Surface

    // 分三块断言:失败时能立刻看出是路由、模式路由还是配置键变了
    expect(actual.exactPaths, "精确路由集合/顺序变了").toEqual(expected.exactPaths)
    expect(actual.patternPaths, "模式路由集合/顺序变了").toEqual(expected.patternPaths)
    expect(actual.configKeys, "defaultConfig 的键变了").toEqual(expected.configKeys)
  })

  test("关键入口必须还在(失败时说人话,不只给一个快照 diff)", () => {
    for (const p of ["/", "/api/health", "/api/sessions", "/api/backend", "/api/setup"]) {
      expect(actual.exactPaths, `入口路由 ${p} 不见了`).toContain(p)
    }
    // 模式路由在源码里是正则字面量(`/^\/api\/sessions\/([^/]+)\/compact$/`),
    // 字符串里带反斜杠转义,所以按片段名判,不按带斜杠的路径判
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
    const cfg = src.slice(src.indexOf("const defaultConfig = {"), src.indexOf("\n}", src.indexOf("const defaultConfig = {")))
    // 取完整数字再比:只写 toContain("port: 5099") 时,改成 50990 也会"通过"(CodeRabbit PR#35 指出)
    const portInit = cfg.match(/\bport:\s*(\d+)/)
    expect(portInit?.[1], "默认端口不该在重构中变化").toBe("5099")
  })

  test("源码里没有残留的占位/半成品标记", () => {
    for (const bad of ["TODO: wire", "FIXME(move)", "<<<<<<<", ">>>>>>>"]) {
      expect(src.includes(bad), `源码里残留 ${bad}`).toBe(false)
    }
  })
})
