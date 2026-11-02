import { afterAll, describe, expect, test } from "bun:test"
import { mkdtemp, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import {
  EXPERTS_DIR,
  loadExperts,
  readFrontmatterName,
  validateExpertManifest,
} from "../src/index"

const temps: string[] = []

afterAll(async () => {
  for (const d of temps) await rm(d, { recursive: true, force: true })
})

describe("experts 目录", () => {
  test("每个专家包清单自洽(路径 / 成员 / 主理人全部对得上)", async () => {
    const packs = await loadExperts()
    expect(packs.length).toBeGreaterThan(0)
    for (const p of packs) {
      expect({ pack: p.id, problems: p.problems }).toEqual({ pack: p.id, problems: [] })
    }
  })

  test("neko-plugin-ops:10 名成员、10 份 agent 文档、11 张头像", async () => {
    const packs = await loadExperts()
    const neko = packs.find((p) => p.id === "neko-plugin-ops")
    expect(neko).toBeDefined()
    expect(neko!.manifest!.name).toBe("neko-plugin-ops")
    expect(neko!.agentIds.length).toBe(10)
    expect(neko!.manifest!.members!.length).toBe(10)
    expect(neko!.manifest!.agentName).toBe("neko-plugin-ops-team-lead")
    expect(neko!.files.filter((f) => f.startsWith("avatars/"))).toHaveLength(11)
    // 三份参考手册随包分发(团队宪法来源)
    for (const ref of ["references/system.llm", "references/neko-sources.md"]) {
      expect(neko!.files).toContain(ref)
    }
    // 打包脚本要求清单里没有本机绝对路径
    for (const f of ["README.md", "references/neko-sources.md"]) {
      const text = await Bun.file(join(EXPERTS_DIR, "neko-plugin-ops", f)).text()
      expect(text).not.toMatch(/C:\/Users|C:\\\\Users|E:\/Users/)
    }
  })

  test("边界:目录里没有清单时给出问题而不是崩掉", async () => {
    const root = await mkdtemp(join(tmpdir(), "wb-experts-"))
    temps.push(root)
    await Bun.write(join(root, "empty-pack", "README.md"), "# 空包\n")
    const packs = await loadExperts(root)
    expect(packs).toHaveLength(1)
    expect(packs[0].manifest).toBeNull()
    expect(packs[0].problems).toContain("缺少 .codebuddy-plugin/plugin.json")
    expect(await loadExperts(join(root, "not-exists"))).toEqual([])
  })
})

describe("validateExpertManifest(错误输入)", () => {
  const ok = {
    name: "demo",
    agents: ["./agents/lead.md"],
    agentName: "lead",
    members: [{ id: "lead", avatar: "avatars/a.jpg" }],
    teamInfo: { leadAgent: "lead", memberAgents: ["lead"] },
  }
  const files = ["agents/lead.md", "avatars/a.jpg"]
  const agentIds = ["lead"]

  test("正常清单无问题", () => {
    expect(validateExpertManifest(ok, { files, agentIds })).toEqual([])
  })

  test("引用了不存在的文档与头像", () => {
    const problems = validateExpertManifest(
      { ...ok, agents: ["./agents/ghost.md"], members: [{ id: "lead", avatar: "avatars/x.jpg" }] },
      { files, agentIds },
    )
    expect(problems.join("\n")).toContain("agents 指向的文件不存在")
    expect(problems.join("\n")).toContain("成员头像不存在")
  })

  test("未登记文档 / 成员 id 对不上 / 非对象输入", () => {
    expect(
      validateExpertManifest(ok, { files: [...files, "agents/extra.md"], agentIds }).join("\n"),
    ).toContain("未登记的文档")
    expect(
      validateExpertManifest({ ...ok, members: [{ id: "ghost" }] }, { files, agentIds }).join("\n"),
    ).toContain("members.id 找不到对应 agent 文档")
    expect(validateExpertManifest(null, { files, agentIds })).toEqual(["plugin.json 不是合法对象"])
  })
})

describe("readFrontmatterName", () => {
  test("带 BOM / CRLF 也能取到", () => {
    expect(readFrontmatterName("\uFEFF---\r\nname: yun-yu\r\n---\r\n正文")).toBe("yun-yu")
  })

  test("无 frontmatter 返回 null", () => {
    expect(readFrontmatterName("# 标题\nname: x")).toBeNull()
  })
})
