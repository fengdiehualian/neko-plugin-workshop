import { join, dirname, basename } from "node:path"

export interface PackMeta {
  id: string
  name: string
  version: string
  description: string
  language: string
  docs?: string
}

export interface Pack {
  meta: PackMeta
  dir: string
  rules: Record<string, unknown>
  templates: string[]
}

const PACKS_DIR = join(import.meta.dir, "..", "packs")
// 工坊根目录(packages/workbench 的上两级即包根),skill 文档里的 <WORKSHOP_DIR> 标记在部署时替换为它
const WORKSHOP_DIR = join(PACKS_DIR, "..")

export async function loadPacks(): Promise<Pack[]> {
  const { readdir, readFile } = await import("node:fs/promises")
  const packs: Pack[] = []
  for (const entry of await readdir(PACKS_DIR, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue
    const dir = join(PACKS_DIR, entry.name)
    const meta = JSON.parse(await readFile(join(dir, "pack.json"), "utf8")) as PackMeta
    let rules: Record<string, unknown> = {}
    try {
      rules = JSON.parse(await readFile(join(dir, "rules.json"), "utf8"))
    } catch {}
    const tdir = join(dir, "templates")
    let templates: string[] = []
    try {
      templates = (await readdir(tdir, { withFileTypes: true })).filter((x) => x.isDirectory()).map((x) => x.name)
    } catch {}
    packs.push({ meta, dir, rules, templates })
  }
  return packs
}

export async function renderProject(packId: string, template: string, vars: Record<string, string>) {
  const files = await renderTemplate(packId, template, vars)
  // 防遗忘层:每个生成的项目自带 Agent 规则与规范手册(opencode 原生识别);
  // references/ 递归复制进项目,Agent 离线可读(不依赖文档站/网络)
  const { readdir, readFile } = await import("node:fs/promises")
  const skillDir = join(PACKS_DIR, packId, "skill")
  try {
    for (const skill of await readdir(skillDir, { withFileTypes: true })) {
      if (!skill.isDirectory()) continue
      const walk = async (rel: string) => {
        const entries = await readdir(join(skillDir, skill.name, rel), { withFileTypes: true })
        for (const f of entries) {
          const r = rel ? `${rel}/${f.name}` : f.name
          if (f.isDirectory()) {
            await walk(r)
            continue
          }
          let text = await readFile(join(skillDir, skill.name, r), "utf8")
          text = text.split("<WORKSHOP_DIR>").join(WORKSHOP_DIR)
          for (const [k, v] of Object.entries(vars)) {
            text = text.split(`{{${k}}}`).join(v)
          }
          files[`.opencode/skill/${skill.name}/${r}`] = text
          if (r === "AGENTS.md") files["AGENTS.md"] = text
        }
      }
      await walk("")
    }
  } catch {}
  return files
}

export async function renderTemplate(packId: string, template: string, vars: Record<string, string>) {
  const { readdir, readFile } = await import("node:fs/promises")
  const dir = join(PACKS_DIR, packId, "templates", template)
  const out: Record<string, string> = {}
  const walk = async (rel: string) => {
    for (const entry of await readdir(join(dir, rel), { withFileTypes: true })) {
      const r = rel ? `${rel}/${entry.name}` : entry.name
      if (entry.isDirectory()) {
        await walk(r)
        continue
      }
      let text = await readFile(join(dir, r), "utf8")
      for (const [k, v] of Object.entries(vars)) {
        text = text.split(`{{${k}}}`).join(v)
      }
      out[r.replace(/\.tpl$/, "")] = text
    }
  }
  await walk("")
  return out
}

/* ------------------------------------------------------------------ *
 * 工作台闭环内核:生成 → 落盘 → check → build
 * 让小白"一句话"生成的插件,能走完"规范校验 + 打包"这条可复用链路。
 * 说明:neko-plugin CLI 在 N.E.K.O 仓库根执行;插件目录可用绝对路径,
 *       不强制放进 N.E.K.O 源码树 (resolve_plugin_dir_candidate 支持)。
 * ------------------------------------------------------------------ */

export interface ScaffoldOptions {
  /** 落盘到的目标(绝对)路径 */
  targetDir: string
  /** N.E.K.O 仓库根(用于解析 neko-plugin CLI 与插件根) */
  nekoRepoRoot: string
  /** python 解释器,默认 "python" */
  python?: string
}

export interface CheckIssue {
  severity: "error" | "warning"
  file?: string
  line?: number
  message: string
  /** 给 Agent/小白的中文修复提示(能识别的模式才有) */
  hint?: string
}

export interface CheckSummary {
  ok: boolean
  exitCode: number
  errors: number
  warnings: number
  /** 结构化 issue 列表(已去重,含中文修复提示) */
  issues: CheckIssue[]
  raw: string
}

export interface BuildSummary {
  ok: boolean
  exitCode: number
  artifact?: string
  raw: string
}

export interface VerifyResult {
  /** 落盘的完整文件路径 → 内容 */
  files: Record<string, string>
  rootDir: string
  check: CheckSummary
  build?: BuildSummary
}

export interface VerifySummary {
  rootDir: string
  ok: boolean
  check: CheckSummary
  build?: BuildSummary
}

/** 对已有插件目录跑 check → build(check 过了才 build);修复循环的主入口 */
export async function verifyProject(
  nekoRepoRoot: string,
  pluginDir: string,
  opts: { python?: string; outPath?: string } = {},
): Promise<VerifySummary> {
  const check = await runCheck(nekoRepoRoot, pluginDir, opts.python ?? "python")
  let build: BuildSummary | undefined
  if (check.ok) {
    const out =
      opts.outPath ?? join(dirname(pluginDir), `${basename(pluginDir)}.neko-plugin`)
    build = await runBuild(nekoRepoRoot, pluginDir, opts.python ?? "python", out)
  }
  return { rootDir: pluginDir, ok: check.ok && (!build || build.ok), check, build }
}

/** 把 renderProject 输出的文件写入 targetDir(扁平结构,含 .openode 点文件) */
async function writeFiles(rootDir: string, files: Record<string, string>) {
  const { mkdir, writeFile } = await import("node:fs/promises")
  for (const [rel, text] of Object.entries(files)) {
    const dest = join(rootDir, rel)
    await mkdir(join(dest, ".."), { recursive: true })
    await writeFile(dest, text, "utf8")
  }
}

async function runSync(cmd: string, args: string[], cwd: string) {
  const { spawnSync } = await import("node:child_process")
  const res = spawnSync(cmd, args, { cwd, encoding: "utf8" })
  const stdout = (res.stdout ?? "").toString()
  const stderr = (res.stderr ?? "").toString()
  return { code: res.status ?? -1, raw: `${stdout}\n${stderr}`.trim() }
}

/** 按实测样本解析 check 输出;识别常见错误并附中文修复提示 */
export function parseCheckIssues(raw: string): CheckIssue[] {
  const issues: CheckIssue[] = []
  const seen = new Set<string>()
  const push = (i: CheckIssue) => {
    const key = `${i.severity}|${i.file ?? ""}|${i.line ?? ""}|${i.message}`
    if (seen.has(key)) return
    seen.add(key)
    issues.push(i)
  }
  for (const line of raw.split(/\r?\n/)) {
    // 致命行(清单本身解析不了):[FAIL] check: failed to parse TOML file '<path>': <原因>
    const fatal = line.match(/^\[FAIL\]\s*check:\s*(.+)$/)
    if (fatal) {
      const msg = fatal[1].trim()
      const toml = msg.match(/failed to parse TOML file '(.+?)':\s*(.+)$/)
      push(
        toml
          ? { severity: "error", file: toml[1], message: toml[2], hint: `plugin.toml 解析失败:${toml[2]}。常见原因是 UTF-8 BOM(Windows 记事本保存导致)或语法错误,用无 BOM UTF-8 重新保存` }
          : { severity: "error", message: msg },
      )
      continue
    }
    const err = line.match(/^\s*\[ERROR\]\s*(.+)$/)
    if (err) push(withHint(err[1].trim(), "error"))
    const warn = line.match(/^\s*\[WARNING\]\s*(.+)$/)
    if (warn) push({ severity: "warning", message: warn[1].trim() })
  }
  return issues
}

function withHint(message: string, severity: "error" | "warning"): CheckIssue {
  // Python syntax error in __init__.py: '{' was never closed at line 7
  const syn = message.match(/^Python syntax error in (.+?):\s*(.+?)\s+at line (\d+)$/)
  if (syn)
    return {
      severity,
      file: syn[1],
      line: Number(syn[3]),
      message: `Python 语法错误:${syn[2]}`,
      hint: `打开 ${syn[1]} 第 ${syn[3]} 行修复语法(常见:括号/引号未闭合、缩进错误、冒号遗漏)`,
    }
  // plugin.entry class 'NoSuchClass' was not found in __init__.py
  const cls = message.match(/^plugin\.entry class '(.+?)' was not found in (.+)$/)
  if (cls)
    return {
      severity,
      file: cls[2],
      message,
      hint: `plugin.toml 的 entry 指向的类 ${cls[1]} 在 ${cls[2]} 中不存在;确保 entry 格式为 plugin.plugins.<id>:<类名>,且代码里 @neko_plugin 类名与之一致`,
    }
  // [plugin].version must be a non-empty string
  const field = message.match(/^\[plugin\]\.(\w+)\s+(.+)$/)
  if (field)
    return {
      severity,
      message,
      hint: `在 plugin.toml 的 [plugin] 段补上 ${field[1]} 字段(参考模板 plugin.toml.tpl)`,
    }
  return { severity, message }
}

/** 在 N.E.K.O 仓库根跑 `neko-plugin check <dir>`,返回结构化摘要 */
export async function runCheck(
  nekoRepoRoot: string,
  pluginDir: string,
  python: string = "python",
): Promise<CheckSummary> {
  const { code, raw } = await runSync(
    python,
    ["plugin/neko_plugin_cli/cli.py", "check", pluginDir],
    nekoRepoRoot,
  )
  const issues = parseCheckIssues(raw)
  // check 进程崩溃(如文件编码损坏触发 Python traceback)时输出里没有 [ERROR] 行可解析,
  // 必须把原始输出尾部作为 issue 带回去——绝不让 Agent 拿到「有错误但零信息」的空 issues(实战踩过:Agent 因此瞎指挥)
  if (code !== 0 && !issues.some((i) => i.severity === "error")) {
    const lines = raw.split(/\r?\n/).filter((l) => l.trim())
    const tail = lines.slice(-4).join(" ⏎ ").slice(0, 400)
    const decode = /UnicodeDecodeError|can't decode|invalid .*byte|codec/i.test(raw)
    issues.push(
      decode
        ? {
            severity: "error",
            message: `文件不是合法 UTF-8,check 无法读取:${tail}`,
            hint: "不要重试同一条命令。用文件写入工具以 UTF-8 直接重写 plugin.toml(修正 name 等字段)与代码文件,再跑一次 verify",
          }
        : {
            severity: "error",
            message: `check 异常退出(exit ${code}),未能解析出结构化错误:${tail || "(无输出)"}`,
            hint: "直接读取插件目录里的文件排查(优先 plugin.toml 与 __init__.py),不要盲目重试同一命令",
          },
    )
  }
  const m = raw.match(/(\d+)\s*error\(s\)\s*,\s*(\d+)\s*warning\(s\)/)
  const errCount = issues.filter((i) => i.severity === "error").length
  const errors = Math.max(m ? Number(m[1]) : 0, errCount, code === 0 ? 0 : 1)
  const warnings = m ? Number(m[2]) : issues.filter((i) => i.severity === "warning").length
  return { ok: code === 0, exitCode: code, errors, warnings, issues, raw }
}

/** 在 N.E.K.O 仓库根跑 `neko-plugin build <dir>`,返回产物路径;默认产物目录 dist_pack,outPath 指定则落到该文件 */
export async function runBuild(
  nekoRepoRoot: string,
  pluginDir: string,
  python: string = "python",
  outPath?: string,
): Promise<BuildSummary> {
  const args = ["plugin/neko_plugin_cli/cli.py", "build", pluginDir]
  if (outPath) args.push("--out", outPath)
  else args.push("-t", join(nekoRepoRoot, "plugin", "dist_pack"))
  const { code, raw } = await runSync(python, args, nekoRepoRoot)
  const artifact =
    raw.match(/[A-Za-z]:\\[^\s"]*\.neko-(?:plugin|bundle)/)?.[0] ??
    raw.match(/[\w/\\-]*\.neko-(?:plugin|bundle)/)?.[0]
  return { ok: code === 0, exitCode: code, artifact, raw }
}

/**
 * 工作台核心闭环:生成并落盘插件,跑 check(有错返回 error 摘要),
 * check 通过后跑 build,产出 .neko-plugin。全部成功才算一次完整通过。
 */
export async function scaffoldAndVerify(
  packId: string,
  template: string,
  vars: Record<string, string>,
  opts: ScaffoldOptions,
): Promise<VerifyResult> {
  const files = await renderProject(packId, template, vars)
  const rootDir = join(opts.targetDir, vars.PLUGIN_ID ?? "plugin")
  await writeFiles(rootDir, files)

  const result = await verifyProject(opts.nekoRepoRoot, rootDir, {
    python: opts.python,
    outPath: join(opts.targetDir, `${vars.PLUGIN_ID}.neko-plugin`),
  })
  return { files, ...result }
}
