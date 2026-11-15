import { describe, expect, test } from "bun:test"
import { readFileSync, existsSync, readdirSync } from "node:fs"
import { join, dirname } from "node:path"
import { fileURLToPath } from "node:url"

// 规则编号的唯一事实来源是 packs/neko-plugin/rules.json(issue #8)。
// 本文件只管「编号」这一件事(语料数量口径在 docs-consistency.test.ts)。
const ROOT = dirname(dirname(fileURLToPath(import.meta.url)))
const SKILL_DIR = join(ROOT, "packs", "neko-plugin", "skill", "neko-plugin-dev")
const read = (p: string) => readFileSync(p, "utf8")
const rules = JSON.parse(read(join(ROOT, "packs", "neko-plugin", "rules.json")))
const skill = read(join(SKILL_DIR, "SKILL.md"))
const agents = read(join(SKILL_DIR, "AGENTS.md"))
const readme = read(join(ROOT, "README.md"))

const hard = rules.x_hard_rules as string[]
const work = (rules.x_workflow_rules as string[]) || []
const idOf = (s: string) => String(s).match(/^[RW]\d+/)?.[0] || ""

describe("rules.json 编号唯一来源", () => {
  test("R 号从 R1 连续递增、不重复、不跳号", () => {
    expect(hard.map(idOf)).toEqual(hard.map((_, i) => "R" + (i + 1)))
  })
  test("W 号同样连续", () => {
    expect(work.map(idOf)).toEqual(work.map((_, i) => "W" + (i + 1)))
  })
  test("每条规则都写明「编号 + 正文」,没有空条目", () => {
    for (const r of [...hard, ...work]) {
      expect(r.length).toBeGreaterThan(10)
      expect(/^[RW]\d+\s+\S/.test(r), r.slice(0, 24)).toBe(true)
    }
  })
  test("历史截断/错字不再出现(extensio、孤立引号、过期范围)", () => {
    expect(hard.join("\n")).not.toContain("extensio、")
    expect(hard.join("\n")).toContain("type=extension")
    expect(skill).not.toMatch(/^\s*'?\d+\.\s.*'\s*$/m)
    expect(skill).not.toMatch(/铁律 1[–-]8/)
  })
  test("SKILL/AGENTS 引用的每个 R/W 号都在 rules.json 里存在", () => {
    const known = new Set([...hard, ...work].map(idOf).filter(Boolean))
    for (const [name, text] of [["AGENTS.md", agents], ["SKILL.md", skill]] as const) {
      for (const m of text.matchAll(/\b(R\d+|W\d+)\b/g)) {
        expect(known.has(m[1]), name + " 引用了不存在的规则号 " + m[1]).toBe(true)
      }
    }
  })
  test("AGENTS.md 的摘要条目与 rules.json 一一对应(不许漏项或另立编号)", () => {
    const listed = [...agents.matchAll(/^- (R\d+)/gm)].map((m) => m[1])
    expect(listed).toEqual(hard.map(idOf))
  })
  test("R11 双注册口径统一:AGENTS 不再写「不混用」", () => {
    expect(agents).toContain("叠加双注册")
    expect(skill).toContain("叠加双注册")
    expect(agents).not.toMatch(/@llm_tool`[^]{0,20}不混用/)
  })
  test("外发确认与外部内容两条已落到两份文档", () => {
    for (const t of [agents, skill]) {
      expect(t).toContain("逐次确认")
      expect(t).toContain("不可信")
    }
    expect(agents).toMatch(/W1/)
    expect(agents).toMatch(/W2/)
    expect(work.join(" ")).toContain("git push")
  })
  test("README 不再自称另一套编号(「15 条铁律」这类说法已废除)", () => {
    expect(readme).not.toMatch(/15 条铁律/)
    // 末号从 rules.json 取，不硬编码。上一版这里写死 R18，PR 追加 R19 之后
    // 这条测试自己就过期了——而 rules.json 明文规定「新增规则只往后追加」。
    const last = Math.max(...hard.map((r) => Number(idOf(r).slice(1))).filter((n) => n > 0))
    expect(last).toBeGreaterThan(0)
    expect(readme).toMatch(new RegExp(`R1[–-]R?${last}`))
  })
  test("rules.json 里承诺的测试文件必须真实存在(issue #13:引用与实际文件名对不上)", () => {
    const promise = String(rules.x_rule_numbering || "")
    const names = [...promise.matchAll(/test\/([A-Za-z0-9_.-]+\.ts)/g)].map((m) => m[1])
    expect(names.length).toBeGreaterThan(0)
    const have = readdirSync(join(ROOT, "test")).filter((f) => f.endsWith(".test.ts"))
    for (const n of names) {
      expect(have.includes(n), "rules.json 说由 test/" + n + " 校验,但该文件不存在").toBe(true)
      expect(existsSync(join(ROOT, "test", n))).toBe(true)
    }
  })
})
