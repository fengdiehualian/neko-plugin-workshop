import { describe, expect, test } from "bun:test"
import { buildWishPrompt, formatExpertBlock } from "../wb-agent-lib.mjs"

const pack = (over: Record<string, unknown> = {}) => ({
  id: "neko-plugin-ops",
  dir: "x",
  files: [],
  agentIds: ["a", "b"],
  problems: [],
  manifest: {
    name: "neko-plugin-ops",
    displayName: { zh: "NEKO 插件运维专家团" },
    displayDescription: { zh: "统筹插件开发与自动运维" },
    agentName: "lead",
    teamInfo: { leadAgent: "lead", memberAgents: ["a"] },
    members: [
      { id: "lead", displayName: { zh: "云朵" }, profession: { zh: "主理人" } },
      { id: "a", displayName: { zh: "云测" }, profession: { zh: "测试工程师" } },
      { id: "b", displayName: { zh: "云安" } },
    ],
    ...over,
  },
})

describe("formatExpertBlock(专家团提示块)", () => {
  test("正常包:标题/成员/主理人不进名单/纪律附后", () => {
    const s = formatExpertBlock(pack(), "## 团队总原则\n1. 先结论")
    expect(s).toContain("【专家团模式(已启用)】")
    expect(s).toContain("NEKO 插件运维专家团")
    expect(s).toContain("云测(测试工程师)")
    expect(s).toContain("云安") // 无 profession 也能列
    expect(s).not.toContain("云朵") // 主理人不重复出现在成员列表
    expect(s).toContain("团队总原则")
    expect(s.indexOf("【专家团模式")).toBeLessThan(s.indexOf("团队总原则"))
  })

  test("坏输入一律空串:无包/无清单/清单有问题", () => {
    expect(formatExpertBlock(null as any)).toBe("")
    expect(formatExpertBlock({ manifest: null } as any)).toBe("")
    expect(formatExpertBlock({ manifest: {}, problems: ["缺 plugin.json"] } as any)).toBe("")
  })

  test("清单字段缺失有兜底:名字回落 id、成员空也成形", () => {
    const s = formatExpertBlock({ id: "bare", problems: [], manifest: { name: "bare", members: [] } } as any)
    expect(s).toContain("bare")
    expect(s).toContain("(见包内清单)")
  })

  test("超预算会截断并留痕", () => {
    const s = formatExpertBlock(pack(), "纪".repeat(5000), 300)
    expect(s.length).toBeLessThanOrEqual(360)
    expect(s).toContain("已截断")
  })

  test("单独关闭的成员从名单剔除", () => {
    const s = formatExpertBlock(pack(), "", 2200, ["a"])
    expect(s).not.toContain("云测")
    expect(s).toContain("云安")
    expect(s).toContain("成员:")
  })

  test("全部成员关闭时给出明确说明而不是空名单", () => {
    const s = formatExpertBlock(pack(), "", 2200, ["a", "b"])
    expect(s).toContain("均已单独关闭")
    expect(s).not.toContain("云测")
    expect(s).not.toContain("云安")
  })
})

describe("buildWishPrompt 的专家团注入位", () => {
  test("第六参注入:顺序在长期偏好之后、愿望之前;两种模式都生效", () => {
    const s = buildWishPrompt("做个插件", "build", 0, "", "【用户长期偏好(必须遵守)】\n1. 莫兰迪\n\n", "【专家团模式(已启用)】纪律正文")
    expect(s.indexOf("莫兰迪")).toBeLessThan(s.indexOf("专家团模式"))
    expect(s.indexOf("专家团模式")).toBeLessThan(s.indexOf("【用户的愿望】"))
    const p = buildWishPrompt("做个插件", "plan", 0, "", "", "【专家团模式(已启用)】纪律正文")
    expect(p).toContain("专家团模式")
    expect(p.indexOf("专家团模式")).toBeLessThan(p.indexOf("【用户的愿望/当前消息】"))
  })

  test("不传第六参完全不影响旧输出(兼容既有调用)", () => {
    const a = buildWishPrompt("查天气", "build", 0)
    expect(a).not.toContain("专家团")
  })
})
