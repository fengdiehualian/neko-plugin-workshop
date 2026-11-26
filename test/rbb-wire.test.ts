import { describe, expect, test } from "bun:test"
import { createRequire } from "node:module"
import { existsSync, lstatSync, readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

// 先搜后建(reuse-before-build)接入线:正典只在上游,本仓库只留「钉版本 + 哈希」。
//
// 这条测试守的是四件容易悄悄坏掉的事:
//   1. 锁与 rbb-pin 各写一遍钉版信息,漂了就等于「校验通过」指向了另一个 commit;
//   2. 有人把上游正文又复制回 market-corpus/reuse_before_build/src/,
//      从此仓库里多一份会各自漂移的正文;
//   3. 接入线文档还指着旧的本地正典路径,装机版照着读必然读不到;
//   4. 许可声明漏了上游,而引用/分发时又需要保留 MIT 署名。
const ROOT = dirname(dirname(fileURLToPath(import.meta.url)))
const SKILL_DIR = join(ROOT, "packs", "neko-plugin", "skill", "neko-plugin-dev")
const WIRE = join(SKILL_DIR, "references", "reuse-before-build.md")
const CORPUS = join(ROOT, "market-corpus")
const requireCjs = createRequire(import.meta.url)
const locklib = requireCjs(join(ROOT, "workbench", "corpus-lock.cjs"))

const pin = JSON.parse(readFileSync(join(SKILL_DIR, "references", "rbb-pin.json"), "utf8"))
const lock = JSON.parse(readFileSync(join(CORPUS, "corpus.lock.json"), "utf8"))
const entry = lock.entries[pin.slug]
const wire = readFileSync(WIRE, "utf8")
const notices = readFileSync(join(ROOT, "THIRD_PARTY_NOTICES.md"), "utf8")
const ignore = readFileSync(join(ROOT, ".gitignore"), "utf8")

/** 语料是否已经拉到本地(开发工作区可能拉过,CI/装机版通常没有)。 */
const corpusRoot = join(CORPUS, pin.slug, "src")
const hasCorpus = (() => {
  try {
    lstatSync(corpusRoot)
    return true
  } catch (e) {
    // 只有真正不存在才是「未拉取」;路径被文件占住(ENOTDIR)、无权限(EACCES)
    // 都属于仓库/工作区坏了,必须让测试失败,不能静默跳过正文校验。
    if ((e as NodeJS.ErrnoException).code === "ENOENT") return false
    throw e
  }
})()

describe("先搜后建接入线:锁与 pin 一致", () => {
  test("锁里有这条,且钉的是完整 SHA", () => {
    expect(entry, "锁里没有 " + pin.slug).toBeTruthy()
    expect(pin.commit, "commit 必须是 40 位完整 SHA").toMatch(/^[0-9a-f]{40}$/)
    expect(pin.commit).toBe("32cdf400e6857652496aae455b68e5766cdfb877")
    expect(pin.ref, "ref 是给人看的 tag;抓取仍以 commit 为准").toBe("v0.3.23")
    expect(entry.commit).toBe(pin.commit)
    expect(entry.ref).toBe(pin.ref)
  })

  test("逐字段一致:source / kind / version / 哈希 / 文件数", () => {
    for (const k of ["source", "kind", "version", "version_source", "transport",
      "archive_format", "archive_sha256", "computedHash", "files", "vendored"]) {
      expect(entry[k], "锁与 pin 的 " + k + " 不一致").toEqual(pin[k])
    }
    expect(pin.version_source, "版本必须来自可解析的文件").toBe("plugin.toml")
    expect(pin.version).toMatch(/^\d+\.\d+\.\d+$/)
  })

  test("anchors 与锁条目逐条一致", () => {
    expect(pin.anchors).toEqual(entry.anchors)
  })

  test("按需条目:正文不在仓库里,标注 vendored:false", () => {
    expect(pin.vendored).toBe(false)
    expect(entry.vendored).toBe(false)
    expect(pin.corpus_root).toBe("market-corpus/" + pin.slug + "/src")
    expect(pin.pull).toContain("pull_rbb.cjs")
    expect(pin.verify).toContain("--only")
    expect(pin.verify).toContain("--verify-only")
  })

  test("发布镜像信息与锁一致(许可核对靠它,不许两处各写一套)", () => {
    expect(pin.release).toEqual(entry.release)
  })

  test("按需语料的落盘目录被 .gitignore 挡住(回迁是悄悄发生的)", () => {
    expect(ignore).toMatch(new RegExp("^market-corpus/" + pin.slug + "/?\\s*$", "m"))
  })

  test("拉取与校验入口都指向真实存在的脚本", () => {
    for (const rel of ["workbench/pull_rbb.cjs", "workbench/pull_pinned.cjs",
      "workbench/fetch_corpus.cjs"]) {
      expect(existsSync(join(ROOT, ...rel.split("/"))), "缺 " + rel).toBe(true)
    }
  })
})

describe("先搜后建接入线:文档与许可", () => {
  test("接入线文档存在,并指向 pin 与拉取入口", () => {
    expect(existsSync(WIRE), "缺少 references/reuse-before-build.md").toBe(true)
    expect(wire).toContain("rbb-pin.json")
    expect(wire).toContain("pull_rbb.cjs")
    expect(wire).toContain("1-dcjiang/neko-reuse-before-build")
    expect(wire).toContain("market-corpus/reuse_before_build")
    expect(wire.includes("`" + pin.version + "`"), "接入线写的版本与 pin 不符").toBe(true)
  })

  test("接入线是说明不是正典抄件", () => {
    expect(wire.length, "接入线 " + wire.length + " 字节,疑似把正典抄进来了").toBeLessThan(6000)
  })

  test("没有第二份 rbb 副本回潮", () => {
    expect(existsSync(join(SKILL_DIR, "references", "rbb")), "references/rbb/ 再现:应只留上游一份").toBe(false)
    expect(existsSync(join(SKILL_DIR, "references", "reuse-before-build.neko.md")), "本地化副本再现").toBe(false)
    expect(existsSync(join(ROOT, "workbench", "vendor_rbb.cjs")), "vendor_rbb.cjs 再现").toBe(false)
    expect(existsSync(join(ROOT, "workbench", "localize_rbb.cjs")), "localize_rbb.cjs 再现").toBe(false)
  })

  test("第三方声明里有上游、许可、钉住的版本,并写明不随包", () => {
    expect(notices).toContain("1-dcjiang/neko-reuse-before-build")
    expect(notices).toMatch(/MIT/)
    expect(notices).toContain(pin.commit)
    expect(notices).toContain(pin.version)
    expect(notices, "要写明不随包分发").toMatch(/不随包|不进安装包|不打包/)
  })
})

describe("先搜后建接入线:已被拉到本地时的核对", () => {
  test("entry 里的路径与锚点都真的在语料里", () => {
    if (!hasCorpus) {
      // 按需条目在 CI / 装机版的常态:仓库里没有正文。
      expect(pin.vendored, "语料不在仓库里,但 pin 没标 vendored:false:离线校验会误判缺失").toBe(false)
      return
    }
    for (const [k, rel] of Object.entries(pin.entry as Record<string, string>)) {
      if (rel.endsWith("/")) {
        expect(existsSync(join(corpusRoot, ...rel.split("/"))), "pin 的 entry." + k + " 指向不存在的目录:" + rel).toBe(true)
      } else {
        expect(existsSync(join(corpusRoot, ...rel.split("/"))), "pin 的 entry." + k + " 指向不存在的路径:" + rel).toBe(true)
      }
    }
    expect(locklib.checkAnchors(join(CORPUS, pin.slug), pin.anchors)).toEqual([])
    const measured = locklib.treeHash(corpusRoot)
    expect(measured.computedHash, "本地语料与钉版不符。先删除 market-corpus/" + pin.slug + "/src,再用 " + pin.pull + " 重拉").toBe(pin.computedHash)
  })
})
