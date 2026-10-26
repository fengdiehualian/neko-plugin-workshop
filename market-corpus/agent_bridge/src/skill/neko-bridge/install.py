#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 ``neko-bridge`` skill 装到本机各个 Agent 软件里。

用法
----
    python install.py --list                 # 只列出探测到的目标与状态
    python install.py --dry-run              # 默认：显示将要写入的文件（不落盘）
    python install.py --apply                # 真的装（只写 <目标>/neko-bridge/，不动别的东西）
    python install.py --apply --only claude,codex
    python install.py --mcp-config           # 打印各家的 MCP 注册片段
    python install.py --apply --home /tmp/x  # 装到别处（测试用）

设计约束
--------
* **只增不改**：每个目标目录里只创建 ``neko-bridge/``，不碰别的 skill、不覆盖已有文件（除非 ``--force``）。
* **不发明目录**：目标 skills 目录不存在就跳过（列出来标 ``missing``），不会替某个 Agent 创造出它的配置结构。
* **备份目录跳过**：带「副本 / copy / backup」字样的 workspace/preset 不装，避免污染备份。
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

SKILL_NAME = "neko-bridge"
HERE = Path(__file__).resolve().parent
PAYLOAD = ("SKILL.md", "README.md", "bin")

#: 目标表：``(key, 展示名, skills 目录模板)``。模板里的 ``*`` 会展开成所有匹配目录。
TARGETS: tuple[tuple[str, str, str], ...] = (
    ("agents", "通用 .agents（多数 harness 的公共位）", "~/.agents/skills"),
    ("claude", "Claude Code", "~/.claude/skills"),
    ("codex", "OpenAI Codex", "~/.codex/skills"),
    ("omp", "Oh My Pi (omp)", "~/.omp/agent/skills"),
    ("evox", "EvoX", "~/.evox/agent/skills"),
    ("dsh", "DeepSeek Harness (dsh)", "~/.dsh/.agent-presets/*/skills"),
    ("qwenpaw", "QwenPaw", "~/.qwenpaw/workspaces/*/skills"),
    ("openfang", "OpenFang", "~/.openfang/workspaces/*/skills"),
    ("workbuddy", "WorkBuddy", "~/.workbuddy/skills"),
    ("doubao", "Doubao", "~/Doubao/skills"),
    ("doubaowork", "DoubaoWork", "~/DoubaoWork/skills"),
    ("pencil", "Pencil", "~/.pencil/skills"),
)

#: 这些字样命中的目录当作备份，跳过。
SKIP_MARKERS = ("副本", "copy", "backup", "bak", "backup-")

MCP_SNIPPETS: dict[str, str] = {
    "claude": "claude mcp add neko-bridge -- python \"{bin}\" mcp",
    "codex": "codex mcp add neko-bridge -- python \"{bin}\" mcp",
    "generic": (
        '{\n'
        '  "mcpServers": {\n'
        '    "neko-bridge": {\n'
        '      "command": "python",\n'
        '      "args": ["{bin}", "mcp"]\n'
        '    }\n'
        '  }\n'
        '}'
    ),
}


def expand(template: str, home: Path, key: str) -> list[Path]:
    raw = template.replace("~", str(home))
    if "*" not in raw:
        return [Path(raw)]
    parent, _, tail = raw.partition("*/")
    base = Path(parent.rstrip("/\\"))
    if not base.is_dir():
        return []
    return sorted(
        path / tail
        for path in base.iterdir()
        if path.is_dir() and not any(marker in path.name.lower() for marker in SKIP_MARKERS)
    )


def state_of(directory: Path) -> str:
    if not directory.is_dir():
        return "missing"
    if (directory / SKILL_NAME / "SKILL.md").is_file():
        return "installed"
    return "ready"


def install(directory: Path, *, force: bool) -> list[str]:
    """把 skill 拷进 ``directory/neko-bridge``，返回写入的文件（相对路径）。"""
    target = directory / SKILL_NAME
    written: list[str] = []
    target.mkdir(parents=True, exist_ok=True)
    for name in PAYLOAD:
        source = HERE / name
        if not source.exists():
            continue
        destination = target / name
        if destination.exists() and not force:
            continue
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(source, destination)
        written.append(name)
    if written and not (target / "bin" / "neko.py").exists():
        return written
    if written:
        (target / "bin" / "neko.py").chmod(0o755)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="install-neko-bridge", description="把 neko-bridge 装到本机各 Agent")
    parser.add_argument("--home", default=str(Path.home()), help="用户目录（测试时可换）")
    parser.add_argument("--list", action="store_true", help="只列出目标与状态")
    parser.add_argument("--dry-run", action="store_true", help="显示将要写入的内容（默认行为）")
    parser.add_argument("--apply", action="store_true", help="真的写入")
    parser.add_argument("--only", default="", help="只处理这些 key（逗号分隔）")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的文件")
    parser.add_argument("--mcp-config", action="store_true", help="打印 MCP 注册片段")
    args = parser.parse_args(argv)

    home = Path(args.home).expanduser()
    wanted = {item.strip() for item in args.only.split(",") if item.strip()}
    rows: list[tuple[str, str, Path, str]] = []
    for key, label, template in TARGETS:
        if wanted and key not in wanted:
            continue
        for directory in expand(template, home, key):
            rows.append((key, label, directory, state_of(directory)))

    if args.mcp_config:
        # 用 replace 而不是 str.format：JSON 片段里的花括号会被 format 当成字段（踩过 KeyError）。
        binary = str((HERE / "bin" / "neko.py").resolve())
        print("# Claude Code")
        print(MCP_SNIPPETS["claude"].replace("{bin}", binary))
        print("\n# OpenAI Codex")
        print(MCP_SNIPPETS["codex"].replace("{bin}", binary))
        print("\n# 其它支持 MCP 的 Agent（把这段贴进它的 mcp 配置）")
        # 用 json.dumps 生成：手工拼字符串会把 Windows 路径的单反斜杠写成非法转义（\U…）
        import json as _json

        print(
            _json.dumps(
                {"mcpServers": {"neko-bridge": {"command": "python", "args": [binary, "mcp"]}}},
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    print(f"skill 源：{HERE}")
    print(f"{'key':10} {'状态':10} 目录")
    for key, label, directory, state in rows:
        print(f"{key:10} {state:10} {directory}  ({label})")

    if args.list or not args.apply:
        pending = [row for row in rows if row[3] in ("ready", "installed")]
        print(f"\n可写入的目标：{len(pending)} 个；缺失（该 Agent 没装/没建过）的跳过："
              f"{len([r for r in rows if r[3] == 'missing'])} 个")
        if not args.apply:
            print("这是 dry-run（默认）。要真的安装：--apply")
        return 0

    total = 0
    for key, _label, directory, state in rows:
        if state == "missing":
            continue
        written = install(directory, force=args.force)
        total += len(written)
        marker = "更新" if state == "installed" else "安装"
        print(f"{marker} {directory / SKILL_NAME}: {', '.join(written) if written else '（已是最新，跳过）'}")
    print(f"\n完成：{total} 个文件写入。自检：python \"{HERE / 'bin' / 'neko.py'}\" doctor")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
