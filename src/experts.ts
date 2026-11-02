import { existsSync } from "node:fs"
import { readdir, readFile } from "node:fs/promises"
import { join } from "node:path"

/** 专家包目录(与 packs/ 并列)。WorkBuddy / CodeBuddy 专家包格式。 */
export const EXPERTS_DIR = join(import.meta.dir, "..", "experts")

export interface ExpertMember {
  id: string
  role?: string
  avatar?: string
  displayName?: { en?: string; zh?: string }
}

export interface ExpertManifest {
  name: string
  version?: string
  description?: string
  agents: string[]
  avatar?: string
  expertType?: string
  agentName?: string
  teamInfo?: { leadAgent?: string; memberAgents?: string[] }
  members?: ExpertMember[]
  [key: string]: unknown
}

export interface ExpertPack {
  id: string
  dir: string
  manifest: ExpertManifest | null
  /** 包内全部文件的相对路径(正斜杠) */
  files: string[]
  /** agent 文档 frontmatter 里的 name,按文件名排序 */
  agentIds: string[]
  /** 清单问题;空数组=自洽 */
  problems: string[]
}

/** 取 markdown frontmatter 的 name 字段(没有 frontmatter 返回 null) */
export function readFrontmatterName(text: string): string | null {
  const normalized = text.replace(/^\uFEFF/, "").replace(/\r\n/g, "\n")
  if (!normalized.startsWith("---\n")) return null
  const end = normalized.indexOf("\n---", 3)
  if (end < 0) return null
  const block = normalized.slice(4, end)
  const hit = block.match(/^name:\s*(.+)$/m)
  if (!hit) return null
  return hit[1].trim().replace(/^["']|["']$/g, "")
}

const isStr = (v: unknown): v is string => typeof v === "string" && v.trim().length > 0

/**
 * 校验专家包清单与包内文件是否对得上。
 * 纯函数:不碰文件系统,便于用构造数据覆盖错误输入。
 */
export function validateExpertManifest(
  manifest: unknown,
  ctx: { files: string[]; agentIds: string[] },
): string[] {
  const problems: string[] = []
  if (!manifest || typeof manifest !== "object") return ["plugin.json 不是合法对象"]
  const m = manifest as Partial<ExpertManifest>
  const { files, agentIds } = ctx

  if (!isStr(m.name)) problems.push("缺少 name")

  const agents = Array.isArray(m.agents) ? m.agents : []
  if (agents.length === 0) problems.push("agents 为空")
  for (const a of agents) {
    if (!isStr(a)) {
      problems.push("agents 里有非字符串项")
      continue
    }
    const rel = a.replace(/^\.\//, "")
    if (!rel.endsWith(".md")) problems.push(`agents 项不是 .md:${a}`)
    if (!files.includes(rel)) problems.push(`agents 指向的文件不存在:${a}`)
  }

  // 反向:agents/ 下的文档必须全部登记,避免"加了人但清单忘写"
  const onDisk = files.filter((f) => /^agents\/[^/]+\.md$/.test(f))
  for (const f of onDisk) {
    if (!agents.some((a) => isStr(a) && a.replace(/^\.\//, "") === f)) {
      problems.push(`agents/ 下有未登记的文档:${f}`)
    }
  }
  if (onDisk.length !== agentIds.length) {
    problems.push(`agents/ 下有 ${onDisk.length - agentIds.length} 个文档缺 frontmatter name`)
  }
  for (const f of onDisk) {
    const stem = f.slice("agents/".length, -".md".length)
    if (!agentIds.includes(stem)) {
      // 名字可自定义,只要求 id 能对上文件名(成员 avatar/id 都按这个约定)
      problems.push(`agent 文档的 name 与文件名不一致:${f}`)
    }
  }

  if (m.avatar !== undefined) {
    if (!isStr(m.avatar)) problems.push("avatar 不是字符串")
    else if (!files.includes(m.avatar)) problems.push(`avatar 文件不存在:${m.avatar}`)
  }

  const members = Array.isArray(m.members) ? m.members : []
  if (members.length > 0 && members.length !== agents.length) {
    problems.push(`members(${members.length}) 与 agents(${agents.length}) 数量不一致`)
  }
  for (const mem of members) {
    if (!isStr(mem?.id)) {
      problems.push("members 里有缺少 id 的成员")
      continue
    }
    if (agentIds.length > 0 && !agentIds.includes(mem.id)) {
      problems.push(`members.id 找不到对应 agent 文档:${mem.id}`)
    }
    if (mem.avatar !== undefined && !files.includes(mem.avatar)) {
      problems.push(`成员头像不存在:${mem.id} → ${mem.avatar}`)
    }
  }

  const lead = m.teamInfo?.leadAgent ?? m.agentName
  if (lead !== undefined && !agentIds.includes(String(lead))) {
    problems.push(`主理人找不到对应 agent 文档:${String(lead)}`)
  }
  for (const sub of m.teamInfo?.memberAgents ?? []) {
    if (!agentIds.includes(sub)) problems.push(`teamInfo.memberAgents 找不到对应文档:${sub}`)
  }

  return problems
}

async function listFiles(dir: string, base = dir): Promise<string[]> {
  const out: string[] = []
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const abs = join(dir, entry.name)
    if (entry.isDirectory()) out.push(...(await listFiles(abs, base)))
    else out.push(abs.slice(base.length + 1).replace(/\\/g, "/"))
  }
  return out
}

/** 扫描 experts/ 下的全部专家包;缺清单的目录也会被列出(problems 非空) */
export async function loadExperts(root: string = EXPERTS_DIR): Promise<ExpertPack[]> {
  if (!existsSync(root)) return []
  const packs: ExpertPack[] = []
  for (const entry of await readdir(root, { withFileTypes: true })) {
    if (!entry.isDirectory() || entry.name.startsWith(".")) continue
    const dir = join(root, entry.name)
    const files = (await listFiles(dir)).sort()
    const manifestRel = ".codebuddy-plugin/plugin.json"
    let manifest: ExpertManifest | null = null
    const problems: string[] = []
    if (!files.includes(manifestRel)) {
      problems.push(`缺少 ${manifestRel}`)
    } else {
      try {
        manifest = JSON.parse(await readFile(join(dir, manifestRel), "utf8")) as ExpertManifest
      } catch (e) {
        problems.push(`${manifestRel} 解析失败:${(e as Error).message}`)
      }
    }

    const agentIds: string[] = []
    for (const f of files.filter((x) => /^agents\/[^/]+\.md$/.test(x))) {
      const name = readFrontmatterName(await readFile(join(dir, f), "utf8"))
      if (name) agentIds.push(name)
    }
    agentIds.sort()

    if (manifest) problems.push(...validateExpertManifest(manifest, { files, agentIds }))
    packs.push({ id: entry.name, dir, manifest, files, agentIds, problems })
  }
  return packs
}
