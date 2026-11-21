import { describe, expect, test } from "bun:test"
import { spawnSync } from "node:child_process"
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs"
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

// 装机版没有仓库里那份 corpus.lock:第一个 pin 拉完会就地生成一条,
// 第二个 pin 再来时锁里自然没有自己。这不该被当成「口径冲突」而拒绝,
// 但仓库里那份锁缺条目仍然是真问题(补锁漏了),必须继续拒绝。
describe("装机版:两个 pin 共用一份本机锁", () => {
  const PULL = join(ROOT, "workbench", "pull_skill_docs.cjs")
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

  // 真正会写锁的是拉取路径。它要联网,但补条目发生在 spawn fetch_corpus 之前,
  // 所以把 PATH 清掉让 curl 直接 ENOENT 就能在不联网的前提下确定性地走到那条分支:
  // 拉取必然失败,但锁该被改的已经改了。这样门禁既不看 GitHub 的脸色,也仍然守得住。
  test("拉取路径才补条目(即使抓取失败,注册也要先落盘)", () => {
    const root = makeRoot("本机生成:安装包不含 corpus.lock,按 pin 落一条。")
    try {
      const r = spawnSync(process.execPath, [PULL], {
        encoding: "utf8",
        env: { ...process.env, NEKO_CORPUS_DIR: root, PATH: "" },
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
    }
  })
})
