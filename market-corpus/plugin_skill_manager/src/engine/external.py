"""用户文件夹里的 Agent Skill（外部根）：发现、去重、以目录联接（junction）挂载。

设计取舍
--------
用户 skill 大多是「给 Agent 看的指令包」，动辄几十上百 MB（``pptx-swarm`` 带一个
261 MB 的 ``scripts/kimi_ppt_dsl.pyz``）。整份复制进插件目录会把插件的
``skills/`` 撑到几百 MB，而宿主同步（``plugin/server/.../layout_migration.py``、
``installation_transactions/replace.py``）是**整树复制**，复制一份就是几秒到几十秒的
启动开销。

因此这里用 **Windows 目录联接（junction）**：``<plugin>/skills/<name>`` 只是一个指向
``~/.agents/skills/<name>`` 的重解析点，零拷贝、零维护、源目录改了立刻生效。
普通符号链接需要管理员权限（本机实测失败），``mklink /J`` 不需要。

不支持联接时（非 Windows / 创建失败）退回复制，并跳过 ``.git``、``__pycache__``
与超过 ``COPY_MAX_BYTES`` 的单个大文件。
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .registry import SkillFormatError, parse_skill_md

#: 单个文件超过这个体积就不复制（仅在退回复制模式时生效）。0 表示不限。
COPY_MAX_BYTES = 5 * 1024 * 1024

#: 命中这些字样的目录一律跳过：备份 / 第三方包 / 缓存。
SKIP_MARKERS = (
    "副本",
    "copy",
    "backup",
    "bak",
    "node_modules",
    "__pycache__",
    ".git",
    "site-packages",
)

#: 相对用户目录的 skills 根候选（存在才采用）。靠前者优先。
ROOT_PATTERNS = (
    ".agents/skills",
    ".claude/skills",
    ".codex/skills",
    ".zcode/skills",
    ".workbuddy/skills",
    ".pencil/skills",
    ".cursor/skills",
    ".cline/skills",
    ".gemini/skills",
    ".copilot/skills",
    ".qoder/skills",
    ".qwenpaw/skills",
    ".kimi-code/skills",
    ".atomcode/skills",
    ".commandcode/skills",
    ".kiro/skills",
    ".trae/skills",
    ".trae-cn/skills",
    "Doubao/skills",
    "DoubaoWork/skills",
    "WorkBuddy/skills",
    ".omp/agent/skills",
    ".evox/agent/skills",
    "*/.agent/skills",
    "*/.agents/skills",
    "*/.claude/skills",
    "*/.codex/skills",
    "*/skills",
)

REPARSE_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


@dataclass
class ExternalSkill:
    """一个来自用户文件夹的 Skill（已通过规范校验）。"""

    name: str
    src: Path
    root: str
    description: str
    license: str = ""
    metadata: dict | None = None

    def summary(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "dir": str(self.src),
            "license": self.license,
            "metadata": dict(self.metadata or {}),
        }


# ---------------------------------------------------------------------------
# 发现
# ---------------------------------------------------------------------------


def _skipped(path: Path) -> bool:
    lowered = path.name.casefold()
    return any(marker in lowered for marker in SKIP_MARKERS)


def _home_is_valid(p: Path) -> bool:
    """家目录必须存在且确实是个目录——否则 ``home`` 配错了会把整个扫描带偏。"""
    try:
        return p.is_dir()
    except OSError:
        return False


def candidate_roots(home: Path, extra: list[str] | None = None) -> list[Path]:
    """展开 ROOT_PATTERNS，返回真实存在的 skills 根（去重、保序）。"""
    home = Path(home)
    if not _home_is_valid(home):
        # 回退到真实用户目录：配置写错时宁可扫真实的，也不要静默扫出 0 个
        real = Path(os.path.expanduser("~"))
        if _home_is_valid(real) and os.path.normcase(str(real)) != os.path.normcase(str(home)):
            home = real
    found: list[Path] = []
    seen: set[str] = set()

    def add(p: Path) -> None:
        try:
            if not p.is_dir():
                return
        except OSError:
            return
        if _skipped(p):
            return
        key = os.path.normcase(os.path.realpath(p))
        if key in seen:
            return
        seen.add(key)
        found.append(p)

    for pattern in ROOT_PATTERNS:
        if "*" not in pattern:
            add(home / pattern)
            continue
        parent, _, tail = pattern.partition("/")
        base = home
        if parent != "*":
            base = home / parent
        try:
            children = sorted(base.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_dir():
                add(child / tail)

    for raw in extra or []:
        if not raw:
            continue
        add(Path(os.path.expandvars(os.path.expanduser(str(raw)))))

    return found


def root_rank(root: Path, home: Path) -> int:
    """根的优先级：与 ROOT_PATTERNS 顺序一致，未列出的排最后。"""
    try:
        rel = root.relative_to(home).as_posix()
    except ValueError:
        return len(ROOT_PATTERNS) + 1
    if rel in ROOT_PATTERNS:
        return ROOT_PATTERNS.index(rel)
    # 通配模式命中：归到对应模式的位置
    for idx, pattern in enumerate(ROOT_PATTERNS):
        if "*" not in pattern:
            continue
        parent, _, tail = pattern.partition("/")
        if rel.endswith("/" + tail) and (parent == "*" or rel.startswith(parent + "/")):
            return idx
    return len(ROOT_PATTERNS) + 1


def _read_skill(skill_dir: Path) -> dict:
    """按规范解析并校验一份 SKILL.md，失败抛 SkillFormatError。"""
    data = parse_skill_md(skill_dir / "SKILL.md")
    name = data.get("name")
    if not isinstance(name, str) or name != skill_dir.name:
        raise SkillFormatError(f"name {name!r} != directory name")
    desc = data.get("description")
    if not isinstance(desc, str) or len(desc.strip()) < 8:
        raise SkillFormatError("'description' is missing or too short")
    meta = data.get("metadata") or {}
    if not isinstance(meta, dict):
        raise SkillFormatError("'metadata' must be a mapping")
    return data


def scan(
    home: Path,
    extra_roots: list[str] | None = None,
) -> tuple[list[ExternalSkill], list[str]]:
    """扫描用户文件夹里的 Skill。

    同名多副本：按根的优先级取第一份，其余的记进 ``notes``。
    与插件自带 Skill 的同名冲突由调用方处理（它才知道哪些是自带的）。
    """
    home = Path(home)
    notes: list[str] = []
    by_name: dict[str, list[tuple[int, str, ExternalSkill]]] = {}

    for root in candidate_roots(home, extra_roots):
        rank = root_rank(root, home)
        try:
            rel_root = root.relative_to(home).as_posix()
        except ValueError:
            rel_root = str(root)
        try:
            entries = sorted(root.iterdir())
        except OSError as exc:
            notes.append(f"{rel_root}: cannot list ({exc})")
            continue
        for d in entries:
            if not d.is_dir() or _skipped(d):
                continue
            if not (d / "SKILL.md").is_file():
                continue
            try:
                data = _read_skill(d)
            except SkillFormatError as exc:
                notes.append(f"{rel_root}/{d.name}: 不合规（{exc}）")
                continue
            skill = ExternalSkill(
                name=d.name,
                src=d,
                root=rel_root,
                description=str(data["description"]).strip(),
                license=str(data.get("license", "") or ""),
                metadata=data.get("metadata") or {},
            )
            by_name.setdefault(d.name.casefold(), []).append((rank, rel_root, skill))

    out: list[ExternalSkill] = []
    for key in sorted(by_name):
        rows = sorted(by_name[key], key=lambda r: (r[0], r[1]))
        winner = rows[0][2]
        out.append(winner)
        others = [rel_root for _, rel_root, _ in rows[1:]]
        if others:
            notes.append(
                f"{winner.name}: 另有 {len(others)} 份副本（{', '.join(others)}），取 {winner.root}"
            )
    return out, notes


# ---------------------------------------------------------------------------
# 挂载：junction 优先，失败退回复制
# ---------------------------------------------------------------------------


def is_link(path: Path) -> bool:
    """是否为重解析点（junction / symlink）。"""
    try:
        st = path.lstat()
    except OSError:
        return False
    return bool(getattr(st, "st_file_attributes", 0) & REPARSE_ATTRIBUTE)


def link_status(dest: Path, src: Path) -> str:
    """``linked`` / ``stale`` / ``conflict`` / ``missing``。"""
    try:
        exists = dest.exists() or is_link(dest)
    except OSError:
        exists = False
    if not exists:
        return "missing"
    if not is_link(dest):
        return "conflict"
    try:
        same = os.path.normcase(os.path.realpath(dest)) == os.path.normcase(
            os.path.realpath(src)
        )
    except OSError:
        same = False
    return "linked" if same else "stale"


def make_junction(dest: Path, src: Path) -> bool:
    """``mklink /J``：目录联接，不需要管理员权限。"""
    if not sys.platform.startswith("win"):
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(  # noqa: S603 - 参数为固定字面量 + 本地路径
            ["cmd", "/c", "mklink", "/J", str(dest), str(src)],
            capture_output=True,
            check=False,
        )
    except OSError:
        return False
    if proc.returncode != 0:
        return False
    return link_status(dest, src) == "linked"


def _copy_ignore(_dir: str, names: list[str]):
    ignored = []
    for name in names:
        if name.casefold() in {"__pycache__", ".git", ".ruff_cache", ".pytest_cache"}:
            ignored.append(name)
    return ignored


def copy_skill(dest: Path, src: Path, max_bytes: int = COPY_MAX_BYTES) -> tuple[bool, list[str]]:
    """退回方案：整树复制（跳过 VCS/缓存目录与超大文件）。"""
    skipped: list[str] = []
    dest.mkdir(parents=True, exist_ok=True)
    for item in sorted(src.rglob("*")):
        rel = item.relative_to(src)
        if any(part.casefold() in {"__pycache__", ".git", ".ruff_cache", ".pytest_cache"} for part in rel.parts):
            continue
        target = dest / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if not item.is_file():
            continue
        if max_bytes and item.stat().st_size > max_bytes:
            skipped.append(f"{rel.as_posix()} ({item.stat().st_size} bytes)")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
    return True, skipped


def mount(dest: Path, src: Path, max_bytes: int = COPY_MAX_BYTES) -> tuple[str, list[str]]:
    """把 ``src`` 挂到 ``dest``。返回 (方式, 跳过的文件)。"""
    status = link_status(dest, src)
    if status == "linked":
        return "junction", []
    if status in {"stale", "conflict"}:
        unmount(dest)
    if make_junction(dest, src):
        return "junction", []
    ok, skipped = copy_skill(dest, src, max_bytes)
    return ("copy" if ok else "failed"), skipped


def unmount(dest: Path) -> bool:
    """摘掉一个联接或复制出来的目录。"""
    if not (dest.exists() or is_link(dest)):
        return False
    if is_link(dest):
        try:
            os.rmdir(dest)  # 联接本身是空目录项，rmdir 即可断开
            return True
        except OSError:
            pass
        try:
            subprocess.run(["cmd", "/c", "rmdir", str(dest)], capture_output=True, check=False)
        except OSError:
            return False
        return not (dest.exists() or is_link(dest))
    shutil.rmtree(dest, ignore_errors=True)
    return not dest.exists()