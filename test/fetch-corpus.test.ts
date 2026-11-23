import { describe, expect, test } from "bun:test"
import { spawnSync } from "node:child_process"
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

// fetch_corpus 的两条命门(issue #28):
//   1. legacy 全量路径的 slug 来自市场接口,未校验时能拼出 OUT 之外的路径;
//   2. 部分条目失败时旧代码退 0,脚本/CI 会把一次不完整的抓取当成成功。
// 另外镜像返回的归档字节必须在落盘前与锁里的 archive_sha256 对上,不能只靠树哈希。
const ROOT = dirname(dirname(fileURLToPath(import.meta.url)))
const FETCH = join(ROOT, "workbench", "fetch_corpus.cjs")
const fc = readFileSync(FETCH, "utf8")

describe("fetch_corpus:legacy 全量路径的健壮性", () => {
  test("slug 先走 assertSlug,再拼 OUT/zip 路径", () => {
    const legacyAt = fc.indexOf("const rawSlug")
    const assertAt = fc.indexOf("locklib.assertSlug(rawSlug)", legacyAt)
    const destAt = fc.indexOf("const dest = path.join(OUT, slug)", legacyAt)
    expect(legacyAt, "找不到 legacy 的 rawSlug").toBeGreaterThan(-1)
    expect(assertAt, "legacy 路径没有校验 slug").toBeGreaterThan(-1)
    expect(destAt, "找不到 legacy 的 dest 拼接").toBeGreaterThan(-1)
    expect(assertAt, "slug 必须先校验再拼路径").toBeLessThan(destAt)
  })

  test("部分成功、部分失败仍退 1", () => {
    // 旧写法是 `(failed.length && !ok) ? 1 : 0`,只要拉到一条就退 0
    expect(fc).not.toMatch(/return\s*\(failed\.length\s*&&\s*!ok\)\s*\?\s*1\s*:\s*0/)
    expect(fc).toMatch(/return\s+failed\.length\s*\?\s*1\s*:\s*0/)
  })

  test("镜像归档先按锁里的 archive_sha256 校验,不符就换下一个镜像", () => {
    expect(fc).toMatch(/expectedArchive\s*&&\s*got\s*!==\s*expectedArchive/)
    expect(fc).toMatch(/downloadAtCommit\(ghRepo,\s*commit,\s*expectedArchive\)/)
    expect(fc).toMatch(/installAtCommit\(slug,\s*entry\.source,\s*entry\.commit,\s*entry\.archive_sha256\)/)
  })

  test("成功落盘与既有副本都会清掉旧 .done(锁模式已不用它续跑)", () => {
    const clear = fc.match(/fs\.rmSync\(path\.join\(dest,\s*"\.done"\),\s*\{\s*force:\s*true\s*\}\)/g) || []
    expect(clear.length, "应在「既有副本跳过」和「成功落盘」两处都清理 .done").toBeGreaterThanOrEqual(2)
  })
})

describe("fetch_corpus:坏锁条目不拖垮整轮", () => {
  const runVerify = (entry: Record<string, unknown>, slug = "../escape") => {
    const root = mkdtempSync(join(tmpdir(), "fetch-bad-entry-"))
    try {
      writeFileSync(join(root, "corpus.lock.json"), JSON.stringify({
        schema: 2, algo: "tree-sha256-v0", entries: { [slug]: entry },
      }))
      return spawnSync(process.execPath, [FETCH, "--locked", "--verify-only"], {
        encoding: "utf8", env: { ...process.env, NEKO_CORPUS_DIR: root },
      })
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  }

  const base = {
    source: "o/r", commit: "a".repeat(40), computedHash: "b".repeat(64),
    files: 1, anchors: [], vendored: false,
  }

  test("slug 路径穿越 → 条目不合规退 1,不抛未捕获异常", () => {
    const r = runVerify(base)
    expect(r.status, "应退 1,实际 stderr=" + r.stderr).toBe(1)
    expect(r.stderr).toContain("锁条目不合规")
    expect(r.stderr).toContain("slug")
    expect(r.stderr).not.toContain("TypeError")
  })

  test("archive_sha256 形态不对 → 条目不合规退 1", () => {
    const r = runVerify({ ...base, archive_sha256: "not-a-sha" }, "on_demand")
    expect(r.status, "应退 1,实际 stderr=" + r.stderr).toBe(1)
    expect(r.stderr).toContain("archive_sha256")
    expect(r.stderr).not.toContain("TypeError")
  })
})
