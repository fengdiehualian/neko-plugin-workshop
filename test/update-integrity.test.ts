import { describe, expect, test } from "bun:test"
import { expectedSha256, verifyUpdateIntegrity, isLocalUpdateFeed } from "../wb-agent-lib.mjs"

// 对应 GitHub issue #6:自动更新必须校验包完整性,且不允许被环境变量指到远端「换包」
const HEX = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90"
const OTHER = "0f1e2d3c4b5a69780f1e2d3c4b5a69780f1e2d3c4b5a69780f1e2d3c4b5a6978"

describe("expectedSha256:从源里取发布时登记的校验值", () => {
  test("GitHub 资产 digest(sha256: 前缀)优先", () => {
    expect(expectedSha256({ assets: [{ digest: "sha256:" + HEX.toUpperCase() }], notes: "" })).toBe(HEX)
  })
  test("资产 sha256 字段(feed/自制源)", () => {
    expect(expectedSha256({ assets: [{ sha256: HEX }], notes: "" })).toBe(HEX)
  })
  test("发布说明正文的「## 校验」段(大写 hex + 反引号)", () => {
    const notes = "更新内容\n\n## 校验\n\n- 大小:71,670,272 字节\n- SHA256:`" + HEX.toUpperCase() + "`\n"
    expect(expectedSha256({ source: "gitee", assets: [{ name: "setup.exe", url: "u", size: 1 }], notes })).toBe(HEX)
  })
  test("正文里的中文冒号/无 markdown 也认", () => {
    expect(expectedSha256({ assets: [], notes: "SHA256：" + HEX })).toBe(HEX)
  })
  test("没有任何校验值 → 空串(调用方据此拒装)", () => {
    expect(expectedSha256({ assets: [{ name: "x.exe", url: "u" }], notes: "本版修复若干问题" })).toBe("")
  })
  test("正文只提到 SHA256 却没有 64 位串 → 空串", () => {
    expect(expectedSha256({ assets: [], notes: "SHA256:略" })).toBe("")
  })
})

describe("verifyUpdateIntegrity:缺校验值/不一致一律不许装", () => {
  const src = { assets: [{ digest: "sha256-" + HEX }], notes: "" }
  test("一致 → ok", () => {
    expect(verifyUpdateIntegrity(src, HEX).ok).toBe(true)
  })
  test("大小写不敏感", () => {
    expect(verifyUpdateIntegrity(src, HEX.toUpperCase()).ok).toBe(true)
  })
  test("不一致 → 拒绝,并把期望/实算都写进原因", () => {
    const r = verifyUpdateIntegrity(src, OTHER)
    expect(r.ok).toBe(false)
    expect(r.reason).toContain("校验不通过")
    expect(r.reason).toContain(HEX.slice(0, 12))
    expect(r.reason).toContain(OTHER.slice(0, 12))
  })
  test("源没给校验值 → 拒绝(不是静默跳过),并引导手动安装", () => {
    const r = verifyUpdateIntegrity({ assets: [], notes: "没有校验信息" }, HEX)
    expect(r.ok).toBe(false)
    expect(r.want).toBe("")
    expect(r.reason).toContain("没有登记校验值")
    expect(r.reason).toContain("手动")
  })
  test("本地哈希算不出/被截短 → 拒绝", () => {
    expect(verifyUpdateIntegrity(src, "").ok).toBe(false)
    expect(verifyUpdateIntegrity(src, "zz").ok).toBe(false)
    expect(verifyUpdateIntegrity(src, HEX.slice(0, 63)).ok).toBe(false)
  })
  test("多资产分卷:任一带 digest 即可", () => {
    const r = verifyUpdateIntegrity({ assets: [{ name: "a.part1" }, { name: "a.part2", sha256: HEX }] }, HEX)
    expect(r.ok).toBe(true)
  })
})

describe("isLocalUpdateFeed:WB_UPDATE_FEED 只认本机源", () => {
  test("接受 file: 与回环地址", () => {
    for (const u of ["file:///C:/tmp/feed.json", "http://127.0.0.1:5199/feed.json", "http://localhost/feed.json", "http://[::1]:8080/feed.json", "http://127.0.0.42/feed.json"]) {
      expect(isLocalUpdateFeed(u)).toBe(true)
    }
  })
  test("拒绝公网源", () => {
    for (const u of ["https://evil.example.com/feed.json", "http://8.8.8.8/feed.json", "ftp://127.0.0.1/feed.json", ""]) {
      expect(isLocalUpdateFeed(u)).toBe(false)
    }
  })
  test("拒绝 userinfo 伪装成回环(http://127.0.0.1@evil.com)", () => {
    expect(isLocalUpdateFeed("http://127.0.0.1:80@evil.com/feed.json")).toBe(false)
    expect(isLocalUpdateFeed("http://localhost.evil.com/feed.json")).toBe(false)
  })
})
