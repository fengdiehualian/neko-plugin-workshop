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
  wb-plugin scaffold <packId> <template> [--out <dir>] [--neko <repo>] [--VAR v ...]
    变量:--PLUGIN_ID / --PLUGIN_NAME / --CLASS_NAME(必填)
  wb-plugin verify <pluginDir> --neko <repo> [--out <file>]
    对已有插件目录跑 check→build(修复循环主入口);输出含结构化 issues
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
        python: "python",
        outPath: flags.targetDir,
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
      { targetDir: flags.targetDir, nekoRepoRoot: flags.nekoRepoRoot, python: "python" },
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
      // 供 Agent 报告给小白
      files: Object.keys(result.files),
    }
    console.log(JSON.stringify(payload, null, 2))
    if (!payload.ok) process.exit(1)
  } catch (e) {
    fail(String(e))
  }
}

main()
