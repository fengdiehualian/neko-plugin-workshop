import { describe, expect, test } from "bun:test"
import { createHash } from "node:crypto"
import { existsSync, readFileSync, statSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

// 先搜后建(reuse-before-build)接入线:正典只在 market-corpus 里那一份。
// 本测试守三件事——接入线指向的路径真在、版本与锚点没漂、第二份副本没回潮。
const ROOT = dirname(dirname(fileURLToPath(import.meta.url)))
const SKILL_DIR = join(ROOT, "packs", "neko-plugin", "skill", "neko-plugin-dev")
const WIRE = join(SKILL_DIR, "references", "reuse-before-build.md")
const CORPUS = join(ROOT, "market-corpus", "reuse_before_build", "src")
const CORPUS_SKILL = join(CORPUS, "skills", "reuse-before-build", "SKILL.md")
const pluginToml = join(CORPUS, "plugin.toml")

const read = (p: string) => readFileSync(p, "utf8")
// 语料按 LF 存储,但 Windows 检出会带 CRLF:统一规范化后再算,CI 与本地结论一致
const sha256LF = (p: string) =>
  createHash("sha256").update(read(p).replace(/\r\n/g, "\n")).digest("hex")
const tomlField = (key: string) => {
  const m = new RegExp("^" + key + '\\s*=\\s*"([^"]+)"', "m").exec(read(pluginToml))
  return m ? m[1] : null
}

describe("先搜后建接入线:正典只读语料那一份", () => {
  test("接入线文档存在,并指向语料里的唯一副本", () => {
    expect(existsSync(WIRE), "缺少 references/reuse-before-build.md").toBe(true)
    const t = read(WIRE)
    expect(t.includes("market-corpus/reuse_before_build"), "接入线没写语料路径").toBe(true)
    expect(t.includes("reuse-before-build/SKILL.md"), "接入线没写正典文件名").toBe(true)
    expect(t.includes("1-dcjiang/neko-reuse-before-build"), "接入线没写上游仓库(安装版要靠它)").toBe(true)
  })

  test("语料与插件清单真在,不是空壳引用", () => {
    expect(existsSync(pluginToml), "语料缺 plugin.toml:" + pluginToml).toBe(true)
    expect(existsSync(CORPUS_SKILL), "语料缺正典 SKILL.md:" + CORPUS_SKILL).toBe(true)
    expect(read(pluginToml).length > 0 && read(CORPUS_SKILL).length > 0, "语料文件为空").toBe(true)
  })

  test("slug 与版本和接入线一致", () => {
    expect(tomlField("id"), "plugin.toml 的 id 变了").toBe("reuse_before_build")
    const version = tomlField("version")
    expect(version, "plugin.toml 没有 version").toBeTruthy()
    expect(read(WIRE).includes("`" + version + "`"), "接入线写的版本与语料不符:语料是 " + version).toBe(true)
  })

  test("完整性锚点等于语料实际内容(防静默漂移)", () => {
    const m = /\b([0-9a-f]{64})\b/.exec(read(WIRE))
    expect(m, "接入线里没有 sha256 锚点").toBeTruthy()
    const anchor = m ? m[1] : ""
    expect(sha256LF(CORPUS_SKILL), "语料 SKILL.md 的 sha256 与接入线锚点不符:改了语料就要同步本文件").toBe(anchor)
  })

  test("没有第二份 rbb 副本回潮", () => {
    expect(existsSync(join(SKILL_DIR, "references", "rbb")), "references/rbb/ 再现:应只留语料一份").toBe(false)
    expect(existsSync(join(SKILL_DIR, "references", "reuse-before-build.neko.md")), "本地化副本再现").toBe(false)
    expect(existsSync(join(ROOT, "workbench", "vendor_rbb.cjs")), "vendor_rbb.cjs 再现").toBe(false)
    expect(existsSync(join(ROOT, "workbench", "localize_rbb.cjs")), "localize_rbb.cjs 再现").toBe(false)
  })

  test("接入线是说明不是正典抄件", () => {
    const size = statSync(WIRE).size
    expect(size < 6000, "接入线 " + size + " 字节,疑似把正典(18470 字节)抄进来了").toBe(true)
  })
})
