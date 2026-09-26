import { describe, expect, test } from "bun:test"
import { applyStrict, type CheckSummary } from "../src/index"

function fakeCheck(over: Partial<CheckSummary>): CheckSummary {
  return {
    ok: true,
    exitCode: 0,
    errors: 0,
    warnings: 0,
    issues: [],
    raw: "",
    ...over,
  }
}

describe("applyStrict(strict 模式:warning 视为失败)", () => {
  test("check 失败时原样透传,不额外加工", () => {
    const c = fakeCheck({ ok: false, errors: 2, warnings: 1 })
    expect(applyStrict(c)).toBe(c)
  })

  test("零 warning 时原样透传(通过)", () => {
    const c = fakeCheck({ ok: true, warnings: 0 })
    expect(applyStrict(c)).toBe(c)
    expect(applyStrict(c).ok).toBe(true)
  })

  test("有 warning ⇒ 判为不通过,追加汇总 issue,保留原 warning 计数与明细", () => {
    const c = fakeCheck({
      ok: true,
      warnings: 6,
      issues: [{ severity: "warning", message: ".vscode/settings.json is missing" }],
    })
    const s = applyStrict(c)
    expect(s.ok).toBe(false)
    expect(s.warnings).toBe(6) // 计数语义不变
    expect(s.errors).toBe(0)
    const summary = s.issues.find((i) => i.message.includes("strict 模式"))
    expect(summary?.severity).toBe("error")
    expect(summary?.message).toContain("6")
    expect(s.issues.some((i) => i.message.includes(".vscode"))).toBe(true) // 原明细保留
  })
})
