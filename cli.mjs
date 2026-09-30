#!/usr/bin/env bun
/**
 * N.E.K.O. 工作台 - 命令行入口
 * 用途:让 Agent(或命令行)能稳定触发「生成 → 落盘 → check → build」,
 *      以及修复循环的「对已有插件目录重新 verify」。
 *
 * 用法(bun 运行):
 *   wb-plugin scaffold <packId> <template> [--out <dir>] [--neko <repo>] [--VAR v ...]
 *   wb-plugin verify   <pluginDir> [--neko <repo>] [--out <file>]
 *
 * 成功:退出码 0,stdout 打印 JSON。
 * 失败:非 0 退出码,stdout 打印 JSON(含结构化 issues)便于 Agent 解析。
 */

// eslint-disable-next-line n/no-unsupported-features/node-builtins
import { parseArgs } from "node:util"
import { resolve } from "node:path"
import { scaffoldAndVerify, verifyProject } from "./src/index"

const USAGE = `用法:
  wb-plugin scaffold <packId> <template> [--out <dir>] [--neko <repo>] [--python <path>] [--strict] --PLUGIN_ID id --PLUGIN_NAME 名称 --CLASS_NAME 类名 [--PLUGIN_AUTHOR 作者]
    变量:--PLUGIN_ID / --PLUGIN_NAME / --CLASS_NAME(必填);--PLUGIN_AUTHOR 可选(默认读本机 git 身份)
  wb-plugin verify   <pluginDir> [--neko <repo>] [--python <path>] [--out <file>] [--strict]
    对已有插件目录跑 check→build(修复循环主入口);输出含结构化 issues
    --strict:代码质量类 warning 一律视为失败(仓库状态类 origin/工作树不计);它是开关,不带值
    --python:NEKO 源码 SDK 依赖所在的解释器(默认 "python",可用环境变量 WB_PYTHON)
例子:
  wb-plugin scaffold neko-plugin reminder --out C:/projects --neko C:/dev/N.E.K.O --PLUGIN_ID poetry --PLUGIN_NAME 每日诗词 --CLASS_NAME Poetry
  wb-plugin verify C:/projects/poetry --neko C:/dev/N.E.K.O --strict`

function fail(msg, extra) {
  console.log(JSON.stringify({ ok: false, error: msg, ...(extra || {}) }))
  process.exit(1)
}

async function main() {
  // 用 node:util parseArgs(Issue #3):手写解析器会把 --strict 后面的位置参数当成它的值,
  // `verify --strict C:/proj` 直接坏掉;布尔选项必须声明
  let parsed
  try {
    parsed = parseArgs({
      args: process.argv.slice(2),
      allowPositionals: true,
      options: {
        out: { type: "string" },
        neko: { type: "string" },
        python: { type: "string" },
        strict: { type: "boolean" },
        PLUGIN_ID: { type: "string" },
        PLUGIN_NAME: { type: "string" },
        CLASS_NAME: { type: "string" },
        PLUGIN_AUTHOR: { type: "string" },
      },
    })
  } catch (e) {
    fail(`${e && e.message ? e.message : e}\n${USAGE}`)
  }
  const { values, positionals } = parsed
  const command = positionals[0]
  const positional = positionals.slice(1)
  if (!command || (command !== "scaffold" && command !== "verify")) {
    fail(USAGE)
  }
  const flags = {
    targetDir: values.out,
    nekoRepoRoot: values.neko,
    python: values.python,
    strict: values.strict === true || process.env.WB_STRICT === "1",
  }
  const vars = {
    PLUGIN_ID: values.PLUGIN_ID,
    PLUGIN_NAME: values.PLUGIN_NAME,
    CLASS_NAME: values.CLASS_NAME,
    PLUGIN_AUTHOR: values.PLUGIN_AUTHOR,
  }

  if (command === "verify") {
    if (!flags.nekoRepoRoot) flags.nekoRepoRoot = process.env.WB_NEKO_REPO
    if (!positional[0] || !flags.nekoRepoRoot) {
      fail("缺少 <pluginDir> 或 --neko(或环境变量 WB_NEKO_REPO)\n" + USAGE)
    }
    try {
      // 相对路径一律按进程 cwd 解析成绝对路径(Issue #3):check/build 以 nekoRepoRoot 为 cwd,
      // 不解析会"写一处、查另一处"
      const result = await verifyProject(resolve(flags.nekoRepoRoot), resolve(positional[0]), {
        python: flags.python || process.env.WB_PYTHON || "python",
        outPath: flags.targetDir ? resolve(flags.targetDir) : undefined,
        strict: flags.strict,
      })
      console.log(
        JSON.stringify(
          {
            ok: result.ok,
            rootDir: result.rootDir,
            check: {
              ok: result.check.ok,
              errors: result.check.errors,
              warnings: result.check.warnings,
              issues: result.check.issues,
            },
            build: result.build
              ? { ok: result.build.ok, exitCode: result.build.exitCode, artifact: result.build.artifact }
              : null,
          },
          null,
          2,
        ),
      )
      if (!result.ok) process.exit(1)
    } catch (e) {
      fail(String(e))
    }
    return
  }

  // scaffold
  const packId = positional[0]
  const template = positional[1]
  if (!packId || !template) {
    fail(USAGE)
  }

  if (!flags.targetDir) flags.targetDir = process.env.WB_PROJECTS_DIR
  if (!flags.nekoRepoRoot) flags.nekoRepoRoot = process.env.WB_NEKO_REPO
  if (!flags.targetDir || !flags.nekoRepoRoot) {
    fail("缺少 --out/--neko(或环境变量 WB_PROJECTS_DIR / WB_NEKO_REPO)\n" + USAGE)
  }
  if (!vars.PLUGIN_ID || !vars.PLUGIN_NAME || !vars.CLASS_NAME) {
    fail("缺少 --PLUGIN_ID / --PLUGIN_NAME / --CLASS_NAME\n" + USAGE)
  }

  try {
    const result = await scaffoldAndVerify(
      packId,
      template,
      {
        PLUGIN_ID: vars.PLUGIN_ID,
        PLUGIN_NAME: vars.PLUGIN_NAME,
        CLASS_NAME: vars.CLASS_NAME,
        PLUGIN_AUTHOR: vars.PLUGIN_AUTHOR ?? "",
      },
      {
        targetDir: resolve(flags.targetDir),
        nekoRepoRoot: resolve(flags.nekoRepoRoot),
        python: flags.python || process.env.WB_PYTHON || "python",
        strict: flags.strict,
      },
    )

    const payload = {
      ok: result.check.ok && (!result.build || result.build.ok),
      rootDir: result.rootDir,
      check: {
        ok: result.check.ok,
        errors: result.check.errors,
        warnings: result.check.warnings,
        issues: result.check.issues,
        exitCode: result.check.exitCode,
      },
      build: result.build
        ? { ok: result.build.ok, exitCode: result.build.exitCode, artifact: result.build.artifact }
        : null,
      // setup-repo --git --github-actions:补齐 .vscode/.github/workflows 等上架合规文件,
      // 全新仓库自动做奠基提交(commit 字段)
      setupRepo: result.setupRepo
        ? { ok: result.setupRepo.ok, git: result.setupRepo.git, commit: result.setupRepo.commit }
        : null,
      // 供 Agent 报告给小白
      files: Object.keys(result.files),
      // 发布上架路径:本地产物 ≠ 已发布(Market 按 GitHub Release 验收,细则见 skill 的 references/cli.md)
      nextSteps: [
        `在 GitHub 创建独立仓库 n.e.k.o_plugin_${vars.PLUGIN_ID},git remote add origin 后推送全部提交`,
        `提交 Market 首次审核(见 references/cli.md 的投稿步骤)`,
        `审核通过后运行 neko-plugin publish ${vars.PLUGIN_ID}:创建 GitHub Release 并发布 Market 版本`,
        "本地 .neko-plugin 只是安装包,不能代替 GitHub Release,严禁复制目录/安装包导入/符号链接伪装发布",
      ],
    }
    console.log(JSON.stringify(payload, null, 2))
    if (!payload.ok) process.exit(1)
  } catch (e) {
    fail(String(e))
  }
}

main()
