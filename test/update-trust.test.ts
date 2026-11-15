import { describe, expect, test } from "bun:test"
import { readFileSync } from "node:fs"
import { verifyUpdateManifest, planUpdateTarget, expectedSha256, cmpVersion, UPDATE_TRUST } from "../wb-agent-lib.mjs"

// 对应 GitHub issue #12:哈希与包同渠道 → 渠道被劫持就能一起换;
// 信任根改成内置公钥验签的清单,并要求多渠道互相印证。
const GOOD = JSON.parse(readFileSync(new URL("./fixtures/manifest-good.json", import.meta.url), "utf8"))
const GOOD_SHA = "7d490fd9180a3c6dd722ecbca569161d06b4eeb07aaaabd54aba6d96a3a584a4"

const src = (o) => ({ source: "github", version: "1.2.63", notes: "", assets: [], ...o })

describe("verifyUpdateManifest:内置公钥验签", () => {
  test("随仓库的签名素材验得过", () => {
    const r = verifyUpdateManifest(GOOD)
    expect(r.ok).toBe(true)
    expect(r.manifest.sha256).toBe(GOOD_SHA)
    expect(r.manifest.version).toBe("v9.9.9")
  })
  test("换掉清单内容但保留原签名 → 验签必须失败(换包演练)", () => {
    // 注意:不能直接改 payload 的 base64 尾巴——那样连 JSON 都解不开,报的是格式错而不是签名错,
    // 测试看着绿其实没验到签名这条路
    const m = JSON.parse(Buffer.from(GOOD.payload, "base64").toString("utf8"))
    const swapped = { ...m, sha256: "f".repeat(64), size: 1 }
    const r = verifyUpdateManifest({ ...GOOD, payload: Buffer.from(JSON.stringify(swapped), "utf8").toString("base64") })
    expect(r.ok).toBe(false)
    expect(r.reason).toContain("签名")
  })
  test("payload 被改坏(解不出 JSON)也拒绝,且给出人话原因", () => {
    const r = verifyUpdateManifest({ ...GOOD, payload: Buffer.from("不是 JSON", "utf8").toString("base64") })
    expect(r.ok).toBe(false)
    expect(r.reason).toContain("JSON")
  })
  test("签名段被换也验不过", () => {
    const r = verifyUpdateManifest({ ...GOOD, sig: Buffer.from("x".repeat(64)).toString("base64") })
    expect(r.ok).toBe(false)
  })
  test("换成别的算法/缺字段/坏 base64 都拒绝", () => {
    expect(verifyUpdateManifest({ ...GOOD, alg: "rsa" }).ok).toBe(false)
    expect(verifyUpdateManifest({ v: 1, alg: "ed25519" }).ok).toBe(false)
    expect(verifyUpdateManifest(null).ok).toBe(false)
    expect(verifyUpdateManifest({ alg: "ed25519", payload: "@@@@", sig: "@@@@" }).ok).toBe(false)
  })
  test("公钥是 SPKI base64 且长度合理", () => {
    expect(UPDATE_TRUST.alg).toBe("ed25519")
    expect(Buffer.from(UPDATE_TRUST.pubkey, "base64").length).toBeGreaterThan(30)
  })
})

describe("planUpdateTarget:单渠道不足以决定升级", () => {
  test("两个渠道都报同一版本 → 互相印证可装", () => {
    const p = planUpdateTarget([src({}), src({ source: "gitee" })], "")
    expect(p.ok).toBe(true)
    expect(p.trustRoot).toBe("agreement")
    expect(p.channels.sort()).toEqual(["gitee", "github"])
  })
  test("只有一个渠道报最高版本、没有签名清单 → 拒装", () => {
    const p = planUpdateTarget([src({ version: "9.9.9" }), src({ source: "gitee", version: "1.2.63" })], "")
    expect(p.ok).toBe(false)
    expect(p.reason).toContain("一个渠道")
  })
  test("只有一个渠道报最高版本,但有验签清单 → 可装,并以清单哈希为准", () => {
    const p = planUpdateTarget([src({ version: "9.9.9" })], GOOD_SHA)
    expect(p.ok).toBe(true)
    expect(p.trustRoot).toBe("manifest")
    expect(p.sha).toBe(GOOD_SHA)
  })
  test("被劫持的一端谎报更高版本想压制另一端:另一端版本较低时,单渠道+无签名=拒装(不能靠谎报升级)", () => {
    const p = planUpdateTarget([src({ version: "99.0.0", notes: "SHA256:`" + "a".repeat(64) + "`" }), src({ source: "gitee", version: "1.2.63" })], "")
    expect(p.ok).toBe(false)
  })
  test("两渠道对同一版本声明的哈希互相矛盾 → 拒装", () => {
    const a = src({ notes: "SHA256:`" + "a".repeat(64) + "`" })
    const b = src({ source: "gitee", notes: "SHA256:`" + "b".repeat(64) + "`" })
    const p = planUpdateTarget([a, b], "")
    expect(p.ok).toBe(false)
    expect(p.reason).toContain("不一致")
  })
  test("清单与正文声明不一致 → 以清单为准并拒装(正文被改的演练)", () => {
    const a = src({ notes: "SHA256:`" + "a".repeat(64) + "`" })
    const b = src({ source: "gitee", notes: "SHA256:`" + "a".repeat(64) + "`" })
    const p = planUpdateTarget([a, b], GOOD_SHA)
    expect(p.ok).toBe(false)
    expect(p.reason).toContain("签名清单")
  })
  test("资产 digest 被利用(issue #12 第 3 点:以前 digest 被丢掉是死代码)", () => {
    const s = src({ assets: [{ name: "x.exe", url: "u", size: 1, digest: "sha256-" + GOOD_SHA }] })
    expect(expectedSha256(s)).toBe(GOOD_SHA)
  })
  test("一个源都没有 → 明确拒绝而不是放行", () => {
    expect(planUpdateTarget([], "").ok).toBe(false)
  })
})

describe("cmpVersion 语义(studio 与 lib 共用一份)", () => {
  test("数字段逐位比,缺位当 0", () => {
    expect(cmpVersion("1.2.64", "1.2.63")).toBe(1)
    expect(cmpVersion("1.10.0", "1.9.9")).toBe(1)
    expect(cmpVersion("v2.0", "2.0.0")).toBe(0)
    expect(cmpVersion("", "0.0.1")).toBe(-1)
  })
})
