import { describe, expect, test } from "bun:test"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

// neko-plugin-dev 深度专题的接入线:正文不进仓库,只留「钉版本 + 哈希」。
//
// 这条测试守的是三件容易悄悄坏掉的事:
//   1. 锁与 pin 各写一遍钉版信息,漂了就等于「校验通过」指向了另一个 commit;
//   2. 有人图省事把正文复制回 references/,从此两份各自漂移,而又都不在门禁里;
//   3. 许可声明漏了上游,安装包里却在分发别人的文字。
const ROOT = dirname(dirname(fileURLToPath(import.meta.url)))
const SKILL_DIR = join(ROOT, "packs", "neko-plugin", "skill", "neko-plugin-dev")
const REF = join(SKILL_DIR, "references")
const CORPUS = join(ROOT, "market-corpus")
const requireCjs = createRequire(import.meta.url)
const locklib = requireCjs(join(ROOT, "workbench", "corpus-lock.cjs"))

const pin = JSON.parse(readFileSync(join(REF, "neko-plugin-dev-pin.json"), "utf8"))
const lock = JSON.parse(readFileSync(join(CORPUS, "corpus.lock.json"), "utf8"))
const entry = lock.entries[pin.slug]
const skill = readFileSync(join(SKILL_DIR, "SKILL.md"), "utf8")
const notices = readFileSync(join(ROOT, "THIRD_PARTY_NOTICES.md"), "utf8")

/** 语料是否已经拉到本地(开发工作区可能拉过,CI/装机版通常没有)。 */
const corpusRoot = join(CORPUS, pin.slug, "src")
const hasCorpus = existsSync(corpusRoot)

describe("neko-plugin-dev 接入线:锁与 pin 一致", () => {
  test("锁里有这条,且钉的是完整 SHA(不钉 tag)", () => {
    expect(entry, "锁里没有 " + pin.slug).toBeTruthy()
    expect(pin.commit, "commit 必须是 40 位完整 SHA").toMatch(/^[0-9a-f]{40}$/)
    expect(pin.ref, "ref 必须与 commit 同值:tag 可被移动,钉版本只认 SHA").toBe(pin.commit)
    expect(entry.commit).toBe(pin.commit)
    expect(entry.ref).toBe(pin.ref)
  })

  test("逐字段一致:source / kind / version / 哈希 / 文件数", () => {
    for (const k of ["source", "kind", "version", "version_source", "transport",
      "archive_format", "archive_sha256", "computedHash", "files"]) {
      expect(entry[k], "锁与 pin 的 " + k + " 不一致").toEqual(pin[k])
    }
    expect(pin.version_source, "版本必须来自可解析的文件").toBe("VERSION.txt")
    expect(pin.version).toMatch(/^\d+\.\d+\.\d+$/)
  })

  test("anchors 与锁条目逐条一致", () => {
    expect(pin.anchors).toEqual(entry.anchors)
  })

  test("按需条目:正文不在仓库里,标注 vendored:false 且不进市场插件口径", () => {
    expect(pin.vendored).toBe(false)
    expect(entry.vendored).toBe(false)
    expect(pin.kind).toBe("skill")
    expect(pin.corpus_root).toBe("market-corpus/" + pin.slug + "/src")
    expect(existsSync(join(REF, "deep-dive")), "正文又被复制回 references/ 了:只该留钉版").toBe(false)
  })

  test("拉取与校验入口都指向真实存在的脚本", () => {
    for (const rel of ["workbench/pull_skill_docs.cjs", "workbench/pull_pinned.cjs",
      "workbench/fetch_corpus.cjs"]) {
      expect(existsSync(join(ROOT, ...rel.split("/"))), "缺 " + rel).toBe(true)
    }
    expect(pin.pull).toContain("pull_skill_docs.cjs")
    expect(pin.verify).toContain("--locked")
  })
})

describe("neko-plugin-dev 接入线:已被拉到本地时的核对", () => {
  test("entry 里的路径与锚点都真的在语料里", () => {
    if (!hasCorpus) {
      // 按需条目的常态:仓库里没有正文。这里不跳过断言,而是明确记一笔,
      // 免得日后有人把「没校验」误读成「校验通过」。
      expect(hasCorpus, "语料未落盘(按需条目的常态);字节级校验用 " + pin.pull).toBe(false)
      return
    }
    for (const [k, rel] of Object.entries(pin.entry as Record<string, string>)) {
      const p = join(corpusRoot, ...rel.split("/"))
      expect(existsSync(p), "pin 的 entry." + k + " 指向不存在的路径:" + rel).toBe(true)
    }
    // anchors.file 相对条目根(与锁里既有条目一致,带 src/ 前缀);
    // entry 里的路径相对已过滤的 src 根,两者口径不同,别混用。
    expect(locklib.checkAnchors(join(CORPUS, pin.slug), pin.anchors)).toEqual([])
    const measured = locklib.treeHash(corpusRoot)
    expect(measured.computedHash, "本地语料与钉版不符,用 " + pin.pull + " 重拉").toBe(pin.computedHash)
  })
})

describe("neko-plugin-dev 接入线:文档与许可", () => {
  test("SKILL.md 写明按需拉取,而不是复制正文", () => {
    expect(skill.includes("neko-plugin-dev-pin.json"), "SKILL.md 没提钉版文件").toBe(true)
    expect(skill.includes("pull_skill_docs.cjs"), "SKILL.md 没给拉取入口").toBe(true)
    expect(/不进仓库|按需拉取/.test(skill), "SKILL.md 没说清正文不进仓库").toBe(true)
  })

  test("第三方声明里有上游、许可与钉住的版本", () => {
    expect(notices).toContain("HMUG12/Project_Neko-plugin_skill")
    expect(notices).toMatch(/MIT/)
    expect(notices).toContain(pin.commit)
    expect(notices).toContain(pin.version)
    expect(notices, "要写明不随包分发").toMatch(/不随包|不进安装包|不打包/)
  })
})
