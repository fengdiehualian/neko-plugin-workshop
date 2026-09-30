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

  test("仓库状态类 warning(origin/工作树/独立仓库)不判负,原样放行(Issue #3)", () => {
    const c = fakeCheck({
      ok: true,
      warnings: 3,
      issues: [
        { severity: "warning", message: "git remote 'origin' is not configured" },
        { severity: "warning", message: "git working tree has uncommitted changes" },
        { severity: "warning", message: "plugin source directory does not have its own git repository" },
      ],
    })
    const s = applyStrict(c)
    expect(s.ok).toBe(true) // 只剩仓库状态类:放行,build 不被跳过
    expect(s.warnings).toBe(3) // 明细保留
  })

  test("仓库状态类与代码质量 warning 混合:只按代码质量判负,忽略数写进汇总(Issue #3)", () => {
    const c = fakeCheck({
      ok: true,
      warnings: 2,
      issues: [
        { severity: "warning", message: "git remote 'origin' is not configured" },
        { severity: "warning", message: ".gitignore should include store.db" },
      ],
    })
    const s = applyStrict(c)
    expect(s.ok).toBe(false)
    const summary = s.issues.find((i) => i.message.includes("strict 模式"))
    expect(summary?.message).toContain("1") // 只有 1 个判负
    expect(summary?.message).toContain("已忽略 1 个仓库状态类警告")
  })
})
