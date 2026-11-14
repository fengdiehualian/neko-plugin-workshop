import { describe, expect, test } from "bun:test"
import { readFileSync, readdirSync, existsSync } from "node:fs"
import { join, dirname } from "node:path"
import { fileURLToPath } from "node:url"

// 对应 GitHub issue #8:规则编号与语料数量在多处各写一套,互相打脸。
// 这里把「唯一事实来源」钉死:规则以 packs/neko-plugin/rules.json 为准,
// 插件数量以 market-corpus/ 的真实目录为准;任何文档声明与事实不符就红。
const ROOT = dirname(dirname(fileURLToPath(import.meta.url)))
const SKILL_DIR = join(ROOT, "packs", "neko-plugin", "skill", "neko-plugin-dev")
const read = (p: string) => readFileSync(p, "utf8")
const rules = JSON.parse(read(join(ROOT, "packs", "neko-plugin", "rules.json")))
const skill = read(join(SKILL_DIR, "SKILL.md"))
const agents = read(join(SKILL_DIR, "AGENTS.md"))
const readme = read(join(ROOT, "README.md"))
const catalog = read(join(SKILL_DIR, "references", "market-catalog.md"))
const patterns = read(join(SKILL_DIR, "references", "market-patterns.md"))

const corpusDir = join(ROOT, "market-corpus")
const corpus = readdirSync(corpusDir, { withFileTypes: true }).filter((d) => d.isDirectory()).map((d) => d.name)
const hasToml = (slug: string) =>
  existsSync(join(corpusDir, slug, "plugin.toml")) ||
  existsSync(join(corpusDir, slug, "src", "plugin.toml")) ||
  existsSync(join(corpusDir, slug, "plugin", "plugin.toml"))
const withToml = corpus.filter(hasToml)
// 第 6 章在架索引表里的 slug(`xxx` | ...),不含文末「本机补采」那张表
const indexed = (md: string) => {
  const sec = md.split("## 6. 全量插件索引")[1] || ""
  const upto = sec.split("### 本机补采")[0]
  return [...new Set([...upto.matchAll(/^\|\s*`([a-z0-9_.-]+)`\s*\|/gmi)].map((m) => m[1]))]
}
const supplemented = [...catalog.split("### 本机补采")[1]?.split("### 附")[0]?.matchAll(/^\|\s*`([a-z0-9_.-]+)`\s*\|/gmi) || []].map((m) => m[1])

describe("规则编号:rules.json 是唯一来源", () => {
  const ids = (rules.x_hard_rules as string[]).map((r) => String(r).match(/^R(\d+)/)?.[1]).filter(Boolean)
  test("R 号从 1 连续递增、无重复", () => {
    const nums = ids.map(Number)
    expect(nums).toEqual(nums.map((_, i) => i + 1))
  })
  test("工作流规则 W 号同样连续", () => {
    const w = (rules.x_workflow_rules as string[]).map((r) => Number(String(r).match(/^W(\d+)/)?.[1]))
    expect(w).toEqual(w.map((_, i) => i + 1))
  })
  test("AGENTS/SKILL 引用的 R/W 号都存在于 rules.json", () => {
    const known = new Set([...rules.x_hard_rules, ...rules.x_workflow_rules].map((r: string) => String(r).match(/^[RW]\d+/)?.[0]).filter(Boolean))
    for (const [name, text] of [["AGENTS.md", agents], ["SKILL.md", skill]] as const) {
      for (const m of text.matchAll(/\b(R\d+|W\d+)\b/g)) {
        expect(known.has(m[1]), name + " 引用了不存在的规则号 " + m[1]).toBe(true)
      }
    }
  })
  test("AGENTS.md 摘要条数与 rules.json 一致(不许漏项)", () => {
    const listed = [...agents.matchAll(/^- (R\d+)/gm)].map((m) => m[1])
    expect(listed).toEqual(rules.x_hard_rules.map((r: string) => String(r).match(/^R\d+/)?.[0]))
  })
  test("文档不再自称另一套编号(「15 条铁律」这类说法已废除)", () => {
    expect(readme).not.toMatch(/15 条铁律/)
    expect(skill).not.toMatch(/铁律 1[–-]8/)
  })
  test("R11 双注册口径统一(SKILL 与 AGENTS 不再互相打脸)", () => {
    expect(agents).toContain("叠加双注册")
    expect(skill).toContain("叠加双注册")
    expect(agents).not.toMatch(/@llm_tool` 与 @plugin_entry 不混用/)
  })
  test("外发确认与外部内容规则落到 AGENTS/SKILL", () => {
    for (const t of [agents, skill]) {
      expect(t).toContain("逐次确认")
      expect(t).toContain("不可信")
    }
    expect(agents).toMatch(/W1/)
    expect(agents).toMatch(/W2/)
  })
})

describe("语料数量:以 market-corpus 真实目录为准", () => {
  test("事实核对:目录数 = 在架索引 + 补采,且补采项不混进索引表", () => {
    const idx = indexed(catalog)
    expect(corpus.length).toBe(idx.length + supplemented.length)
    expect(idx.filter((s) => supplemented.includes(s))).toEqual([])
    for (const slug of idx) expect(corpus.includes(slug), "索引里有目录不存在:" + slug).toBe(true)
    for (const slug of supplemented) expect(corpus.includes(slug), "补采项不存在:" + slug).toBe(true)
  })
  test("目录里带 plugin.toml 的份数与抓取说明一致", () => {
    expect(catalog).toContain(`${withToml.length} 份带 \`plugin.toml\``)
    expect(catalog).toContain(`${corpus.length} 个目录`)
  })
  test("README 与 SKILL 不再写历史 46 个的口径", () => {
    for (const [name, text] of [["README.md", readme], ["SKILL.md", skill], ["AGENTS.md", agents]] as const) {
      expect(text, name + " 里还留着「46 个」旧数量").not.toMatch(/46 个(官方|真实|在架|插件|市场)/)
    }
  })
  test("README/SKILL/AGENTS/patterns 提到的在架数量必须等于索引条数", () => {
    const n = indexed(catalog).length
    const claims: [string, string][] = [["README.md", readme], ["SKILL.md", skill], ["AGENTS.md", agents], ["market-patterns.md", patterns]]
    for (const [name, text] of claims) {
      for (const m of text.matchAll(/(\d{2,3})\s*(?:个|份)\s*(?:官方|在架|真实上架|社区)?\s*插件/g)) {
        const val = Number(m[1])
        // 允许:等于索引数、等于带清单份数、或明显是别的东西(条目数/规则数等小数字)
        const ok = val === n || val === withToml.length || val < 40
        expect(ok, `${name} 写「${m[0].trim()}」,与事实(索引 ${n} / 有清单 ${withToml.length})不符`).toBe(true)
      }
    }
  })
})
