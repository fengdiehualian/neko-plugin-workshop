import { describe, expect, test } from "bun:test"
import { readFileSync } from "node:fs"
import { verifyUpdateManifest, planUpdateTarget, expectedSha256, cmpVersion, UPDATE_TRUST } from "../wb-agent-lib.mjs"

// 对应 GitHub issue #12:哈希与包同渠道 → 渠道被劫持就能一起换;
// 信任根改成内置公钥验签的清单,并要求多渠道互相印证。
const GOOD = JSON.parse(readFileSync(new URL("./fixtures/manifest-good.json", import.meta.url), "utf8"))
// 示范素材是用**独立测试钥匙**签的(issue #7):不能用生产私钥签测试内容
const TEST_PUB = readFileSync(new URL("./fixtures/test-pubkey.txt", import.meta.url), "utf8").trim()
const GOOD_SHA = JSON.parse(Buffer.from(GOOD.payload, "base64").toString("utf8")).sha256.toLowerCase()
const GOOD_VERSION = "v9.9.9"
const GOOD_ASSET = "N.E.K.O.PluginWorkshop-Setup.exe"
const GOOD_SIZE = 12345678
const okExpect = { version: GOOD_VERSION, asset: GOOD_ASSET, size: GOOD_SIZE }

const src = (o) => ({ source: "github", version: "1.2.63", notes: "", assets: [], ...o })

describe("verifyUpdateManifest:独立测试钥匙签的素材", () => {
  test("测试公钥验得过", () => {
    const r = verifyUpdateManifest(GOOD, okExpect, TEST_PUB)
    expect(r.ok).toBe(true)
    expect(r.manifest.sha256).toBe(GOOD_SHA)
  })
  test("生产公钥验不过测试素材(证明素材不是生产签名,issue #7)", () => {
    const r = verifyUpdateManifest(GOOD, okExpect)
    expect(r.ok).toBe(false)
    expect(r.fatal).toBe(true)
  })
  test("测试钥匙与生产钥匙不是同一把", () => {
    expect(TEST_PUB).not.toBe(UPDATE_TRUST.pubkey)
  })
})

describe("verifyUpdateManifest:签名与内容都要对得上本次目标(issue #3)", () => {
  test("换内容保留原签名 → fatal 拒绝", () => {
    const m = JSON.parse(Buffer.from(GOOD.payload, "base64").toString("utf8"))
    const swapped = { ...m, sha256: "f".repeat(64) }
    const r = verifyUpdateManifest({ ...GOOD, payload: Buffer.from(JSON.stringify(swapped), "utf8").toString("base64") }, okExpect, TEST_PUB)
    expect(r.ok).toBe(false)
    expect(r.fatal).toBe(true)
    expect(r.reason).toContain("签名")
  })
  test("旧版清单拿来给新版用 → 明确拒绝(防签名回滚)", () => {
    const r = verifyUpdateManifest(GOOD, { ...okExpect, version: "v9.9.10" }, TEST_PUB)
    expect(r.ok).toBe(false)
    expect(r.fatal).toBe(true)
    expect(r.reason).toContain("不一致")
  })
  test("清单资产名与本次下载的资产不符 → 拒绝", () => {
    const r = verifyUpdateManifest(GOOD, { ...okExpect, asset: "别的包.exe" }, TEST_PUB)
    expect(r.ok).toBe(false)
    expect(r.fatal).toBe(true)
  })
  test("清单体积与渠道声明不符 → 拒绝", () => {
    const r = verifyUpdateManifest(GOOD, { ...okExpect, size: 999 }, TEST_PUB)
    expect(r.ok).toBe(false)
    expect(r.fatal).toBe(true)
  })
  test("payload 被改坏(解不出 JSON)也 fatal", () => {
    const r = verifyUpdateManifest({ ...GOOD, payload: Buffer.from("不是 JSON", "utf8").toString("base64") }, okExpect, TEST_PUB)
    expect(r.ok).toBe(false)
    expect(r.fatal).toBe(true)
  })
  test("坏 base64 / 缺字段 / 换算法 都 fatal", () => {
    expect(verifyUpdateManifest({ ...GOOD, sig: "" }, okExpect, TEST_PUB).fatal).toBe(true)
    expect(verifyUpdateManifest({ ...GOOD, alg: "rsa" }, okExpect, TEST_PUB).fatal).toBe(true)
    expect(verifyUpdateManifest(null, okExpect, TEST_PUB).fatal).toBe(true)
  })
})

describe("planUpdateTarget:清单不可信时不能靠双渠道互证放行(issue #3)", () => {
  const twoChannels = [
    src({ version: "9.9.9", notes: "SHA256:`" + GOOD_SHA + "`" }),
    src({ source: "gitee", version: "9.9.9", notes: "SHA256:`" + GOOD_SHA + "`" }),
  ]
  test("两渠道一致 + 清单正常 → 放行(以清单为准)", () => {
    const p = planUpdateTarget(twoChannels, { sha: GOOD_SHA, fatal: false })
    expect(p.ok).toBe(true)
    expect(p.trustRoot).toBe("manifest")
  })
  test("两渠道一致但清单验签失败 → 必须拒装,不能退回互证放行", () => {
    const p = planUpdateTarget(twoChannels, { sha: "", fatal: true, reason: "清单签名不匹配" })
    expect(p.ok).toBe(false)
    expect(p.reason).toContain("清单不可信")
  })
  test("清单版本对不上(fatal)同样直接拒", () => {
    const p = planUpdateTarget(twoChannels, { sha: "", fatal: true, reason: "清单版本与要装的版本不一致", version: "9.9.9" })
    expect(p.ok).toBe(false)
  })
  test("兼容旧调用:直接传 sha 字符串", () => {
    expect(planUpdateTarget([src({ version: "9.9.9" })], GOOD_SHA).ok).toBe(true)
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
