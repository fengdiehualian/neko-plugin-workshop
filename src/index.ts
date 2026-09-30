import { join, dirname, basename, resolve } from "node:path"
import { spawnSync } from "node:child_process"
import { existsSync } from "node:fs"
import { mkdir, writeFile } from "node:fs/promises"

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
  /** strict 模式:warning 一律视为失败 */
  strict?: boolean
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
  /** 官方 setup-repo(--git --github-actions)结果:补齐 .vscode/.github/workflows 等上架合规文件 */
  setupRepo?: { ok: boolean; git: boolean; commit: boolean | null; raw: string }
}

/** rules.json 声明的插件 ID 约束,内核入口强制执行(目录名/发布名都由它拼出来) */
export const PLUGIN_ID_PATTERN = /^[a-z][a-z0-9_]*$/

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
  opts: { python?: string; outPath?: string; strict?: boolean } = {},
): Promise<VerifySummary> {
  const check = await runCheck(nekoRepoRoot, pluginDir, opts.python ?? "python", opts.strict === true)
  const finalCheck = opts.strict ? applyStrict(check) : check
  let build: BuildSummary | undefined
  if (finalCheck.ok) {
    const out =
      opts.outPath ?? join(dirname(pluginDir), `${basename(pluginDir)}.neko-plugin`)
    build = await runBuild(nekoRepoRoot, pluginDir, opts.python ?? "python", out)
  }
  return { rootDir: pluginDir, ok: finalCheck.ok && (!build || build.ok), check: finalCheck, build }
}

/**
 * 仓库状态类 warning(Issue #3):新脚手架没有 origin、Agent 修复过程中工作树必然是脏的 ——
 * 这些是"发布状态"而非代码质量,让它们判负会使 strict 永远不过、build 被跳过、修复循环空转
 * (甚至诱导 Agent 伪造 remote)。仍保留在 issues 里展示,只是不计入 strict 判负。
 */
const REPO_STATE_WARNING = /git remote|working tree|own git repository/i

/**
 * strict 模式:代码质量类 warning 一律视为失败(仓库状态类除外,见上)。
 * check 本身有错误或零判负 warning 时原样返回;否则把 check 判为不通过并附汇总 issue,
 * 让修复循环(或调用方)在 build 之前把 warning 清零。
 */
export function applyStrict(check: CheckSummary): CheckSummary {
  if (!check.ok || check.warnings === 0) return check
  const repoState = check.issues.filter(
    (i) => i.severity === "warning" && REPO_STATE_WARNING.test(i.message),
  ).length
  const blocking = check.warnings - repoState
  if (blocking <= 0) return check
  return {
    ...check,
    ok: false,
    issues: [
      ...check.issues,
      {
        severity: "error",
        message: `strict 模式:${blocking} 个 warning 视为错误${repoState ? `(已忽略 ${repoState} 个仓库状态类警告)` : ""},check 不通过`,
        hint: "逐条修复上方 warning(每条都带 fix 建议)后重跑 verify --strict;origin/工作树等仓库状态类警告不影响打包",
      } satisfies CheckIssue,
    ],
  }
}

/** 把 renderProject 输出的文件写入 targetDir(扁平结构,含 .openode 点文件) */
async function writeFiles(rootDir: string, files: Record<string, string>) {
  for (const [rel, text] of Object.entries(files)) {
    const dest = join(rootDir, rel)
    await mkdir(join(dest, ".."), { recursive: true })
    await writeFile(dest, text, "utf8")
  }
}

async function runSync(cmd: string, args: string[], cwd: string) {
  // PYTHONDONTWRITEBYTECODE:check/build 会 import 插件源码,缺省会在源码树(乃至 build
  // 暂存 payload)里落 __pycache__/*.pyc 并被打进 .neko-plugin——禁止写字节码缓存
  const res = spawnSync(cmd, args, { cwd, encoding: "utf8", env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } })
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
  strict: boolean = false,
): Promise<CheckSummary> {
  const args = ["plugin/neko_plugin_cli/cli.py", "check", pluginDir]
  // NEKO 原生 -s:把仓库支持文件缺失类 warning 直接升格为 error(check_cmd.py "Treat
  // missing repository support files as errors");残余 warning 由 applyStrict 兜底判失败
  if (strict) args.push("-s")
  const { code, raw } = await runSync(python, args, nekoRepoRoot)
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
  // outPath 是调用方指定的产物路径,直接作为 artifact;日志反向匹配只兜底无 --out 的直调场景
  // (旧正则不含 . / 空格 / 中文,像 /Users/a/proj.v2/x.neko-plugin 会被截断)
  const artifact =
    outPath ??
    raw.match(/[A-Za-z]:\\[^\s"]*\.neko-(?:plugin|bundle)/)?.[0] ??
    raw.match(/[^\s"']+\.neko-(?:plugin|bundle)/)?.[0]
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
  // rules.json 的 ^[a-z][a-z0-9_]*$ 必须在入口强制:PLUGIN_ID 直接拼进落盘路径与产物名,
  // 带 ../ 会写到目录外,空串会把模板文件散落在 out 根目录
  const pluginId = String(vars.PLUGIN_ID ?? "")
  if (!PLUGIN_ID_PATTERN.test(pluginId)) {
    throw new Error(
      `PLUGIN_ID 不合法:${JSON.stringify(pluginId)}(要求 ^[a-z][a-z0-9_]*$:小写字母开头,仅小写字母/数字/下划线,不能为空)`,
    )
  }
  // 作者身份(Issue #3):PLUGIN_AUTHOR 变量 > 本机 git 身份 > 工坊占位名。
  // 模板 {{PLUGIN_AUTHOR}} 与奠基提交身份都用它,绝不冒挂官方域名
  const gitName = (await runSync("git", ["config", "user.name"], process.cwd())).raw.trim()
  const authorName = String(vars.PLUGIN_AUTHOR ?? "").trim() || gitName || "N.E.K.O. Workshop"
  const renderVars = { ...vars, PLUGIN_AUTHOR: authorName }
  const files = await renderProject(packId, template, renderVars)
  const targetDir = resolve(opts.targetDir) // 相对路径按进程 cwd 解析一次(Issue #3),与 check/build 的 cwd 无关
  const rootDir = join(targetDir, pluginId)
  // 开发技能(.opencode/**)不写进插件目录:否则被 N.E.K.O build 原样打进 .neko-plugin
  // (成品多 ~30 个无关条目,且点目录会触发 macOS codesign "bundle format unrecognized")。
  // 落到工作区根即可——引擎 cwd=工作区,agent 照常读到;分发给任意底座用 wb_install_skill。
  const projectFiles: Record<string, string> = {}
  const skillFiles: Record<string, string> = {}
  for (const [rel, text] of Object.entries(files)) {
    if (rel.startsWith(".opencode/")) skillFiles[rel] = text
    else projectFiles[rel] = text
  }
  await writeFiles(rootDir, projectFiles)
  if (Object.keys(skillFiles).length) await writeFiles(targetDir, skillFiles)

  // 独立 git 仓库(Issue #3):官方 setup-repo --git 遇到上层 .git 会跳过建仓,而 Market/publish
  // 要求插件目录本身是独立仓库 —— 输出目录在别的仓库内(如工坊 workspace)时必须自己建嵌套独立仓
  if (!existsSync(join(rootDir, ".git"))) {
    await runSync("git", ["init"], rootDir)
  }

  // 上架合规文件:官方 setup-repo 补 .vscode/.github/workflows(verify.yml/release.yml)/ruff.toml
  // ——缺失才写、不覆盖模板文件。先跑 --git --github-actions(一步到位),失败(如没装 git)
  // 再回落 --github-actions 保底生成;两步输出都带回结果(Issue #3),失败不阻断生成
  let setup = await runSync(
    opts.python ?? "python",
    ["plugin/neko_plugin_cli/cli.py", "setup-repo", rootDir, "--git", "--github-actions"],
    opts.nekoRepoRoot,
  )
  let setupRaw = setup.raw
  if (setup.code !== 0) {
    const fallback = await runSync(
      opts.python ?? "python",
      ["plugin/neko_plugin_cli/cli.py", "setup-repo", rootDir, "--github-actions"],
      opts.nekoRepoRoot,
    )
    setupRaw += "\n--- fallback (no --git) ---\n" + fallback.raw
    if (fallback.code === 0) setup = fallback
  }
  // 奠基提交:只对"全新空仓库"做(有历史的仓库绝不自动提交,免得卷走用户的 WIP)。
  // 身份按提交所在仓库(rootDir)探测 —— 外层仓库的 repo-local 身份对嵌套新仓库不生效;
  // 缺失才用中立占位邮箱(绝不挂官方域名,Issue #3)
  const gitReady = existsSync(join(rootDir, ".git"))
  let scaffoldCommit: boolean | null = null
  if (gitReady) {
    const head = await runSync("git", ["rev-parse", "--verify", "HEAD"], rootDir)
    if (head.code !== 0) {
      await runSync("git", ["add", "-A"], rootDir)
      const commitName = (await runSync("git", ["config", "user.name"], rootDir)).raw.trim()
      const commitEmail = (await runSync("git", ["config", "user.email"], rootDir)).raw.trim()
      const identityArgs: string[] = []
      if (!commitName) identityArgs.push("-c", `user.name=${authorName}`)
      if (!commitEmail) identityArgs.push("-c", "user.email=noreply@neko-workshop.invalid")
      const commit = await runSync(
        "git",
        [...identityArgs, "commit", "-m", `chore: scaffold ${pluginId} via N.E.K.O. workshop`],
        rootDir,
      )
      scaffoldCommit = commit.code === 0
      setupRaw += "\n--- scaffold commit ---\n" + commit.raw
    }
  }

  const result = await verifyProject(opts.nekoRepoRoot, rootDir, {
    python: opts.python,
    outPath: join(targetDir, `${pluginId}.neko-plugin`),
    strict: opts.strict,
  })
  return {
    files,
    setupRepo: {
      ok: setup.code === 0,
      git: gitReady,
      commit: scaffoldCommit,
      raw: setupRaw,
    },
    ...result,
  }
}
