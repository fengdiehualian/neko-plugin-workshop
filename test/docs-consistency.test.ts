import { describe, expect, test } from "bun:test"
import { readFileSync, readdirSync, existsSync } from "node:fs"
import { join, dirname } from "node:path"
import { fileURLToPath } from "node:url"

// 对应 GitHub issue #8:语料数量在多处各写一套,互相打脸。
// 规则编号的一致性在 test/rules-numbering.test.ts;本文件只管「数量口径」:
// 一切以 market-corpus/ 的真实目录与索引表为准,文档里写的数字必须等于事实。
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
