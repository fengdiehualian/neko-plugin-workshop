import { describe, expect, test } from "bun:test"
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"

import { THINK_LEVELS, estTokens, openaiEffort, sessionTokens, writeJsonAtomic }
  from "../src/studio/util.mjs"

// 搬出来的纯函数必须有自己的单测。
//
// 「能启动」只证明 import 通了,证明不了估算公式或原子落盘没被搬错。
// 这几个函数被上下文压缩与退出清理依赖,算错是静默的:token 少算会撑爆上下文,
// 落盘不原子会留下半截 JSON,而两者都不会让冒烟测试变红。

describe("estTokens", () => {
  test("空值返回 0", () => {
    expect(estTokens("")).toBe(0)
    expect(estTokens(null)).toBe(0)
    expect(estTokens(undefined)).toBe(0)
  })

  test("中文按 1 字 1 token", () => {
    expect(estTokens("猫娘计划")).toBe(4)
  })

  test("英文按 3.5 字符 1 token,向上取整", () => {
    expect(estTokens("abc")).toBe(1)        // 3/3.5 → 1
    expect(estTokens("abcdefg")).toBe(2)    // 7/3.5 → 2
    expect(estTokens("a".repeat(35))).toBe(10)
  })

  test("中英混排分别计", () => {
    // 2 个汉字 + 7 个英文 = 2 + 2 = 4
    expect(estTokens("猫娘abcdefg")).toBe(4)
  })
})

describe("sessionTokens", () => {
  test("优先用最近一轮引擎回传的 usage", () => {
    const s = {
      messages: [
        { content: "x".repeat(100) },
        { content: "y", usage: { input: 100, output: 20, reasoning: 5 } },
      ],
    }
    expect(sessionTokens(s)).toBe(125)
  })

  test("summarizedUpTo 之前的消息不计入", () => {
    const s = {
      summarizedUpTo: 1,
      summary: "猫娘",                       // 2 tokens
      messages: [
        { content: "a".repeat(350) },        // 已进摘要,忽略
        { content: "b".repeat(7) },          // 2 tokens + 8 固定开销
      ],
    }
    expect(sessionTokens(s)).toBe(2 + 2 + 8)
  })

  test("没有 messages 与 summary 时返回 0", () => {
    expect(sessionTokens({})).toBe(0)
    expect(sessionTokens(null)).toBe(0)
  })

  test("usage 全为 0 时不当作有效值,继续按内容估算", () => {
    const s = { messages: [{ content: "猫娘", usage: { input: 0, output: 0 } }] }
    expect(sessionTokens(s)).toBe(2 + 8)
  })
})

describe("openaiEffort", () => {
  test("none 映射成 minimal,xhigh 映射成 high", () => {
    expect(openaiEffort("none")).toBe("minimal")
    expect(openaiEffort("xhigh")).toBe("high")
  })

  test("其余原样透传", () => {
    for (const v of ["auto", "low", "medium", "high"]) expect(openaiEffort(v)).toBe(v)
  })

  test("THINK_LEVELS 覆盖这六档", () => {
    expect(THINK_LEVELS).toEqual(["auto", "none", "low", "medium", "high", "xhigh"])
  })
})

describe("writeJsonAtomic", () => {
  test("写入内容正确,且不残留 .tmp", () => {
    const dir = mkdtempSync(join(tmpdir(), "studio-util-"))
    try {
      const p = join(dir, "nested", "a.json")
      writeJsonAtomic(p, { a: 1, 中文: "值" })
      expect(JSON.parse(readFileSync(p, "utf8"))).toEqual({ a: 1, 中文: "值" })
      expect(existsSync(p + ".tmp"), "残留了 .tmp").toBe(false)
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  test("父目录不存在时自动创建", () => {
    const dir = mkdtempSync(join(tmpdir(), "studio-util-"))
    try {
      const p = join(dir, "x", "y", "z", "b.json")
      writeJsonAtomic(p, { ok: true })
      expect(existsSync(p)).toBe(true)
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })
})
