import { describe, expect, test } from "bun:test"
import { planModelRetry } from "../wb-agent-lib.mjs"

/** 造一个驱动层结果 */
function res(over: Record<string, unknown> = {}) {
  return { ok: false, reply: null, artifacts: [], modifiedFiles: [], stopped: false, error: "", ...over }
}

describe("planModelRetry(模型无响应自动重试)", () => {
  test("看门狗判定无响应 → 重试,且识别为 no-response", () => {
    const p = planModelRetry({ attemptsMade: 1, maxRetries: 2, result: res({ error: "模型服务已 360 秒没有任何响应,任务被自动停止" }) })
    expect(p).not.toBeNull()
    expect(p!.kind).toBe("no-response")
  })

  test("本轮没生成回复 → 重试", () => {
    const p = planModelRetry({ attemptsMade: 1, maxRetries: 1, result: res({ error: "本轮没有生成新回复(模型未返回内容,可重试)" }) })
    expect(p!.kind).toBe("no-response")
  })

  test("零输出且没有错误文本(CLI 退出 0 空输出 / 引擎空转) → 仍重试", () => {
    const p = planModelRetry({ attemptsMade: 1, maxRetries: 2, result: res({ error: null }) })
    expect(p).not.toBeNull()
    expect(p!.kind).toBe("no-response")
  })

  test("连接失败 → 重试,识别为 connection", () => {
    const p = planModelRetry({ attemptsMade: 1, maxRetries: 2, result: null, error: "fetch failed: ECONNREFUSED 127.0.0.1:4096" })
    expect(p!.kind).toBe("connection")
  })

  test("已经有回复 → 不重试(避免重复干活/重复扣费)", () => {
    expect(planModelRetry({ attemptsMade: 1, maxRetries: 2, result: res({ reply: "做好了", error: "中途报错" }) })).toBeNull()
  })

  test("已经产出插件文件 → 不重试", () => {
    expect(planModelRetry({ attemptsMade: 1, maxRetries: 2, result: res({ artifacts: ["a.neko-plugin"], error: "超时" }) })).toBeNull()
    expect(planModelRetry({ attemptsMade: 1, maxRetries: 2, result: res({ modifiedFiles: ["x.py"], error: "超时" }) })).toBeNull()
  })

  test("用户主动停止 → 不重试", () => {
    expect(planModelRetry({ attemptsMade: 1, maxRetries: 3, result: res({ stopped: true, error: "任务已停止" }) })).toBeNull()
    expect(planModelRetry({ attemptsMade: 1, maxRetries: 3, result: res({ error: "任务已停止" }), aborted: true })).toBeNull()
  })

  test("密钥/鉴权错误 → 不重试(重试白等)", () => {
    expect(planModelRetry({ attemptsMade: 1, maxRetries: 3, result: res({ error: "401 Unauthorized: invalid api key" }) })).toBeNull()
    expect(planModelRetry({ attemptsMade: 1, maxRetries: 3, result: res({ error: "模型服务拒绝访问:密钥可能不对" }) })).toBeNull()
  })

  test("额度耗尽 → 不重试", () => {
    expect(planModelRetry({ attemptsMade: 1, maxRetries: 3, result: res({ error: "insufficient quota, please top up" }) })).toBeNull()
    expect(planModelRetry({ attemptsMade: 1, maxRetries: 3, result: res({ error: "账户余额不足" }) })).toBeNull()
  })

  test("重试次数用尽 → 不再重试", () => {
    const r = res({ error: "等待超时" })
    expect(planModelRetry({ attemptsMade: 2, maxRetries: 2, result: r })).not.toBeNull() // 第 2 次失败后还能再试 1 次
    expect(planModelRetry({ attemptsMade: 3, maxRetries: 2, result: r })).toBeNull() // 共 3 次机会用完
    expect(planModelRetry({ attemptsMade: 1, maxRetries: 0, result: r })).toBeNull() // 关闭重试
  })

  test("退避翻倍,上限 30 秒", () => {
    expect(planModelRetry({ attemptsMade: 1, maxRetries: 5, baseDelayMs: 3000, result: res({ error: "timeout" }) })!.waitMs).toBe(3000)
    expect(planModelRetry({ attemptsMade: 2, maxRetries: 5, baseDelayMs: 3000, result: res({ error: "timeout" }) })!.waitMs).toBe(6000)
    expect(planModelRetry({ attemptsMade: 4, maxRetries: 5, baseDelayMs: 3000, result: res({ error: "timeout" }) })!.waitMs).toBe(24000)
    expect(planModelRetry({ attemptsMade: 5, maxRetries: 5, baseDelayMs: 3000, result: res({ error: "timeout" }) })!.waitMs).toBe(30000)
  })

  test("成功结果不重试", () => {
    expect(planModelRetry({ attemptsMade: 1, maxRetries: 2, result: res({ ok: true, reply: "好了" }) })).toBeNull()
  })
})
