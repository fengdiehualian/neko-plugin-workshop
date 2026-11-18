import { describe, expect, test } from "bun:test"
import { createRequire } from "node:module"
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

// 语料锁(corpus.lock v0)的守则:锁里的哈希必须与磁盘上那份语料逐字节对得上。
// 这条测试的价值不在「今天能过」,而在「有人手改语料 / 改过滤规则时不吭声地过」——
// 那时 CI 会红,而不是等下次重抓才发现语料已经不是锁里写的那一份。
const ROOT = dirname(dirname(fileURLToPath(import.meta.url)))
const requireCjs = createRequire(import.meta.url)
const lock = requireCjs(join(ROOT, "workbench", "corpus-lock.cjs"))

const CORPUS = join(ROOT, "market-corpus")

const withTempTree = (files: Record<string, string | Buffer>, fn: (root: string) => void) => {
  const root = mkdtempSync(join(tmpdir(), "corpuslock-"))
  try {
    for (const [rel, body] of Object.entries(files)) {
      const p = join(root, ...rel.split("/"))
      mkdirSync(dirname(p), { recursive: true })
      writeFileSync(p, body)
    }
    fn(root)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
}

describe("语料锁:锁值与磁盘一致", () => {
  test("每条的 computedHash / files 都与 market-corpus 里的实际文件对得上", () => {
    const parsed = lock.loadLock(CORPUS)
    const slugs = Object.keys(parsed.entries)
    expect(slugs.length, "锁里一条都没有").toBeGreaterThan(0)
    for (const slug of slugs) {
      const entry = parsed.entries[slug]
      const measured = lock.treeHash(join(CORPUS, slug, "src"))
      expect(measured, slug + " 的语料目录不存在").not.toBeNull()
      expect(measured.files, slug + " 的文件数与锁不符").toBe(entry.files)
      expect(measured.computedHash, slug + " 的字节与锁不符(改过语料就重跑 --update)").toBe(entry.computedHash)
    }
  })

  test("每条都记了 repo / commit / ref / tree / 版本来源", () => {
    const parsed = lock.loadLock(CORPUS)
    for (const [slug, entry] of Object.entries(parsed.entries)) {
      expect(lock.SLUG_RE.test(slug), slug + " 不是合法 slug").toBe(true)
      expect(entry.source, slug + " 缺 source").toMatch(/^[\w.-]+\/[\w.-]+$/)
      expect(entry.ref, slug + " 缺 ref").toBeTruthy()
      expect(entry.commit, slug + " 缺 commit").toMatch(/^[0-9a-f]{40}$/)
      expect(entry.computedHash, slug + " 缺 computedHash").toMatch(/^[0-9a-f]{64}$/)
      expect(entry.archive_sha256, slug + " 缺 archive_sha256").toMatch(/^[0-9a-f]{64}$/)
      expect(entry.version_source, slug + " 没记版本是哪个文件读出来的").toBeTruthy()
      expect(Array.isArray(entry.anchors), slug + " 缺 anchors").toBe(true)
    }
  })

  test("rbb 锁钉在 v0.3.22，anchors 覆盖 id / 正典块 / 规则口径", () => {
    const parsed = lock.loadLock(CORPUS)
    const entry = parsed.entries.reuse_before_build
    expect(entry, "锁里没有 reuse_before_build").toBeTruthy()
    expect(entry.ref).toBe("v0.3.22")
    expect(entry.commit).toBe("a375629a9a8b96af18809107aa7202c68c1f104d")
    expect(entry.version).toBe("0.3.22")
    expect(entry.version_source).toBe("plugin.toml")
    expect(entry.release?.asset).toBe("neko-reuse-before-build-v0.3.22-vendor.zip")
    expect(entry.release?.asset_sha256).toBe("ac0d31f21d95d3b12af3fb860795e8507d8f8e122e3ddfa9dbb0d71204c87310")
    expect(entry.release?.checksums_sha256).toBe("06ec74ac178586572b2884424a4103b9b0b9a06c106414d8e0b82e83e801b323")
    expect(entry.release?.tree_scope).toBe("vendor")

    const byId = Object.fromEntries(entry.anchors.map((a: { id: string }) => [a.id, a]))
    expect(byId["plugin-manifest"].all).toContain('id = "reuse_before_build"')
    expect(byId["skill-identity"].all).toEqual(expect.arrayContaining([
      "RBB:INVARIANTS:BEGIN",
      "RBB:INVARIANTS:END",
    ]))
    expect(byId["rule-numbering"].all).toEqual(expect.arrayContaining(["RULE_RE", "MULTILINE"]))
  })

  test("forge 锁钉在 v2.8.0 / 722529a，来源是 git + tar 归档", () => {
    const parsed = lock.loadLock(CORPUS)
    const entry = parsed.entries["plugin-forge"]
    expect(entry, "锁里没有 plugin-forge").toBeTruthy()
    // kind 是新增值:现有代码不读它,但写清楚「这是工具仓不是市场插件」
    expect(entry.kind).toBe("forge")
    expect(entry.source_type).toBe("git")
    expect(entry.ref).toBe("v2.8.0")
    expect(entry.commit).toBe("722529a5fb439e3972a4d42f1f7b7d3ea17985ab")
    expect(entry.version).toBe("2.8.0")
    // forge 的版本只写在 .claude-plugin/plugin.json,读不到就会静默漂
    expect(entry.version_source).toBe("plugin.json")
    // git 来源记的是 tar 流哈希,不是 codeload zip:两者字节不同,不能混为一谈
    expect(entry.archive_format).toBe("tar")
    expect(entry.files).toBe(80)
    // 工具仓没有市场发布镜像,release 块不该出现在这条上
    expect(entry.release, "forge 不该有 release 镜像块").toBeUndefined()

    const byId = Object.fromEntries(entry.anchors.map((a: { id: string }) => [a.id, a]))
    expect(byId["plugin-manifest"].all).toContain('"name": "plugin-forge"')
    expect(byId["skill-identity"].all).toContain("name: plugin-forge")
    expect(byId["rbb-pin"].any).toContain("v0.3.22")
    expect(byId["audit-ledger"].any).toContain("sec-declared")
  })

  test("老条目没有 archive_format，按 zip 理解(向后兼容)", () => {
    const parsed = lock.loadLock(CORPUS)
    const rbb = parsed.entries.reuse_before_build
    expect(rbb.source_type ?? "zip").toBe("zip")
    expect(rbb.archive_format ?? "zip").toBe("zip")
  })

  test("锚点指向的文件都在语料里(锚点失效要当场报,不能等 --locked 才炸)", () => {
    const parsed = lock.loadLock(CORPUS)
    for (const [slug, entry] of Object.entries(parsed.entries)) {
      const missing = lock.checkAnchors(join(CORPUS, slug), entry.anchors)
      expect(missing, slug + " 锚点缺失:" + missing.join(", ")).toEqual([])
    }
  })
})

describe("语料锁:树哈希口径", () => {
  test("按字节算,不做 CRLF 归一(同一份内容换行不同必须不同哈希)", () => {
    let lf = ""
    let crlf = ""
    withTempTree({ "a.md": "x\ny\n" }, (root) => { lf = lock.treeHash(root).computedHash })
    withTempTree({ "a.md": "x\r\ny\r\n" }, (root) => { crlf = lock.treeHash(root).computedHash })
    expect(lf).not.toBe(crlf)
  })

  test("路径排序按 UTF-8 字节序,与 locale 无关", () => {
    // 'Z' < 'a' < 'é'(0x5A / 0x61 / 0xC3):按码点排会把 é 排到 Z 后面去
    const order = lock.listCorpusFiles
    withTempTree({ "Z.md": "1", "a.md": "2", "é.md": "3" }, (root) => {
      expect(order(root)).toEqual(["Z.md", "a.md", "é.md"])
    })
  })

  test("白名单之外的文件、.github、超限文件都不进树", () => {
    withTempTree(
      {
        "keep.py": "print(1)",
        "README": "没有扩展名的 README 要留",
        "LICENSE": "许可文件要留",
        "avatar.png": "二进制不进",
        ".github/workflows/ci.yml": "流水线不进",
        "node_modules/x.js": "依赖目录不进",
      },
      (root) => {
        expect(lock.listCorpusFiles(root)).toEqual(["LICENSE", "README", "keep.py"])
      },
    )
  })

  test("目录不存在返回 null,空目录返回 0 个文件", () => {
    expect(lock.treeHash(join(tmpdir(), "definitely-not-here-" + Date.now()))).toBeNull()
    withTempTree({}, (root) => expect(lock.treeHash(root).files).toBe(0))
  })
})

describe("语料锁:版本与锚点口径", () => {
  test("SKILL.md 不按 slug 硬编码:换一个 skill 目录名也能读到版本", () => {
    withTempTree(
      { "skills/another-skill/SKILL.md": "---\nname: another-skill\nmetadata:\n  version: 2.1.0\n---\n" },
      (root) => expect(lock.readVersion(root)).toEqual({ version: "2.1.0", version_source: "SKILL.md" }),
    )
    withTempTree(
      { "plugin.toml": "[plugin]\nversion = \"0.1.0\"\n", "skills/another-skill/SKILL.md": "---\nmetadata:\n  version: 9.9.9\n---\n" },
      (root) => expect(lock.readVersion(root)).toEqual({ version: "0.1.0", version_source: "plugin.toml" }),
    )
  })

  test("readVersion 能读 .claude-plugin/plugin.json，但让位给 plugin.toml", () => {
    withTempTree(
      { ".claude-plugin/plugin.json": JSON.stringify({ name: "plugin-forge", version: "2.8.0" }) + "\n" },
      (root) => expect(lock.readVersion(root)).toEqual({ version: "2.8.0", version_source: "plugin.json" }),
    )
    // 市场插件的宿主清单优先:两边都写时以 plugin.toml 为准
    withTempTree(
      {
        "plugin.toml": '[plugin]\nversion = "9.9.9"\n',
        ".claude-plugin/plugin.json": JSON.stringify({ version: "1.0.0" }),
      },
      (root) => expect(lock.readVersion(root)).toEqual({ version: "9.9.9", version_source: "plugin.toml" }),
    )
    // 坏 JSON 不猜也不抛:退回到「读不到」,让调用方保留锁里的旧值并告警
    withTempTree(
      { ".claude-plugin/plugin.json": "{ 坏掉的 json" },
      (root) => expect(lock.readVersion(root)).toEqual({ version: null, version_source: null }),
    )
  })

  test("checkAnchors 的 strip 能把锁里的 src/ 前缀摘到 staging 上核对", () => {
    withTempTree({ "plugin.toml": "name = \"x\"\nversion = \"1.0\"\n" }, (root) => {
      const anchors = [{ id: "m", file: "src/plugin.toml", all: ["name = \"x\""] }]
      expect(lock.checkAnchors(root, anchors, { strip: "src/" })).toEqual([])
      expect(lock.checkAnchors(root, anchors).length, "不通前缀时应当报文件不存在").toBe(1)
      expect(lock.checkAnchors(root, [{ id: "m", file: "src/plugin.toml", all: ["name = \"y\""] }], { strip: "src/" }).length).toBe(1)
    })
  })
})

describe("语料锁:公开约定文档", () => {
  test("LOCK-FORMAT.md 的过滤规则与代码常量一致(文档不许抄旧值)", () => {
    const doc = readFileSync(join(CORPUS, "LOCK-FORMAT.md"), "utf8")
    const lines = doc.split("\n")
    const pick = (kw: string) => {
      const l = lines.find((x) => x.includes(kw))
      expect(l, "文档里找不到「" + kw + "」那一行").toBeTruthy()
      return l as string
    }
    // 逐个比对那一行:不要求反引号写法(白名单很长,逐项加反引号反而难读),
    // 但限定在白名单行里比,免得 `.py` 靠 `.pyc` 之类蒙混过关
    const extLine = pick("扩展名白名单")
    for (const ext of lock.KEEP_EXT) expect(extLine, "白名单行缺 " + ext).toContain(ext)
    const nameLine = pick("无扩展名文件") + "\n" + (lines[lines.findIndex((x) => x.includes("无扩展名文件")) + 1] || "")
    for (const name of lock.KEEP_NAME) expect(nameLine, "无扩展名清单缺 " + name).toContain(name)
    const dirLine = pick("目录跳过")
    for (const dir of lock.SKIP_DIRS) expect(dirLine, "跳过目录清单缺 " + dir).toContain(dir)
    expect(doc, "文档里的单文件上限与 MAX_FILE 不一致").toContain(String(lock.MAX_FILE))
  })

  test("LOCK-FORMAT.md 覆盖新增条目的两条路径与全部退出码", () => {
    const doc = readFileSync(join(CORPUS, "LOCK-FORMAT.md"), "utf8")
    for (const s of ["--update", "--update-all", "--locked", "--verify-only"]) {
      expect(doc, "文档没讲 " + s).toContain(s)
    }
    for (const s of ["source", "archive_format", "computedHash", "archive_sha256", "anchors", "kind"]) {
      expect(doc, "文档没讲字段 " + s).toContain(s)
    }
    expect(doc, "文档没写 git 来源的两条硬约束").toMatch(/refs\/tags/)
    expect(doc, "文档没写换行固定的原因").toMatch(/core\.autocrlf/)
  })
})

describe("语料锁:slug 与锁结构", () => {
  test("slug 只允许 [a-z0-9_-],大写/路径穿越/空串一律拒", () => {
    expect(lock.assertSlug("reuse_before_build")).toBe("reuse_before_build")
    for (const bad of ["Reuse", "../x", "a/b", "", null]) {
      expect(() => lock.assertSlug(bad), "放过了非法 slug:" + String(bad)).toThrow()
    }
  })

  test("schema / algo 不认识时不猜,直接抛", () => {
    const root = mkdtempSync(join(tmpdir(), "corpuslock-lock-"))
    try {
      writeFileSync(join(root, "corpus.lock.json"), JSON.stringify({ schema: 99, algo: "tree-sha256-v0", entries: {} }))
      expect(() => lock.loadLock(root)).toThrow(/schema/)
      writeFileSync(join(root, "corpus.lock.json"), JSON.stringify({ schema: 1, algo: "md5", entries: {} }))
      expect(() => lock.loadLock(root)).toThrow(/algo/)
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })
})
