import { describe, expect, test } from "bun:test"
import { parseCheckIssues } from "../src/index"

describe("check output parser (按 2026-09-12 实测样本回归)", () => {
  test("Python syntax error → file/line + 中文 hint", () => {
    const issues = parseCheckIssues(
      "[FAIL] demo4_broken: check found 2 error(s), 4 warning(s)\n  [ERROR] Python syntax error in __init__.py: '{' was never closed at line 7\n  [ERROR] Python syntax error in __init__.py: '{' was never closed at line 7",
    )
    expect(issues.length).toBe(1) // 重复行去重
    expect(issues[0].severity).toBe("error")
    expect(issues[0].file).toBe("__init__.py")
    expect(issues[0].line).toBe(7)
    expect(issues[0].hint).toContain("第 7 行")
  })

  test("TOML 解析失败 → BOM hint", () => {
    const issues = parseCheckIssues(
      "[FAIL] check: failed to parse TOML file 'C:\\p\\plugin.toml': Invalid statement (at line 1, column 1)",
    )
    expect(issues.length).toBe(1)
    expect(issues[0].severity).toBe("error")
    expect(issues[0].file).toContain("plugin.toml")
    expect(issues[0].hint).toContain("BOM")
  })

  test("[plugin] 字段缺失 → 补字段 hint", () => {
    const issues = parseCheckIssues("[ERROR] [plugin].version must be a non-empty string")
    expect(issues.length).toBe(1)
    expect(issues[0].hint).toContain("version")
  })

  test("entry 类不存在 → entry 格式 hint", () => {
    const issues = parseCheckIssues("[ERROR] plugin.entry class 'NoSuchClass' was not found in __init__.py")
    expect(issues.length).toBe(1)
    expect(issues[0].file).toBe("__init__.py")
    expect(issues[0].hint).toContain("plugin.plugins.<id>")
  })

  test("warning 正常收集", () => {
    const issues = parseCheckIssues("  [WARNING] .vscode/settings.json is missing")
    expect(issues.length).toBe(1)
    expect(issues[0].severity).toBe("warning")
  })
})
