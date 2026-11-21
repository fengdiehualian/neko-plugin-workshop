import { describe, expect, test } from "bun:test"
import { spawnSync } from "node:child_process"
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
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
  })

  // 只查一个目录名是不够的:把 52 篇平铺成 references/30-xxx.md 一样是回迁,而且更难发现
  // ——目录没了,看上去只是「多了几篇参考文档」。所以按两条线索查:
  //   1. 深度专题的命名形态 NN-xxx.md(本仓库自己的参考页不带数字前缀);
  //   2. pin 的 anchors 里 references/ 下的文件名,在 packs/ 下递归找同名文件。
  //     换子目录、改前缀名都能抓到,清单从 pin 来,不用另外维护。
  test("正文没有被回迁进 references/(只该留钉版)", () => {
    const GENERIC = new Set(["SKILL.md", "README.md", "index.md", "VERSION.txt"])
    const walk = (dir: string): string[] =>
      readdirSync(dir, { withFileTypes: true }).flatMap((d) =>
        d.isDirectory() ? walk(join(dir, d.name)) : [join(dir, d.name)])

    expect(existsSync(join(REF, "deep-dive")), "references/deep-dive/ 又出现了:只该留钉版").toBe(false)
    const numbered = readdirSync(REF).filter((f) => /^\d{2}-.+\.md$/.test(f))
    expect(numbered, "references/ 下出现深度专题形态的文件(回迁):" + numbered.join(", ")).toEqual([])

    const names = (pin.anchors as { file: string }[])
      .filter((a) => a.file.includes("/references/"))
      .map((a) => a.file.split("/").pop() as string)
      .filter((n) => n && !GENERIC.has(n))
    expect(names.length, "pin 的 anchors 里应当有 references 下的文件").toBeGreaterThan(0)
    const hits = walk(join(ROOT, "packs")).filter((p) => names.includes(p.split(/[\\/]/).pop() as string))
    expect(hits, "这些深度专题又在 packs/ 里出现了:" + hits.join(", ")).toEqual([])
  })

  // 仓库里只钉版本、正文不进库。但拉一次就会在 market-corpus/ 下躺 75 个未跟踪文件,
  // `git add -A` 顺手就提交了——回迁是悄悄发生的,没有冲突、没有提示。
  // 不只盯当前这个 slug:锁里每个 vendored:false 的条目都得有忽略规则,
  // 以后新增按需条目忘了加,这条当场红。
  test("按需语料的落盘目录被 .gitignore 挡住(回迁是悄悄发生的)", () => {
    const ignore = readFileSync(join(ROOT, ".gitignore"), "utf8")
    const onDemand = Object.entries(lock.entries as Record<string, { vendored?: boolean }>)
      .filter(([, e]) => e.vendored === false)
      .map(([s]) => s)
    expect(onDemand.length, "锁里应当至少有一个按需条目").toBeGreaterThan(0)
    for (const s of onDemand) {
      expect(ignore, ".gitignore 没忽略 market-corpus/" + s + "/,拉一次就成未跟踪文件")
        .toMatch(new RegExp("^market-corpus/" + s + "/?\\s*$", "m"))
    }
  })

  test("拉取与校验入口都指向真实存在的脚本", () => {
    for (const rel of ["workbench/pull_skill_docs.cjs", "workbench/pull_pinned.cjs",
      "workbench/fetch_corpus.cjs"]) {
      expect(existsSync(join(ROOT, ...rel.split("/"))), "缺 " + rel).toBe(true)
    }
    expect(pin.pull).toContain("pull_skill_docs.cjs")
    expect(pin.verify).toContain("--locked")
    // 不带 --only 的 --locked 会处理锁里所有条目:别的语料缺了就去下载,
    // 于是「只核对这一条」变成「顺手把别的也拉了」,「不联网」也不成立了。
    expect(pin.verify, "verify 必须限定到本条目").toContain("--only")
    expect(pin.verify, "核对不该联网").toContain("--verify-only")
  })
})

describe("neko-plugin-dev 接入线:已被拉到本地时的核对", () => {
  test("entry 里的路径与锚点都真的在语料里", () => {
    if (!hasCorpus) {
      // 按需条目在 CI / 装机版的常态:仓库里没有正文,没什么可校验的。
      // 这里不写 `expect(hasCorpus).toBe(false)` ——那在没语料时是同义反复,一句恒真的
      // 断言看着像校验,实际什么都没证明。改成检查「没语料这件事本身合不合法」:
      // pin 若没标 vendored:false,离线校验就会把缺失当故障,那才是真问题。
      expect(pin.vendored, "语料不在仓库里,但 pin 没标 vendored:false:离线校验会误判缺失").toBe(false)
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

// 装机版没有仓库里那份 corpus.lock:第一个 pin 拉完会就地生成一条,
// 第二个 pin 再来时锁里自然没有自己。这不该被当成「口径冲突」而拒绝,
// 但仓库里那份锁缺条目仍然是真问题(补锁漏了),必须继续拒绝。
describe("装机版:两个 pin 共用一份本机锁", () => {
  const PULL = join(ROOT, "workbench", "pull_skill_docs.cjs")
  const FORGE = join(ROOT, "workbench", "pull_forge.cjs")
  const entryForge = {
    source: "HorizonCloudy/plugin-forge", kind: "forge", ref: "v2.8.0", commit: "7".repeat(40),
    version: "2.8.0", version_source: "plugin.json", transport: "git",
    computedHash: "8".repeat(64), files: 1, anchors: [],
  }
  const makeRoot = (note: string) => {
    const root = mkdtempSync(join(tmpdir(), "pin-append-"))
    writeFileSync(join(root, "corpus.lock.json"), JSON.stringify({
      schema: 2, algo: "tree-sha256-v0", note, entries: { "plugin-forge": entryForge },
    }))
    return root
  }
  /** 锁里放一条「本 pin 的、但字段过期」的条目,模拟上游升版后装机版的处境。 */
  const makeStaleRoot = (note: string) => {
    const root = mkdtempSync(join(tmpdir(), "pin-stale-"))
    writeFileSync(join(root, "corpus.lock.json"), JSON.stringify({
      schema: 2, algo: "tree-sha256-v0", note,
      entries: { [pin.slug]: { ...entry, commit: "9".repeat(40), ref: "9".repeat(40), version: "0.0.9" } },
    }))
    return root
  }
  const runCheck = (root: string) =>
    spawnSync(process.execPath, [PULL, "--check"], {
      encoding: "utf8", env: { ...process.env, NEKO_CORPUS_DIR: root },
    })

  // --check 的约定是「只看不写」。它若顺手把条目补进锁里,会出两种坏结果:锁文件只读时
  // 先进 saveLock 抛错,语料明明对得上却报未捕获异常;锁可写时,报着「树哈希不符」却把
  // 状态改了,后面的人读到的锁已经不是他以为的那份。
  test("本机生成的锁缺本条目 → --check 不报「锁里没有」,也不改文件", () => {
    const root = makeRoot("本机生成:安装包不含 corpus.lock,按 pin 落一条。")
    try {
      const before = readFileSync(join(root, "corpus.lock.json"), "utf8")
      const r = runCheck(root)
      // 没有语料,所以是退 1(语料不存在),而不是退 2(口径冲突)——说明它没被「锁里没有」挡住
      expect(r.status, "没有语料时 --check 应退 1").toBe(1)
      expect(r.stderr, "不该报「锁里没有」").not.toContain("锁里没有")
      expect(r.stderr).toContain("语料不存在")
      const after = readFileSync(join(root, "corpus.lock.json"), "utf8")
      expect(after, "--check 改了锁文件").toBe(before)
      expect(JSON.parse(after).entries[pin.slug], "--check 不该补条目").toBeFalsy()
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })

  test("--check 连跑两次结果一致(只读,不是一次性假象)", () => {
    const root = makeRoot("本机生成:安装包不含 corpus.lock,按 pin 落一条。")
    try {
      const a = runCheck(root)
      const snap = readFileSync(join(root, "corpus.lock.json"), "utf8")
      const b = runCheck(root)
      expect(b.status).toBe(a.status)
      expect(readFileSync(join(root, "corpus.lock.json"), "utf8")).toBe(snap)
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })

  test("仓库里那份锁缺条目 → 仍然拒绝", () => {
    const root = makeRoot("语料锁 v0:每条记上游 commit、zip 哈希与落盘树的哈希。")
    try {
      const r = runCheck(root)
      expect(r.status).toBe(2)
      expect(r.stderr).toContain("锁里没有")
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })

  // 上游一升版,pin 换了、本机锁还是旧的。仓库锁遇到这种要停下让人统一;
  // 本机锁不能:装机版没有 git,用户手上没有「两处」可统一,只能永远卡住。
  test("本机锁里的条目与新 pin 不一致 → 按 pin 覆盖,不让用户「先统一」", () => {
    const root = makeStaleRoot("本机生成:安装包不含 corpus.lock,按 pin 落一条。")
    const emptyBin = mkdtempSync(join(tmpdir(), "no-curl-"))
    try {
      const r = spawnSync(process.execPath, [PULL], {
        encoding: "utf8",
        env: { ...process.env, NEKO_CORPUS_DIR: root, PATH: emptyBin },
      })
      expect(r.stderr, "装机版没有两处可统一,不该这么提示").not.toContain("先统一再拉")
      expect(r.stdout, "要说清是按 pin 覆盖的").toContain("按 pin 覆盖")
      const now = JSON.parse(readFileSync(join(root, "corpus.lock.json"), "utf8"))
      expect(now.entries[pin.slug].commit, "锁条目没跟到新 pin").toBe(pin.commit)
      expect(now.entries[pin.slug].version).toBe(pin.version)
    } finally {
      rmSync(root, { recursive: true, force: true })
      rmSync(emptyBin, { recursive: true, force: true })
    }
  }, 30000)

  test("仓库锁里的条目与 pin 不一致 → 仍然拒绝(那才是真该人管的)", () => {
    const root = makeStaleRoot("语料锁 v0:每条记上游 commit、zip 哈希与落盘树的哈希。")
    try {
      const r = runCheck(root)
      expect(r.status).toBe(2)
      expect(r.stderr).toContain("先统一再拉")
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })

  // 真正会写锁的是拉取路径。它要联网,但补条目发生在 spawn fetch_corpus 之前,
  // 所以把 PATH 指向一个空目录让 curl 直接 ENOENT,就能在不联网的前提下确定性地
  // 走到那条分支:拉取必然失败,但锁该改的已经改了。这样门禁既不看 GitHub 的脸色,
  // 也仍然守得住。
  //
  // 注意不能用 PATH="":Linux 上 execvp 看到「存在的空串」会回退到 confstr 默认路径
  // (/usr/bin:/bin),curl 反而又被找到了——本地过、CI 红就是这么来的。
  test("拉取路径才补条目(即使抓取失败,注册也要先落盘)", () => {
    const root = makeRoot("本机生成:安装包不含 corpus.lock,按 pin 落一条。")
    const emptyBin = mkdtempSync(join(tmpdir(), "no-curl-"))
    try {
      const r = spawnSync(process.execPath, [PULL], {
        encoding: "utf8",
        env: { ...process.env, NEKO_CORPUS_DIR: root, PATH: emptyBin },
      })
      expect(r.status, "抓不到的时候应当失败,而不是静默成功").not.toBe(0)
      expect(r.stdout).toContain("已按")
      const now = JSON.parse(readFileSync(join(root, "corpus.lock.json"), "utf8"))
      expect(now.entries[pin.slug], "拉取路径没把条目补进本机锁").toBeTruthy()
      expect(now.entries[pin.slug].commit).toBe(pin.commit)
      expect(now.entries[pin.slug].vendored, "按需条目要带 vendored:false,否则离线校验会要求正文").toBe(false)
      expect(now.entries["plugin-forge"], "补条目时不该动别的条目").toBeTruthy()
    } finally {
      rmSync(root, { recursive: true, force: true })
      rmSync(emptyBin, { recursive: true, force: true })
    }
  }, 30000)

  // pull_forge 以前是独立脚本,现在只是 pull_pinned 的薄壳。三处行为跟着变了
  // (--locked --only、本机锁追加、比对多了 vendored),但测试只覆盖 pull_skill_docs。
  // 壳接错(比如指向旧 pin)不会让任何用例变红,所以这里对 forge 那条入口做冒烟。
  test("pull_forge 壳接对了:读的是 forge 那条 pin,空目录下 --check 退 1", () => {
    const root = mkdtempSync(join(tmpdir(), "forge-smoke-"))
    try {
      const r = spawnSync(process.execPath, [FORGE, "--check"], {
        encoding: "utf8", env: { ...process.env, NEKO_CORPUS_DIR: root },
      })
      expect(r.status, "没有语料时应退 1,stderr=" + r.stderr).toBe(1)
      expect(r.stdout, "它认的应当是 forge 那条 pin").toContain("plugin-forge")
      expect(r.stderr).toContain("语料不存在")
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })

  test("pull_forge 也走本机锁追加:缺条目不报「锁里没有」,--check 仍不改文件", () => {
    const root = mkdtempSync(join(tmpdir(), "forge-local-"))
    writeFileSync(join(root, "corpus.lock.json"), JSON.stringify({
      schema: 2, algo: "tree-sha256-v0", note: "本机生成:安装包不含 corpus.lock,按 pin 落一条。",
      entries: { [pin.slug]: entry },
    }))
    try {
      const before = readFileSync(join(root, "corpus.lock.json"), "utf8")
      const r = spawnSync(process.execPath, [FORGE, "--check"], {
        encoding: "utf8", env: { ...process.env, NEKO_CORPUS_DIR: root },
      })
      expect(r.status, "stderr=" + r.stderr).toBe(1)
      expect(r.stderr, "本机锁缺条目不该被当成口径冲突").not.toContain("锁里没有")
      expect(readFileSync(join(root, "corpus.lock.json"), "utf8"), "--check 改了锁文件").toBe(before)
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })
})
