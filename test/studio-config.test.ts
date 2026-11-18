import { describe, expect, test } from "bun:test"
import { mkdtempSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"

import { CONFIG_FILE, PATH_KEYS, defaultConfig, loadConfig, resolvePath }
  from "../src/studio/config.mjs"

// 配置层搬出来之后必须有自己的单测。
//
// 它决定「工坊读哪份配置、缺文件时用什么」:读错会让用户改的端口不生效,
// 或者把相对路径解析到错误目录——两种都不会让冒烟测试变红(冒烟用的是自己写的临时配置)。

function withRoot(cfg: Record<string, unknown> | null, fn: (root: string) => void) {
  const root = mkdtempSync(join(tmpdir(), "studio-config-"))
  try {
    if (cfg) writeFileSync(join(root, CONFIG_FILE), JSON.stringify(cfg), "utf8")
    fn(root)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
}

describe("loadConfig", () => {
  test("没有配置文件时用默认值", () => {
    withRoot(null, (root) => {
      const c = loadConfig(root)
      expect(c.port).toBe(5099)
      expect(c.openBrowser).toBe(true)
      expect(c.engineMode).toBe("exe")
      expect(c.contextLimit).toBe(128000)
    })
  })

  test("配置文件覆盖默认值,没写的键仍走默认", () => {
    withRoot({ port: 6001, model: "m1" }, (root) => {
      const c = loadConfig(root)
      expect(c.port).toBe(6001)
      expect(c.model).toBe("m1")
      expect(c.openBrowser).toBe(true)
      expect(c.timeoutSec).toBe(1500)
    })
  })

  test("相对路径按 root 解析,绝对路径原样", () => {
    withRoot({ workspace: "./ws", nekoRepo: "C:/abs/neko" }, (root) => {
      const c = loadConfig(root)
      expect(c.workspace).toBe(join(root, "ws"))
      expect(c.nekoRepo).toBe("C:/abs/neko")
    })
  })

  test("thinkingLevel 非法时回落 auto", () => {
    withRoot({ thinkingLevel: "turbo" }, (root) => {
      expect(loadConfig(root).thinkingLevel).toBe("auto")
    })
  })

  test("配置文件是坏 JSON 时退回默认值,不抛", () => {
    const root = mkdtempSync(join(tmpdir(), "studio-config-"))
    try {
      writeFileSync(join(root, CONFIG_FILE), "{ 坏掉的", "utf8")
      const c = loadConfig(root)
      expect(c.port).toBe(5099)
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })

  test("返回独立对象:改它不污染 defaultConfig", () => {
    withRoot(null, (root) => {
      const c = loadConfig(root)
      c.workspace = "/tmp/changed"
      c.port = 1
      expect(defaultConfig.workspace).toBe("./workspace")
      expect(defaultConfig.port).toBe(5099)
    })
  })

  test("PATH_KEYS 里的键都在默认配置里", () => {
    for (const k of PATH_KEYS) expect(defaultConfig, "缺路径键 " + k).toHaveProperty(k)
  })
})

describe("resolvePath", () => {
  test("./ 与 ../ 相对 root 解析", () => {
    expect(resolvePath("./a", "/r")).toBe(join("/r", "./a"))
    expect(resolvePath("../a", "/r")).toBe(join("/r", "../a"))
  })

  test("绝对路径与空值原样返回", () => {
    expect(resolvePath("C:/x/y", "/r")).toBe("C:/x/y")
    expect(resolvePath("", "/r")).toBe("")
    expect(resolvePath(null, "/r")).toBe(null)
  })
})
