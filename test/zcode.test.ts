import { describe, expect, test } from "bun:test"
import { buildZcodeArgs, parseZcodeOutput } from "../wb-agent-lib.mjs"

describe("ZCode 底座参数", () => {
  test("基础参数:prompt/cwd/mode/json", () => {
    const a = buildZcodeArgs({ text: "做一个插件", dir: "C:/ws", zcodeMode: "yolo" })
    expect(a).toContain("--prompt")
    expect(a[a.indexOf("--prompt") + 1]).toBe("做一个插件")
    expect(a[a.indexOf("--cwd") + 1]).toBe("C:/ws")
    expect(a[a.indexOf("--mode") + 1]).toBe("yolo")
    expect(a[a.indexOf("--output-format") + 1]).toBe("json")
    expect(a).toContain("--no-color")
  })

  test("有会话时带 --resume", () => {
    const a = buildZcodeArgs({ text: "x", dir: "C:/ws", sessionID: "sess_123" })
    expect(a).toContain("--resume")
    expect(a[a.indexOf("--resume") + 1]).toBe("sess_123")
  })

  test("无会话不带 --resume", () => {
    expect(buildZcodeArgs({ text: "x", dir: "C:/ws" })).not.toContain("--resume")
  })

  test("模式缺省为 yolo", () => {
    const a = buildZcodeArgs({ text: "x", dir: "C:/ws" })
    expect(a[a.indexOf("--mode") + 1]).toBe("yolo")
  })
})

describe("ZCode 输出解析", () => {
  test("整段 JSON:取 response 与 sessionId", () => {
    const r = parseZcodeOutput(JSON.stringify({ sessionId: "s1", response: "做好了" }))
    expect(r.reply).toBe("做好了")
    expect(r.sessionId).toBe("s1")
  })

  test("result 字段也认", () => {
    const r = parseZcodeOutput(JSON.stringify({ result: "方案如下" }))
    expect(r.reply).toBe("方案如下")
  })

  test("NDJSON:取最后一条有内容的事件", () => {
    const nd = [
      JSON.stringify({ type: "start" }),
      JSON.stringify({ type: "delta", response: "半截" }),
      JSON.stringify({ type: "result", response: "完整结果", sessionId: "s2" }),
    ].join("\n")
    const r = parseZcodeOutput(nd)
    expect(r.reply).toBe("完整结果")
    expect(r.sessionId).toBe("s2")
  })

  test("纯文本:原样返回", () => {
    const r = parseZcodeOutput("就是一段普通输出\n第二行")
    expect(r.reply).toContain("普通输出")
    expect(r.sessionId).toBe(null)
  })

  test("空输出", () => {
    const r = parseZcodeOutput("   ")
    expect(r.reply).toBe(null)
  })

  test("usage 透传", () => {
    const r = parseZcodeOutput(JSON.stringify({ response: "ok", usage: { input: 12, output: 3 } }))
    expect(r.usage).toEqual({ input: 12, output: 3 })
  })
})
