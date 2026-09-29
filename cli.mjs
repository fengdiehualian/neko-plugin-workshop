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
import { scaffoldAndVerify, verifyProject } from "./src/index"

const USAGE = `用法:
  wb-plugin scaffold <packId> <template> [--out <dir>] [--neko <repo>] [--python <path>] [--strict] [--VAR v ...]
    变量:--PLUGIN_ID / --PLUGIN_NAME / --CLASS_NAME(必填)
  wb-plugin verify   <pluginDir> [--neko <repo>] [--python <path>] [--out <file>] [--strict]
    对已有插件目录跑 check→build(修复循环主入口);输出含结构化 issues
    --strict:warning 一律视为失败(warnings>0 ⇒ check 不通过,跳过 build)
    --python:NEKO 源码 SDK 依赖所在的解释器(默认 "python",可用环境变量 WB_PYTHON)
例子:
  wb-plugin scaffold neko-plugin reminder --out C:/projects --neko C:/dev/N.E.K.O --PLUGIN_ID poetry --PLUGIN_NAME 每日诗词 --CLASS_NAME Poetry
  wb-plugin verify C:/projects/poetry --neko C:/dev/N.E.K.O`

function fail(msg, extra) {
  console.log(JSON.stringify({ ok: false, error: msg, ...(extra || {}) }))
  process.exit(1)
}

async function main() {
  const argv = process.argv.slice(2)
  const command = argv[0]
  if (!command || (command !== "scaffold" && command !== "verify")) {
    fail(USAGE)
  }

  // 收集 flags 与位置参数
  const flags = {}
  const vars = {}
  const positional = []
  for (let i = 1; i < argv.length; i++) {
    const a = argv[i]
    if (a.startsWith("--")) {
      const key = a.slice(2)
      if (i + 1 < argv.length && !argv[i + 1].startsWith("--")) {
        const val = argv[i + 1]
        if (key === "out") flags.targetDir = val
        else if (key === "neko") flags.nekoRepoRoot = val
        else if (key === "python") flags.python = val
        else vars[key] = val
        i++
      } else {
        flags[key] = true
      }
    } else {
      positional.push(a)
    }
  }

  if (command === "verify") {
    if (!flags.nekoRepoRoot) flags.nekoRepoRoot = process.env.WB_NEKO_REPO
    if (!positional[0] || !flags.nekoRepoRoot) {
      fail("缺少 <pluginDir> 或 --neko(或环境变量 WB_NEKO_REPO)\n" + USAGE)
    }
    try {
      const result = await verifyProject(flags.nekoRepoRoot, positional[0], {
        python: flags.python || process.env.WB_PYTHON || "python",
        outPath: flags.targetDir,
        strict: flags.strict === true || process.env.WB_STRICT === "1",
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
      { PLUGIN_ID: vars.PLUGIN_ID, PLUGIN_NAME: vars.PLUGIN_NAME, CLASS_NAME: vars.CLASS_NAME },
      { targetDir: flags.targetDir, nekoRepoRoot: flags.nekoRepoRoot, python: flags.python || process.env.WB_PYTHON || "python", strict: flags.strict === true || process.env.WB_STRICT === "1" },
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
