import { describe, expect, test } from "bun:test"
import { spawnSync } from "node:child_process"
import { createRequire } from "node:module"
import { mkdtempSync, mkdirSync, readdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs"
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
    // vendored:false 是按需条目(正文不进仓库),磁盘上没有副本属正常;
    // 它一旦被拉到本地,下面的「落盘副本」用例会照样逐字节校验。
    const slugs = Object.keys(parsed.entries).filter((s) => parsed.entries[s].vendored !== false)
    expect(slugs.length, "锁里一条都没有").toBeGreaterThan(0)
    for (const slug of slugs) {
      const entry = parsed.entries[slug]
      const measured = lock.treeHash(join(CORPUS, slug, "src"))
      expect(measured, slug + " 的语料目录不存在").not.toBeNull()
      expect(measured.files, slug + " 的文件数与锁不符").toBe(entry.files)
      expect(measured.computedHash, slug + " 的字节与锁不符(改过语料就重跑 --update)").toBe(entry.computedHash)
    }
  })

  test("按需条目声明了 vendored:false,且不会顶替仓库内副本", () => {
    const parsed = lock.loadLock(CORPUS)
    const on_demand = Object.entries(parsed.entries).filter(([, e]) => (e as { vendored?: boolean }).vendored === false)
    for (const [slug, entry] of on_demand) {
      expect(entry.kind, slug + " 是按需条目,应写明 kind(如 skill),别混进市场插件计数").toBeTruthy()
      expect(entry.kind).not.toBe("plugin")
      expect(entry.source, slug + " 缺 source").toMatch(/^[\w.-]+\/[\w.-]+$/)
      expect(entry.commit, slug + " 必须钉完整 SHA").toMatch(/^[0-9a-f]{40}$/)
    }
  })

  /**
   * vendored:false 会把离线校验从「必须存在」放宽成「不存在就 SKIP」。
   * 不给白名单的话,只要把一个条目的这个字段改掉、再删掉语料,CI 依然全绿——
   * 用一个布尔值就能把整条语料的正文校验关掉,而没有任何地方会吭声。
   * 新增按需条目要走一次显式改动,不能顺手改字段。
   */
  test("vendored:false 只给按需文档条目用,且名单是写死的", () => {
    const ALLOWED = ["neko-plugin-skill"]
    const parsed = lock.loadLock(CORPUS)
    const got = Object.entries(parsed.entries)
      .filter(([, e]) => (e as { vendored?: boolean }).vendored === false)
      .map(([s]) => s)
    expect(got.sort(), "这两边不一致:要么漏了新增的按需条目,要么有人把普通条目的正文校验关掉了")
      .toEqual([...ALLOWED].sort())
  })

  /**
   * `pull_pinned` 用「note 里含『本机生成』」来区分「装机版就地生成的锁」和「仓库里那份锁」。
   * 前者允许覆盖条目,后者必须退 2 让人去统一——否则「有人只改了一边」会被当成正常现象放过。
   * 这个判据是字符串约定,所以得有一条测试盯着仓库锁别沾上这四个字:改文案时顺手把
   * 仓库锁写成「本机生成」,覆盖逻辑就会在 CI 和评审眼皮底下生效,而且没人看得出来。
   */
  test("仓库里那份锁的 note 不能含「本机生成」", () => {
    const parsed = lock.loadLock(CORPUS)
    expect(String(parsed.note || ""), "仓库锁的 note 含「本机生成」,会让覆盖逻辑在仓库里生效")
      .not.toContain("本机生成")
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
    expect(entry.transport).toBe("git")
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
    expect(rbb.transport ?? "zip").toBe("zip")
    expect(rbb.archive_format ?? "zip").toBe("zip")
  })

  test("锚点指向的文件都在语料里(锚点失效要当场报,不能等 --locked 才炸)", () => {
    const parsed = lock.loadLock(CORPUS)
    for (const [slug, entry] of Object.entries(parsed.entries)) {
      // 按需条目的正文不在仓库里,没有可核的对象;它的锚点在 --locked 抓取后校验。
      // 这里跳过是「没有副本」,不是「核过了」——磁盘上有副本时下面的用例仍会逐字节比对。
      if ((entry as { vendored?: boolean }).vendored === false) continue
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

  // 纯文档/技能语料没有 plugin.toml,版本写在一个单行 VERSION.txt 里。
  // 它排 SKILL.md 之前是有意的:显式声明版本的文件比 frontmatter 里顺手写的 version 更明确。
  // 这条用例把取舍钉住——否则下次调整候选顺序,没人知道原来依赖这个优先级。
  test("readVersion 读 VERSION.txt,且优先于 SKILL.md", () => {
    withTempTree(
      { "VERSION.txt": "4.3.1\n" },
      (root) => expect(lock.readVersion(root)).toEqual({ version: "4.3.1", version_source: "VERSION.txt" }),
    )
    withTempTree(
      { "VERSION.txt": "v1.2.3\n" },
      (root) => expect(lock.readVersion(root)).toEqual({ version: "1.2.3", version_source: "VERSION.txt" }),
    )
    withTempTree(
      { "VERSION.txt": "4.3.1\n", "skills/x/SKILL.md": "---\nmetadata:\n  version: 4.2.0\n---\n" },
      (root) => expect(lock.readVersion(root)).toEqual({ version: "4.3.1", version_source: "VERSION.txt" }),
    )
    // 但让位给 plugin.toml:市场插件仍以宿主认的那份清单为准
    withTempTree(
      { "VERSION.txt": "4.3.1\n", "plugin.toml": '[plugin]\nversion = "0.1.0"\n' },
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

  test("v1 锁读取时迁移成 v2 字段(改名字不破老锁)", () => {
    const root = mkdtempSync(join(tmpdir(), "corpuslock-v1-"))
    try {
      writeFileSync(join(root, "corpus.lock.json"), JSON.stringify({
        schema: 1,
        algo: "tree-sha256-v0",
        entries: {
          gitOne: { repo: "o/n", source: "git", tree_sha256: "a".repeat(64), commit: "b".repeat(40), ref: "v1", files: 1, anchors: [] },
          zipOne: { repo: "o/z", tree_sha256: "c".repeat(64), commit: "d".repeat(40), ref: "v1", files: 2, anchors: [] },
        },
      }))
      const l = lock.loadLock(root)
      expect(l.schema, "读出来应升到 v2").toBe(2)
      expect(l.entries.gitOne.source).toBe("o/n")
      expect(l.entries.gitOne.transport).toBe("git")
      expect(l.entries.gitOne.computedHash).toBe("a".repeat(64))
      expect(l.entries.gitOne.repo, "旧字段应被删掉").toBeUndefined()
      // v1 没写 source 就是 zip:迁移后也不该凭空多出 transport
      expect(l.entries.zipOne.source).toBe("o/z")
      expect(l.entries.zipOne.transport).toBeUndefined()
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })

  test("slug 拒绝原型污染保留字(它们也符合 [a-z0-9_-])", () => {
    for (const bad of ["__proto__", "constructor", "prototype"]) {
      expect(() => lock.assertSlug(bad), "放过了保留字:" + bad).toThrow()
    }
  })

  test("旧版本机生成的 v1 锁,迁移后与 forge-pin.json 逐字段一致", () => {
    const pin = JSON.parse(readFileSync(join(ROOT, "packs", "neko-plugin", "skill",
      "neko-plugin-dev", "references", "forge-pin.json"), "utf8"))
    const root = mkdtempSync(join(tmpdir(), "corpuslock-upgrade-"))
    try {
      // 旧版 pull_forge 生成的就是这套:repo / source(传输方式) / tree_sha256
      writeFileSync(join(root, "corpus.lock.json"), JSON.stringify({
        schema: 1,
        algo: "tree-sha256-v0",
        entries: {
          [pin.slug]: {
            repo: pin.source, kind: pin.kind, source: pin.transport,
            ref: pin.ref, commit: pin.commit, version: pin.version,
            version_source: pin.version_source, archive_format: pin.archive_format,
            archive_sha256: pin.archive_sha256, tree_sha256: pin.computedHash,
            files: pin.files, anchors: pin.anchors,
          },
        },
      }))
      const e = lock.loadLock(root).entries[pin.slug]
      expect(e.source, "仓库名没迁过来").toBe(pin.source)
      expect(e.transport, "传输方式没迁过来").toBe(pin.transport)
      expect(e.computedHash, "哈希没迁过来").toBe(pin.computedHash)
      expect(e.commit).toBe(pin.commit)
      expect(e.anchors).toEqual(pin.anchors)
      expect(e.repo, "旧字段应被删掉").toBeUndefined()
      expect(e.tree_sha256, "旧字段应被删掉").toBeUndefined()
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })

  // 直接 writeFileSync 会先截断再写:中途盘满 / 断电 / Ctrl-C 就留下半截 JSON,
  // 之后连 --check 都读不了锁,连没动过的条目一起废掉。saveLock 必须是「写临时文件 +
  // rename」,而且失败时不能把临时文件留在目录里。
  test("saveLock 走临时文件替换,写完不留 .tmp", () => {
    const root = mkdtempSync(join(tmpdir(), "corpuslock-save-"))
    try {
      lock.saveLock(root, { schema: 2, algo: "tree-sha256-v0", entries: {} })
      expect(readdirSync(root), "写完只该有锁文件本身").toEqual(["corpus.lock.json"])
      expect(lock.loadLock(root).entries).toEqual({})
    } finally {
      rmSync(root, { recursive: true, force: true })
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

// 按需条目(vendored:false):仓库里只留钉版,正文用时再拉。
// 两条边界必须同时成立——离线时「没有」不算故障,但本地一旦有副本就必须逐字节对得上;
// 否则一份被改过的副本可以长期顶着「按需条目」的名义不被发现。
describe("按需条目(vendored:false)的离线校验", () => {
  const FETCH = join(ROOT, "workbench", "fetch_corpus.cjs")
  const ENTRY = {
    source: "o/r", kind: "skill", vendored: false,
    ref: "a".repeat(40), commit: "a".repeat(40),
    version: "1.0.0", version_source: "VERSION.txt", transport: "zip", archive_format: "zip",
    archive_sha256: "c".repeat(64), computedHash: "d".repeat(64), files: 1, anchors: [],
  }
  const runVerify = (root: string) =>
    spawnSync(process.execPath, [FETCH, "--locked", "--verify-only"], {
      encoding: "utf8",
      env: { ...process.env, NEKO_CORPUS_DIR: root },
    })
  const withLock = (body: (root: string) => void) => {
    const root = mkdtempSync(join(tmpdir(), "ondemand-"))
    try {
      writeFileSync(join(root, "corpus.lock.json"), JSON.stringify({
        schema: 2, algo: "tree-sha256-v0", entries: { on_demand: ENTRY },
      }))
      body(root)
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  }

  test("仓库里没有正文时离线跳过,不判失败", () => {
    withLock((root) => {
      const r = runVerify(root)
      expect(r.status, "离线校验应退 0,实际 stderr=" + r.stderr).toBe(0)
      expect(r.stdout).toContain("SKIP  on_demand")
      expect(r.stdout).toContain("按需跳过 1 个")
    })
  })

  test("本地一旦有副本,就必须与锁逐字节一致", () => {
    withLock((root) => {
      mkdirSync(join(root, "on_demand", "src"), { recursive: true })
      writeFileSync(join(root, "on_demand", "src", "README.md"), "被改过的一份\n")
      const r = runVerify(root)
      expect(r.status, "副本与锁不符应退 1").toBe(1)
      expect(r.stderr).toContain("树哈希不符")
    })
  })
})

// 审查反馈补的两条边界:两者都属于「看起来是缺失,其实是异常」——
// 把 src 写成普通文件、以及用 --only 拉一个条目却被别的条目拖失败。
describe("按需条目:缺失与异常的区分", () => {
  const FETCH = join(ROOT, "workbench", "fetch_corpus.cjs")
  const onDemand = {
    source: "o/r", kind: "skill", vendored: false,
    ref: "a".repeat(40), commit: "a".repeat(40),
    version: "1.0.0", version_source: "VERSION.txt", transport: "zip", archive_format: "zip",
    archive_sha256: "c".repeat(64), computedHash: "d".repeat(64), files: 1, anchors: [],
  }
  const vendored = { ...onDemand, vendored: true, source: "o/v", commit: "e".repeat(40), ref: "e".repeat(40) }

  const runWith = (entries: Record<string, unknown>, extra: string[], body: (root: string) => void) => {
    const root = mkdtempSync(join(tmpdir(), "ondemand-edge-"))
    try {
      writeFileSync(join(root, "corpus.lock.json"), JSON.stringify({
        schema: 2, algo: "tree-sha256-v0", entries,
      }))
      const r = spawnSync(process.execPath, [FETCH, "--locked", "--verify-only", ...extra], {
        encoding: "utf8", env: { ...process.env, NEKO_CORPUS_DIR: root },
      })
      body(root)
      return r
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  }

  test("src 存在但不是目录 → 判失败,不能算「没拉过」", () => {
    const root = mkdtempSync(join(tmpdir(), "ondemand-file-"))
    try {
      writeFileSync(join(root, "corpus.lock.json"), JSON.stringify({
        schema: 2, algo: "tree-sha256-v0", entries: { on_demand: onDemand },
      }))
      mkdirSync(join(root, "on_demand"), { recursive: true })
      writeFileSync(join(root, "on_demand", "src"), "这不是目录\n")
      const r = spawnSync(process.execPath, [FETCH, "--locked", "--verify-only"], {
        encoding: "utf8", env: { ...process.env, NEKO_CORPUS_DIR: root },
      })
      expect(r.status, "src 是文件时应退 1").toBe(1)
      expect(r.stderr).toContain("不是目录")
      expect(r.stderr).not.toContain("SKIP")
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })

  test("--only 只处理指定条目,不被别的缺失条目拖失败", () => {
    const r = runWith({ on_demand: onDemand, other: vendored }, ["--only", "on_demand"], () => {})
    expect(r.status, "只拉按需条目应退 0,实际 stderr=" + r.stderr).toBe(0)
    expect(r.stdout).toContain("按需跳过 1 个")
    expect(r.stderr, "不该去校验 other").not.toContain("other")
  })

  test("--only 指向锁里没有的条目 → 用法错误退 2", () => {
    const r = runWith({ on_demand: onDemand }, ["--only", "nope"], () => {})
    expect(r.status).toBe(2)
    expect(r.stderr).toContain("--only 指定的条目不在锁里")
  })

  // --only 收窄的是「本次处理哪些」,不能拿收窄后的名单去判断「哪些是锁外的」。
  // 否则锁里明明登记过的条目会被报成「未在锁里登记」,每次按需拉取都刷一遍假警告——
  // 假警告多了,真警告就没人看了。
  test("--only 收窄时不把锁里其它条目误报成「锁外」", () => {
    const root = mkdtempSync(join(tmpdir(), "only-unlocked-"))
    try {
      writeFileSync(join(root, "corpus.lock.json"), JSON.stringify({
        schema: 2, algo: "tree-sha256-v0", entries: { on_demand: onDemand, kept: vendored },
      }))
      // kept 在锁里、磁盘上也有副本:它是「锁内」,不该出现在锁外提示里
      mkdirSync(join(root, "kept", "src"), { recursive: true })
      writeFileSync(join(root, "kept", "src", "a.md"), "x\n")
      const run = () => spawnSync(process.execPath,
        [FETCH, "--locked", "--verify-only", "--only", "on_demand"], {
          encoding: "utf8", env: { ...process.env, NEKO_CORPUS_DIR: root },
        })
      const r = run()
      expect(r.stdout, "kept 在锁里,不该被报成锁外:" + r.stdout).not.toContain("锁外条目")

      // 反面:真没登记过的目录仍要报,别把修误报变成把提示删了
      mkdirSync(join(root, "not_locked", "src"), { recursive: true })
      const r2 = run()
      expect(r2.stdout, "真锁外的目录还是要报").toContain("锁外条目")
      expect(r2.stdout).toContain("not_locked")
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })

  // lstat 能看见、stat 跟随失败:链接在但指向的东西不在。existsSync 对这种也返回 false,
  // 若拿它当「没有」,悬空链接会被永远当成「还没拉过」。
  //
  // Windows 上普通符号链接要管理员/开发者模式,但目录 junction 不需要——所以先试默认
  // (Linux 走这条),失败再试 junction。两种都建不出来才跳过,不假装通过。
  const makeLink = (target: string, link: string): boolean => {
    for (const type of [undefined, "junction", "dir"] as const) {
      try { symlinkSync(target, link, type); return true } catch { /* 换一种 */ }
    }
    return false
  }
  const canSymlink = (() => {
    const d = mkdtempSync(join(tmpdir(), "sym-probe-"))
    try { return makeLink(join(d, "nope"), join(d, "l")) } finally {
      rmSync(d, { recursive: true, force: true })
    }
  })()

  // ENOTDIR 的语义是「路径上某一级存在但不是目录」,比如 OUT/on_demand 是个普通文件。
  // 那不是「没拉过」,是环境坏了;归成 missing 就等于给按需条目又开了一个静默跳过的入口。
  test("src 的父级是普通文件(ENOTDIR) → 判失败,不能算「没拉过」", () => {
    const root = mkdtempSync(join(tmpdir(), "ondemand-enotdir-"))
    try {
      writeFileSync(join(root, "corpus.lock.json"), JSON.stringify({
        schema: 2, algo: "tree-sha256-v0", entries: { on_demand: onDemand },
      }))
      writeFileSync(join(root, "on_demand"), "这不是目录\n")
      const r = spawnSync(process.execPath, [FETCH, "--locked", "--verify-only"], {
        encoding: "utf8", env: { ...process.env, NEKO_CORPUS_DIR: root },
      })
      expect(r.status, "父级坏掉时应退 1").toBe(1)
      // Linux 报 ENOTDIR,Windows 上 libuv 把它并进 ENOENT,所以这里落到「条目目录不是
      // 目录」那句上。两边都要求「说得出到底哪里不对」,而不是一句「缺失」了事。
      expect(r.stderr, "要指出坏在哪,不能只说「缺失」").toMatch(/不是目录|ENOTDIR/)
      expect(r.stderr).not.toContain("SKIP")
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })

  test.skipIf(!canSymlink)("src 是悬空符号链接 → 判失败,不能算「没拉过」", () => {
    const root = mkdtempSync(join(tmpdir(), "ondemand-dangling-"))
    try {
      writeFileSync(join(root, "corpus.lock.json"), JSON.stringify({
        schema: 2, algo: "tree-sha256-v0", entries: { on_demand: onDemand },
      }))
      mkdirSync(join(root, "on_demand"), { recursive: true })
      makeLink(join(root, "on_demand", "not-there"), join(root, "on_demand", "src"))
      const r = spawnSync(process.execPath, [FETCH, "--locked", "--verify-only"], {
        encoding: "utf8", env: { ...process.env, NEKO_CORPUS_DIR: root },
      })
      expect(r.status, "悬空链接时应退 1").toBe(1)
      expect(r.stderr).toContain("悬空符号链接")
      expect(r.stderr).not.toContain("SKIP")
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })

  // 指向真实目录的链接是正常用法(语料放别处软链进来),必须照常按目录处理。
  test.skipIf(!canSymlink)("src 是指向真实目录的链接 → 当目录正常用,不误判", () => {
    const root = mkdtempSync(join(tmpdir(), "ondemand-link-"))
    try {
      mkdirSync(join(root, "real-src"), { recursive: true })
      writeFileSync(join(root, "real-src", "a.md"), "x\n")
      writeFileSync(join(root, "corpus.lock.json"), JSON.stringify({
        schema: 2, algo: "tree-sha256-v0", entries: { on_demand: onDemand },
      }))
      mkdirSync(join(root, "on_demand"), { recursive: true })
      makeLink(join(root, "real-src"), join(root, "on_demand", "src"))
      const r = spawnSync(process.execPath, [FETCH, "--locked", "--verify-only"], {
        encoding: "utf8", env: { ...process.env, NEKO_CORPUS_DIR: root },
      })
      // 树哈希对不上是预期的(锁里是假哈希),关键是它走到了「比对哈希」而不是「当成没拉过」
      expect(r.stderr).toContain("树哈希不符")
      expect(r.stderr).not.toContain("语料缺失")
      expect(r.stderr).not.toContain("悬空符号链接")
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })

  // 给了 --only 却解析不出条目,若退化成「全量」,用户以为只拉了一条,实际把锁里所有
  // 缺失语料都拖了下来——而且没有任何提示。空串和只有逗号都必须当场拒绝。
  test("--only 给了却没有条目 → 退 2,不许退化成全量", () => {
    for (const bad of ["", ",", " , "]) {
      const r = runWith({ on_demand: onDemand, other: vendored }, ["--only", bad], () => {})
      expect(r.status, "--only " + JSON.stringify(bad) + " 应退 2").toBe(2)
      expect(r.stderr).toContain("--only 后面没有条目")
      expect(r.stdout, "不该处理任何条目").not.toContain("按需跳过")
    }
  })
})
