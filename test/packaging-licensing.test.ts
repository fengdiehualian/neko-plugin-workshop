import { describe, expect, test } from "bun:test"
import { createHash } from "node:crypto"
import { readFileSync, readdirSync, statSync, existsSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

// 对应 GitHub issue #16(出处与许可)、#17(第三方声明)、#18(打包口径)、#21(语料出处)。
// 起因是实测发现:`references/market-examples/` 里三份文件与 market-corpus 逐字节相同、许可「未声明」,
// 却跟着每个安装包分发;而 experts/neko-plugin-ops 的文本自陈吸收自两个 MIT 上游,声明里一个字都没提。
// 「声明与事实相符」必须由测试盯着,不然下一轮改动又会飘回去。
const ROOT = dirname(dirname(fileURLToPath(import.meta.url)))
const read = (p: string) => readFileSync(join(ROOT, p), "utf8")
const sha = (p: string) => createHash("sha256").update(readFileSync(p)).digest("hex")
const SKILL = "packs/neko-plugin/skill/neko-plugin-dev"
const EXAMPLES = ["shell_cmd", "sys_monitor", "tavily_search"]

describe("第三方副本的打包口径(issue #16/#18)", () => {
  const attrs = read(".gitattributes")
  const ME = SKILL + "/references/market-examples"
  test("market-examples 的 export-ignore 两行都在(只写一行等于没写)", () => {
    expect(attrs.includes(ME + " export-ignore"), "缺「目录本身」那一行").toBe(true)
    expect(attrs.includes(ME + "/** export-ignore"), "缺「目录内容」那一行").toBe(true)
  })
  test("出包脚本与包级复核都把它当禁含项(光靠注释不算闸)", () => {
    const bp = read("workbench/build_payload.py")
    expect(bp, "build_payload 的 FORBIDDEN 里没有 market-examples").toMatch(/FORBIDDEN\s*=\s*\[[\s\S]{0,1200}market-examples\//)
    const cp = read("workbench/check_package.ps1")
    expect(cp, "check_package 的 $forbidden 里没有 market-examples").toMatch(/\$forbidden\s*=\s*@\([^\)]*market-examples\//)
  })
  test("SKILL.md 索引如实说明装机版没有它", () => {
    const sk = read(SKILL + "/SKILL.md")
    const row = sk.split(/\r?\n/).find((l) => l.includes("`market-examples/`") && l.startsWith("|")) || ""
    expect(row, "索引里没有 market-examples 那一行").not.toBe("")
    expect(row, "索引必须写明安装包不携带").toMatch(/安装包不携带|不随包|只在开发仓/)
  })
  test("这三份确实是 market-corpus 的逐字节副本(所以「本仓库文档」的说法不成立)", () => {
    for (const slug of EXAMPLES) {
      const localDir = join(ROOT, SKILL, "references", "market-examples", slug)
      const corpusDir = join(ROOT, "market-corpus", slug)
      if (!existsSync(localDir) || !existsSync(corpusDir)) continue // 新克隆没取语料时不硬断言
      const rels: string[] = []
      const walk = (d: string, base: string) => {
        for (const e of readdirSync(d, { withFileTypes: true })) {
          const r = base ? base + "/" + e.name : e.name
          if (e.isDirectory()) walk(join(d, e.name), r)
          else rels.push(r)
        }
      }
      walk(localDir, "")
      expect(rels.length, slug + " 副本是空的?").toBeGreaterThan(0)
      for (const rel of rels) {
        const a = join(localDir, ...rel.split("/"))
        const cands = [join(corpusDir, ...rel.split("/")), join(corpusDir, "src", ...rel.split("/"))].filter(existsSync)
        if (!cands.length) continue
        expect(sha(a), `${slug}/${rel} 与语料不一致(那就不是副本,声明要重写)`).toBe(sha(cands[0]))
      }
    }
  })
})

describe("第三方文本的许可声明(issue #17/#18)", () => {
  const doc = read("THIRD_PARTY_NOTICES.md")
  const notices = JSON.parse(read("workbench/notices.json"))
  test("第 4 节列出副本与专家团,并写明处理方式", () => {
    expect(doc).toMatch(/## 4\. 仓库里的第三方文本与副本/)
    for (const slug of EXAMPLES) expect(doc, "缺 " + slug).toContain(`market-examples/${slug}`)
    expect(doc).toMatch(/msitarzewski\/agency-agents/)
    expect(doc).toMatch(/jnMetaCode\/agency-agents-zh/)
    // 不能一边说不分发、一边又在第 5 节把 references/** 整片算本仓库 MIT
    const s5 = doc.split("## 5. 本仓库生成物")[1] || ""
    // 允许写 references/**,但必须带排除条款;光有「不含第 4 节」这句话才算口径闭合
    expect(s5.slice(0, 1500), "第 5 节没写明「不含第 4 节所列第三方副本/派生文本」").toMatch(/不含第 4 节|第 4 节那两类不归在这里/)
  })
  test("notices.json 的 vendored 与磁盘实算哈希一致(声明不许抄旧值)", () => {
    const v = notices.vendored
    expect(v, "notices.json 缺 vendored 段").toBeTruthy()
    expect(v.experts.upstreams.length).toBe(2)
    for (const f of v.experts.files) {
      const p = join(ROOT, "experts", "neko-plugin-ops", ...f.path.split("/"))
      expect(existsSync(p), "声明里列的文件不存在:" + f.path).toBe(true)
      expect(sha(p), f.path + " 的哈希与声明不一致(改了没重跑生成器?)").toBe(f.sha256)
    }
    expect(v.market_examples.length).toBe(EXAMPLES.filter((s) => existsSync(join(ROOT, SKILL, "references", "market-examples", s))).length)
  })
})

describe("语料出处登记(issue #21)", () => {
  const fc = read("workbench/fetch_corpus.cjs")
  test("抓取时记下实际 ref 与 zip 哈希,不再只写「抓了 main」", () => {
    expect(fc).toMatch(/SOURCES\.json/)
    expect(fc, "没把 ref 记下来,分支一动就无法复现").toMatch(/ref:\s*"refs\/heads\/"\s*\+\s*branch/)
    expect(fc, "没记 zip 哈希").toMatch(/zip_sha256:\s*sha256buf\(buf\)/)
    expect(fc, "既有目录也要留「来源未记录」的说明,免得看起来像已核对过").toMatch(/本机既有/)
    expect(fc, "跑完要落盘").toMatch(/saveSources\(sources\)/)
  })
})
