import { describe, expect, test } from "bun:test"
import { readFileSync, existsSync, readdirSync } from "node:fs"
import { join, dirname } from "node:path"
import { fileURLToPath } from "node:url"
import { walkMarkdown, THIRD_PARTY_DIRS } from "./refs"

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
  test("README/SKILL/AGENTS 不再抄写条数或末号范围(issue #16/#19:抄第二遍必然过期)", () => {
    expect(readme).not.toMatch(/15 条铁律/)
    for (const [name, text] of [["README.md", readme], ["SKILL.md", skill], ["AGENTS.md", agents],
                                ["packs/README.md", read(join(ROOT, "packs", "neko-plugin", "README.md"))]] as const) {
      // 判据是「用连字符把两个编号连起来」,不是「从 R1 开始」。
      // 原来只写 R1[–-]R?\d+,于是 R2–R3 和所有 W 范围都漏了;
      // 而且这里照抄了代码里的注释过滤(排除 # 开头行),可被测的都是 markdown——
      // `## 对外动作与外部内容(工坊硬规则 W1–W3)` 这种标题行被当成注释跳过了整整一版。
      const ranges = text.split(/\r?\n/).filter((l) => /\b[RW]\d+\s*[–—\-]\s*[RW]?\d+/.test(l))
      expect(ranges, name + " 里还写着规则范围:" + ranges.join(" | ")).toEqual([])
      expect(text, name + " 里还写着「N 条硬规则」").not.toMatch(/\d+\s*条(硬)?规则/)
    }
    // 反过来:真正的事实来源仍然要有内容,测试不能靠「文档什么都不写」蒙过
    expect(hard.length).toBeGreaterThan(10)
  })
  test("plugin_conformance 用 C 命名空间,与 rules.json 的 R 号互不指代(issue #19)", () => {
    const py = read(join(ROOT, "tools", "plugin_conformance.py"))
    const code = py.split(/\r?\n/).filter((l) => !/^\s*#/.test(l)).join("\n")
    const ids = [...code.matchAll(/\b([RC])(\d+)\b/g)]
    const rIds = ids.filter((m) => m[1] === "R").map((m) => m[0])
    expect(rIds, "检查项又用回 R 号了:" + rIds.join(",")).toEqual([])
    const cIds = [...new Set(ids.filter((m) => m[1] === "C").map((m) => Number(m[2])))].sort((a, b) => a - b)
    expect(cIds, "C 号必须从 C1 连续").toEqual(cIds.map((_, i) => i + 1))
    expect(cIds.length).toBeGreaterThanOrEqual(10)
    // 文档里引用它时也必须说 C 号(以前 market-catalog 写「17 条规则」,既不对也不是同一套编号)
    const cat = read(join(SKILL_DIR, "references", "market-catalog.md"))
    const claim = [...cat.matchAll(/C1[–\-]C(\d+)[^。\n]{0,40}?(\d+)\s*项/g)].pop()
    if (claim) expect(Number(claim[1])).toBe(cIds.length)
    expect(cat).toMatch(/conformance 命名空间|与 `rules\.json` 的 R 号规则不是同一套编号/)
  })
  // issue #19 的待办是「等 forge/、deep-dive/ 进仓时把扫描目录加进去」。按目录名点名,
  // 每加一个子目录就得改一次测试,漏一次就是永久空档——#19 关不掉正是因为这个。
  // 改成递归扫 references/ 下所有 md(公共实现在 test/refs.ts):
  // 以后谁再往里放子目录都不用动测试。第三方逐字节副本除外,那份我们改不了。
  test("references 下所有文档引用的 R/W 号都真实存在(issue #19:范围不能只盯着 SKILL/AGENTS)", () => {
    const files = walkMarkdown(join(SKILL_DIR, "references"), THIRD_PARTY_DIRS)
    expect(files.length, "references/ 一个 md 都没扫到,路径写错了").toBeGreaterThan(10)
    const known = new Set([...hard, ...work].map(idOf).filter(Boolean))
    for (const f of files) {
      const rel = f.slice(ROOT.length + 1).replace(/\\/g, "/")
      const text = read(f)
      for (const m of text.matchAll(/\b(R\d+|W\d+)\b/g)) {
        expect(known.has(m[1]), rel + " 引用了不存在的规则号 " + m[1]).toBe(true)
      }
      // 范围写法一律禁止。别只在 R1 上判:R2–R3 同样是把编号固化成范围,
      // W 范围也是——判据是「有没有用连字符把两个编号连起来」,不是从哪个号开始。
      const ranges = text.split(/\r?\n/).filter((l) => /\b[RW]\d+\s*[–—\-]\s*[RW]?\d+/.test(l))
      expect(ranges, rel + " 里还写着规则范围:" + ranges.join(" | ")).toEqual([])
      expect(text, rel + " 里还写着「N 条硬规则」").not.toMatch(/\d+\s*条(硬)?规则/)
    }
  })

  test("rules.json 自己写明了命名空间约定", () => {
    const promise = String(rules.x_rule_numbering || "")
    expect(promise, "x_rule_numbering 没写 C 命名空间,下一个改动又会把两套编号混起来").toMatch(/C1[–\-]C14|conformance/)
    expect(promise, "没写明「不许抄条数/页数」的约定").toMatch(/不(要|许)?抄|不写死|以 rules\.json|运行时统计/)
  })
  test("rules.json 里承诺的测试文件必须真实存在(issue #13:引用与实际文件名对不上)", () => {
    const promise = String(rules.x_rule_numbering || "")
    const names = [...promise.matchAll(/test\/([A-Za-z0-9_.-]+\.ts)/g)].map((m) => m[1])
    expect(names.length).toBeGreaterThan(0)
    // 只认 .test.ts 会把 helper 判成缺失:rules.json 现在既提测试文件,也提
    // test/refs.ts(公共 walk)。承诺的对象是「这个文件真的在」,不是「它必须是测试」。
    const have = readdirSync(join(ROOT, "test"))
    for (const n of names) {
      expect(have.includes(n), "rules.json 说由 test/" + n + " 校验,但该文件不存在").toBe(true)
      expect(existsSync(join(ROOT, "test", n))).toBe(true)
    }
  })
})
