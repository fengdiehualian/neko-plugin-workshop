import { describe, expect, test } from "bun:test"
import { spawnSync } from "node:child_process"
import { createHash } from "node:crypto"
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { tmpdir } from "node:os"
import { fileURLToPath } from "node:url"

// 对应 GitHub issue #8:PR 里那份 reuse-before-build 是「改写版冒充正典」——
// 缺 Join/Extend/Fork/Build 判决序、缺「不可复用原因」首行门禁、缺可改性硬闸、缺逐次确认、缺不可信内容，
// 而且没有上游哈希、没跑上游自带的 verify_localized.py,测试只做了字符串 contains。
// 本文件把三件事钉住:①正典逐字节等于上游(pin 里的 SHA-256);②上游校验器真的在跑且判 PASS;
// ③反向对照——砍掉条款必须变红,否则"跑过了"什么都不是。
const ROOT = dirname(dirname(fileURLToPath(import.meta.url)))
const SKILL_DIR = join(ROOT, "packs", "neko-plugin", "skill", "neko-plugin-dev")
const REF = join(SKILL_DIR, "references")
const RBB = join(REF, "rbb")
const COPY = join(REF, "reuse-before-build.neko.md")
const VERIFY = join(RBB, "scripts", "verify_localized.py")
const RUNTIME = process.execPath // 用跑测试的同一个运行时(bun/node 都能跑 .cjs),不依赖 PATH

const sha256 = (b: Buffer) => createHash("sha256").update(b).digest("hex")

// 跑子进程(python / bun / node)的测试一律给 60 秒:bun 默认 5 秒,冷启动或磁盘抖动就会假红(实测超时表现为 out 为空)
const SEC60 = 60_000

// —— python 定位:issue #9 的教训——只有 ENOENT 才算"这台机器没装",其它启动失败一律算测试环境坏了
// 隔离参数按版本给:-I 通用,-P(不把脚本目录塞进 sys.path)要 3.11+;别把老解释器误判成"没装"而静默跳过
type Py = { bin: string | null; flags: string[]; missing: boolean; reason: string }
function detectPython(): Py {
  const cands = [process.env.PYTHON_BIN, "python", "python3", "py"].filter(Boolean) as string[]
  const notes: string[] = []
  for (const c of cands) {
    const r = spawnSync(c, ["-I", "-c", "print('11' if __import__('sys').version_info>=(3,11) else '10')"], { encoding: "utf8" })
    if (r.error) {
      const code = (r.error as NodeJS.ErrnoException).code
      notes.push(`${c}:${code === "ENOENT" ? "没装" : "启动失败 " + code}`)
      if (code !== "ENOENT") return { bin: null, flags: ["-I"], missing: false, reason: notes.join(" | ") }
      continue
    }
    if (r.status === 0) {
      const v = (r.stdout || "").trim()
      if (v !== "11" && v !== "10") { notes.push(`${c}:探测输出异常 ${JSON.stringify(v)}`); continue }
      return { bin: c, flags: v === "11" ? ["-I", "-P"] : ["-I"], missing: false, reason: `${c}(3.${v})` }
    }
    notes.push(`${c}:退出码 ${r.status}`)
  }
  return { bin: null, flags: ["-I"], missing: true, reason: notes.join(" | ") }
}
const py = detectPython()

function testProc(name: string, fn: () => void) {
  return test(name, fn, SEC60)
}
function pyVerify(file: string, extra: string[] = []): { rc: number | null; out: string } {
  const r = spawnSync(py.bin as string, [...py.flags, VERIFY, file, ...extra], { cwd: ROOT, encoding: "utf8" })
  if (r.error) return { rc: null, out: "spawn-error " + String((r.error as Error).message) }
  return { rc: r.status, out: (r.stdout || "") + (r.stderr || "") }
}
function nodeScript(script: string, args: string[] = []): { rc: number | null; out: string } {
  const r = spawnSync(RUNTIME, [script, ...args], { cwd: ROOT, encoding: "utf8" })
  if (r.error) return { rc: null, out: "spawn-error " + String((r.error as Error).message) }
  return { rc: r.status, out: (r.stdout || "") + (r.stderr || "") }
}
const tmp = mkdtempSync(join(tmpdir(), "rbb-test-"))
function scratch(name: string, text: string): string {
  const p = join(tmp, name)
  writeFileSync(p, text, "utf8")
  return p
}

describe("rbb 正典:逐字节钉在上游", () => {
  const pinPath = join(RBB, "UPSTREAM_PIN.json")
  const pin = JSON.parse(readFileSync(pinPath, "utf8"))
  test("pin 形状可信:tag + 40 位 commit + verbatim + MIT", () => {
    expect(pin.upstream).toMatch(/^https:\/\/github\.com\/[0-9A-Za-z_.-]+\/[0-9A-Za-z_.-]+$/)
    expect(pin.ref).toMatch(/^v\d+\.\d+\.\d+$/)
    expect(pin.commit).toMatch(/^[0-9a-f]{40}$/)
    expect(String(pin.mode)).toContain("verbatim")
    expect(String(pin.license)).toContain("MIT")
    expect(Array.isArray(pin.files)).toBe(true)
    expect(pin.files.length).toBeGreaterThanOrEqual(15)
  })
  test("每个正典文件的实算 SHA-256 等于 pin(改一个字节就红)", () => {
    const bad: string[] = []
    for (const f of pin.files) {
      expect(f.sha256, "pin 里哈希本身不合法:" + f.path).toMatch(/^[0-9a-f]{64}$/)
      expect(f.blob, "pin 里 git blob 不合法:" + f.path).toMatch(/^[0-9a-f]{40}$/)
      const p = join(RBB, f.path)
      if (!existsSync(p)) { bad.push(f.path + " 缺文件"); continue }
      const got = sha256(readFileSync(p))
      if (got !== f.sha256) bad.push(`${f.path} 期望 ${f.sha256.slice(0, 12)} 实算 ${got.slice(0, 12)}`)
    }
    expect(bad, "正典被改动:\n  " + bad.join("\n  ")).toEqual([])
  })
  testProc("vendor --check 离线自证通过", () => {
    const r = nodeScript("workbench/vendor_rbb.cjs", ["--check"])
    expect(r.out, r.out).toContain("逐字一致")
    expect(r.rc).toBe(0)
  })
  test("MIT 全文随包(不是只写一句 MIT)", () => {
    const lic = readFileSync(join(RBB, "UPSTREAM_LICENSE"), "utf8")
    expect(lic).toMatch(/MIT License/)
    expect(lic).toMatch(/Permission is hereby granted, free of charge/)
  })
})

describe("上游校验器:正典与工坊副本", () => {
  test("python 不可用时必须显式跳过并警告(不许静默绿)", () => {
    if (py.missing) console.warn(`⚠ [rbb] 本机没有可用 python(${py.reason})——本组校验**本轮没跑**,不能算通过`)
    expect(py.missing, "python 存在但启动失败,属环境问题:" + py.reason).toBe(false)
  })
  const group = py.missing ? describe.skip : describe
  group("跑真校验", () => {
    testProc("正典 SKILL.md --canonical = verbatim PASS", () => {
      const r = pyVerify(join(RBB, "SKILL.md"), ["--canonical"])
      expect(r.out, r.out).toContain("PASS")
      expect(r.out).toContain("invariant-block=verbatim")
      expect(r.rc).toBe(0)
    })
    testProc("上游中文完整版 --canonical = translated(zh) PASS", () => {
      const r = pyVerify(join(RBB, "references", "zh-guide.md"), ["--canonical"])
      expect(r.out, r.out).toContain("PASS")
      expect(r.out).toContain("invariant-block=translated(zh)")
      expect(r.rc).toBe(0)
    })
    testProc("工坊副本不加 flag 必须过完整校验(含 localized: 声明)", () => {
      const r = pyVerify(COPY)
      expect(r.out, r.out).toContain("PASS")
      expect(r.rc).toBe(0)
      // 走了白名单就说明声明行被跳过——副本不许混进包内参考件白名单
      expect(r.out, "副本被判成 reference copy,声明行其实没校:" + r.out).not.toContain("(reference copy)")
      expect(r.out).toMatch(/anchors=12\/12/)
    })
    testProc("副本不许偷用 --canonical 给自己开后门(rc=2)", () => {
      const r = pyVerify(COPY, ["--canonical"])
      expect(r.rc).toBe(2)
      expect(r.out).toContain("REFUSED")
    })
    testProc("换一份更宽松的契约也拦下(rc=2)", () => {
      const loose = scratch("loose.json", JSON.stringify({ version: 4, block: {}, rules_min: 0, rule_anchors: [], must_have: [], critical_sections: [] }))
      const r = pyVerify(COPY, ["--contract", loose])
      expect(r.rc).toBe(2)
      expect(r.out).toContain("REFUSED")
    })
  })
})

describe("反向对照:砍掉条款必须变红", () => {
  test("同样要求 python", () => {
    if (py.missing) console.warn("⚠ [rbb] 本机没有可用 python——反向对照**本轮没跑**")
    expect(py.missing, "python 启动失败:" + py.reason).toBe(false)
  })
  const group = py.missing ? describe.skip : describe
  group("逐条试", () => {
    const text = readFileSync(COPY, "utf8")
    const lines = () => text.split(/\r?\n/)
    const cutSection = (heading: string, replacement: string) => {
      const L = lines()
      const m = L.findIndex((l) => l.trim() === "## " + heading)
      expect(m, "副本里没有章节:" + heading).toBeGreaterThanOrEqual(0)
      const e = L.findIndex((l, i) => i > m && /^##\s+\S/.test(l))
      return L.slice(0, m + 1).join("\n") + "\n" + replacement + "\n\n" + L.slice(e < 0 ? L.length : e).join("\n") + "\n"
    }
    const cases: [string, () => string, string][] = [
      ["抽掉 localized 声明行", () => lines().filter((l) => !l.startsWith("localized:")).join("\n") + "\n", "declaration"],
      ["删掉不变块第 3 条", () => lines().filter((l) => !l.startsWith("3. **先证明不可复用")).join("\n") + "\n", "rule:rule_3"],
      ["假不变块(编号齐但没有语义锚点)", () => text.replace(/(RBB:INVARIANTS:BEGIN -->\n)[\s\S]*?(\n<!-- RBB:INVARIANTS:END)/, "$1<!-- RBB:INVARIANTS:LANG: zh -->\n1. 甲\n2. 乙\n3. 丙\n4. 丁\n5. 戊$2"), "rule:rule_1"],
      ["硬约束章节砍成一句", () => cutSection("硬约束", "就一句话。"), "section_short:hard_constraints"],
      ["摘掉「找到了什么」交付段", () => text.replaceAll("找到了什么", "（摘掉）").replaceAll("What I found", "(gone)"), "anchor:deliverable"],
      ["摘掉「不可复用原因」首行门禁", () => text.replaceAll("不可复用原因", "（摘掉）").replaceAll("Not reusable because", "(gone)"), "anchor:gate"],
      ["摘掉「没有第五个判决」", () => text.replaceAll("没有第五个判决", "（摘掉）").replaceAll("There is no fifth verdict", "(gone)"), "anchor:no_fifth_verdict"],
    ]
    for (const [name, make, expectMissing] of cases) {
      testProc(name + " → FAIL", () => {
        const r = pyVerify(scratch(name.replace(/[^\w]/g, "_") + ".md", make()))
        expect(r.rc, `${name} 居然通过了(${r.out})——这道闸是装饰`).not.toBe(0)
        expect(r.out, `${name} 应报 ${expectMissing},实际 ${r.out}`).toContain(expectMissing)
      })
    }
  })
})

describe("许可声明:正典不进本仓库 MIT 的口径", () => {
  const doc = readFileSync(join(ROOT, "THIRD_PARTY_NOTICES.md"), "utf8")
  const notices = JSON.parse(readFileSync(join(ROOT, "workbench", "notices.json"), "utf8"))
  const pin = JSON.parse(readFileSync(join(RBB, "UPSTREAM_PIN.json"), "utf8"))
  test("第 4 节把上游/tag/commit/版权行/逐文件哈希写全", () => {
    expect(doc).toContain("neko-reuse-before-build")
    expect(doc, "声明里的 tag 与 pin 不一致").toContain(pin.ref)
    expect(doc, "声明里的 commit 与 pin 不一致").toContain(pin.commit)
    expect(doc, "缺上游版权行(仅写 MIT 不够)").toMatch(/Copyright \(c\) .*1-dcjiang/)
    const listed = (doc.match(/\| `references\/rbb\//g) || []).length
    expect(listed, `声明表列了 ${listed} 行,pin 登记 ${pin.files.length} 个文件`).toBe(pin.files.length)
  })
  test("不许把第三方正典算进本仓库 MIT 生成物", () => {
    expect(doc).toMatch(/不含上节的第三方正典/)
    expect(doc).toMatch(/把它们当本仓库 MIT 产物是错的/)
  })
  test("notices.json 的 vendored 与 pin 一致(机器可读口径同源)", () => {
    const v = notices.vendored
    expect(v, "notices.json 缺 vendored 段").toBeTruthy()
    expect(v.ref).toBe(pin.ref)
    expect(v.commit).toBe(pin.commit)
    expect(v.files.length).toBe(pin.files.length)
    for (const f of v.files) {
      const p = pin.files.find((x: any) => x.path === f.path)
      expect(p, "vendored 里有 pin 没登记的文件:" + f.path).toBeTruthy()
      expect(f.sha256).toBe(p.sha256)
    }
    expect(v.localized_copy, "副本路径要能从声明里定位").toContain("reuse-before-build.neko.md")
  })
})

describe("工坊副本的来历与接线", () => {
  testProc("生成物与源一致(上游正文或附录改过却没重跑 = 红)", () => {
    const r = nodeScript("workbench/localize_rbb.cjs", ["--check"])
    expect(r.out, r.out).toContain("一致")
    expect(r.rc).toBe(0)
  })
  test("声明行在文件末尾且四键齐全", () => {
    const last = readFileSync(COPY, "utf8").split(/\r?\n/).filter((l) => l.trim()).pop() || ""
    expect(last.startsWith("localized:")).toBe(true)
    for (const k of ["host=", "kept=", "changed=", "unsupported="]) expect(last, "缺 " + k).toContain(k)
  })
  test("自定阈值明确标注不冒充上游", () => {
    const t = readFileSync(COPY, "utf8")
    expect(t).toMatch(/90\s*天/)
    expect(t, "自定阈值必须标注出处,不能混进正典").toMatch(/本工程自定/)
    expect(t, "要写清正典在哪").toContain("references/rbb/")
  })
  test("SKILL.md 索引同时指向正典与副本", () => {
    const sk = readFileSync(join(SKILL_DIR, "SKILL.md"), "utf8")
    expect(sk, "SKILL.md 没索引 rbb 正典目录").toMatch(/rbb\/SKILL\.md/)
    expect(sk, "SKILL.md 没索引工坊副本").toMatch(/reuse-before-build\.neko\.md/)
    expect(sk, "索引里要说清谁是正典、谁是副本").toMatch(/逐字|正典/)
  })
  testProc("正典没被改写版顶掉(issue #8 的根因)", () => {
    const legacy = join(REF, "reuse-before-build.md")
    if (!existsSync(legacy)) return // 主干没有;PR #5 那份若合进来,必须自己带声明行过校验
    if (py.missing) { console.warn("⚠ [rbb] 存在" + legacy + " 但没 python 可校,本轮未验"); return }
    const r = pyVerify(legacy)
    expect(r.rc, "存在无声明的改写版:" + r.out).toBe(0)
  })
})
