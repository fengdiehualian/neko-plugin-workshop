import { describe, expect, test } from "bun:test"
import { formatUserMemory } from "../wb-agent-lib.mjs"

describe("formatUserMemory(用户长期偏好注入)", () => {
  test("没有内容时返回空串(调用方直接拼,不用判断)", () => {
    expect(formatUserMemory([])).toBe("")
    expect(formatUserMemory(null)).toBe("")
    expect(formatUserMemory([{ text: "   " }, "", "  "])).toBe("")
  })

  test("多条按序号列出,带标题与约束说明", () => {
    const s = formatUserMemory([{ text: "配色用低饱和莫兰迪色" }, "插件名优先中文"])
    expect(s).toContain("【用户长期偏好(必须遵守)】")
    expect(s).toContain("1. 配色用低饱和莫兰迪色")
    expect(s).toContain("2. 插件名优先中文")
    expect(s).toContain("与本轮需求冲突时以本轮需求为准")
    expect(s.endsWith("\n\n")).toBe(true)
  })

  test("纯字符串与对象混用都能吃", () => {
    const s = formatUserMemory(["甲", { text: "乙" }, { text: null }])
    expect(s).toContain("1. 甲")
    expect(s).toContain("2. 乙")
  })

  test("换行/连续空白压成单空格,不破坏提示词排版", () => {
    const s = formatUserMemory([{ text: "第一行\n\n第二行\t 第三行" }])
    expect(s).toContain("1. 第一行 第二行 第三行")
    expect(s.split("\n").filter((l) => l.startsWith("1. ")).length).toBe(1)
  })

  test("单条超长截断到 300 字,不会一条吃掉全部预算", () => {
    const s = formatUserMemory([{ text: "啊".repeat(1000) }], { maxChars: 5000 })
    const line = s.split("\n").find((l) => l.startsWith("1. "))!
    expect([...line.slice(3)].length).toBe(300)
  })

  test("按码点截断,不会把 emoji 劈成半个代理项", () => {
    const s = formatUserMemory([{ text: "啊".repeat(299) + "😀😀😀" }], { maxChars: 5000 })
    const line = s.split("\n").find((l) => l.startsWith("1. "))!
    expect([...line.slice(3)].length).toBe(300)
    expect(/[\uD800-\uDBFF](?![\uDC00-\uDFFF])/.test(line)).toBe(false)
    expect(line.endsWith("😀")).toBe(true)
  })

  test("整体超预算时截断条目,不会无限膨胀", () => {
    const items = Array.from({ length: 20 }, (_, i) => ({ text: `偏好${i} ` + "x".repeat(200) }))
    const s = formatUserMemory(items, { maxChars: 800 })
    expect(s.length).toBeLessThanOrEqual(820)
    expect(s).toContain("1. 偏好0")
  })

  test("maxChars 有下限保护,传 0 也不会把内容吞光", () => {
    const s = formatUserMemory([{ text: "短偏好" }], { maxChars: 0 })
    expect(s).toContain("短偏好")
  })
})
