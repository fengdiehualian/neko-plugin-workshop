import { describe, expect, test } from "bun:test"
import { existsSync, readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

// forge 接入线的守则:钉版清单、锁条目、4 份短文三者必须互相对得上。
//
// 为什么单独一条:钉版信息现在写了两遍(锁里一份、forge-pin.json 一份),
// 短文里又点了一批 `src/...` 路径。任何一处漂了,读者拿到的是「指向不存在的文件」
// 或「按旧版规则写的代码」,而门禁全绿——这类坏是最难发现的。

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)))
const REF = join(ROOT, "packs", "neko-plugin", "skill", "neko-plugin-dev", "references")
const SKILL = join(ROOT, "packs", "neko-plugin", "skill", "neko-plugin-dev", "SKILL.md")
const CORPUS = join(ROOT, "market-corpus")

const pin = JSON.parse(readFileSync(join(REF, "forge-pin.json"), "utf8"))
const lock = JSON.parse(readFileSync(join(CORPUS, "corpus.lock.json"), "utf8"))
const entry = lock.entries[pin.slug]

const SHORTS = [
  "forge-flow.md",
  "forge-roles.md",
  "forge-contract-gate.md",
  "forge-reading-map.md",
]

describe("forge 接入线:钉版与锁一致", () => {
  test("forge-pin.json 与 corpus.lock 的 commit / ref / version 一致", () => {
    expect(entry, "锁里没有 " + pin.slug).toBeTruthy()
    expect(pin.commit, "pin 与锁的 commit 不一致").toBe(entry.commit)
    expect(pin.ref, "pin 与锁的 ref 不一致").toBe(entry.ref)
    expect(pin.version, "pin 与锁的 version 不一致").toBe(entry.version)
    expect(pin.corpus_root).toBe("market-corpus/" + pin.slug + "/src")
    expect(pin.kind).toBe("forge")
  })

  test("anchors 与锁条目逐条一致(两处各写一遍,漂了当场红)", () => {
    expect(pin.anchors).toEqual(entry.anchors)
  })

  test("语料目录里的关键入口都在", () => {
    // entry 里的路径是相对语料根(即 <slug>/src),不是相对 market-corpus
    for (const [k, rel] of Object.entries(pin.entry)) {
      const p = join(CORPUS, pin.slug, "src", ...String(rel).split("/"))
      expect(existsSync(p), "forge-pin.json 的 entry." + k + " 指向不存在的路径:" + rel).toBe(true)
    }
  })
})

describe("forge 接入线:短文", () => {
  const skill = readFileSync(SKILL, "utf8")

  test("4 份短文都在,且都被 SKILL.md 索引", () => {
    for (const f of SHORTS) {
      expect(existsSync(join(REF, f)), "缺 " + f).toBe(true)
      expect(skill.includes(f), "SKILL.md 没索引 " + f).toBe(true)
    }
    expect(skill.includes("forge-pin.json"), "SKILL.md 没提 forge-pin.json").toBe(true)
    expect(skill.includes("pull_forge.cjs"), "SKILL.md 没给拉取入口").toBe(true)
  })

  test("短文点名的 src/ 路径必须真在语料里(防悬空引用)", () => {
    const missing: string[] = []
    for (const f of SHORTS) {
      const text = readFileSync(join(REF, f), "utf8")
      for (const m of text.matchAll(/`(src\/[A-Za-z0-9_./-]+)`/g)) {
        const rel = m[1]
        if (rel.includes("*")) continue
        if (!existsSync(join(CORPUS, pin.slug, ...rel.split("/")))) missing.push(f + " → " + rel)
      }
    }
    expect(missing, "短文指向的语料路径不存在:" + missing.join("; ")).toEqual([])
  })

  test("短文不复制正典条文:每份都写明正典在哪、自己只是接入线", () => {
    for (const f of SHORTS) {
      const text = readFileSync(join(REF, f), "utf8")
      expect(text.length, f + " 太长,接入线不该抄条文").toBeLessThan(9000)
      expect(/market-corpus\/plugin-forge\/src|src\/skills\/plugin-forge/.test(text),
        f + " 没写正典在哪").toBe(true)
    }
  })
})
