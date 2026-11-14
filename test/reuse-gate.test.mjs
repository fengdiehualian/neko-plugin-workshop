// 复用门禁端到端用例(确定部分):新建任务在写码前必须先做市场检索.
// 跑法: bun test test/reuse-gate.e2e.mjs
// LLM 行为部分见 test/reuse-gate.llm-protocol.md(需要一个真 agent 跑,不能在这里自动)。Python 规则用例由 test/rules.test.ts 挂进 bun。
import { expect, test, describe } from "bun:test"
import { readFileSync, existsSync } from "node:fs"
import { join } from "node:path"

const ROOT = join(import.meta.dir, "..")
const SKILL = join(ROOT, "packs/neko-plugin/skill/neko-plugin-dev")

describe("reuse-before-build gate", () => {
  test("SKILL.md 有第 10 条铁律且指向门禁文档", () => {
    const t = readFileSync(join(SKILL, "SKILL.md"), "utf8")
    expect(t).toContain("10. **先找现成,再自建**")
    expect(t).toContain("references/reuse-before-build.md")
  })
  test("AGENTS.md 有 R16 与 2a 前置步", () => {
    const t = readFileSync(join(SKILL, "AGENTS.md"), "utf8")
    expect(t).toContain("R16")
    expect(t).toContain("2a. 动手前先走")
  })
  test("门禁文档含三轮检索、90 天共建规则和验收三条件", () => {
    const t = readFileSync(join(SKILL, "references/reuse-before-build.md"), "utf8")
    for (const s of ["第 1 轮", "第 2 轮", "第 3 轮", "90 天", "验收标准", "权威链"])
      expect(t).toContain(s)
  })
  test("市场检索通路真实可用(联网)", async () => {
    const r = await fetch("https://market.project-neko.cn/api/v1/plugins?page=1&page_size=20")
    expect(r.ok).toBe(true)
    const d = await r.json()
    expect(d.total).toBeGreaterThan(100)
    expect(d.items.length).toBeGreaterThan(0)
    const hits = d.items.filter((x) => /待办|todo/i.test(x.name + x.short_description + (x.tags || []).join()))
    // 命中可零可多,但每项必须有可跳转字段——门禁要求报告写结论,不要求一定有命中
    for (const x of hits) { expect(typeof x.slug).toBe("string"); expect(x.repo_url).toBeTruthy() }
  })
  test("兼容快照数据在仓库里且与工具输出一致", () => {
    const p = join(ROOT, "market-corpus/compat-v2/summary.json")
    expect(existsSync(p)).toBe(true)
    const s = JSON.parse(readFileSync(p, "utf-8"))
    expect(Object.keys(s.counts).sort()).toEqual(["affected_dev", "affected_runtime", "compatible", "review", "unverified"].sort())
    const sum = Object.values(s.counts).reduce((a, x) => a + x, 0)
    expect(sum).toBe(s.total)
    expect(s.total).toBeGreaterThan(100)
  })
})
