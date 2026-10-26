"""Agent Skill 注册表：扫描 skills/ 目录，校验 SKILL.md，管理启用/禁用/重载。

SKILL.md 采用 YAML frontmatter（``---`` 包裹）+ Markdown 正文：

    ---
    name: knowledge-search
    description: ...
    license: Apache-2.0
    metadata:
      author: neko-plugin
      version: "1.0"
    ---

校验规则（对应 Agent Skills 规范的最小集合）：
* 目录下存在 SKILL.md；frontmatter 可解析
* ``name`` 必填且与目录名一致（``^[a-z0-9-]{1,64}$``）
* ``description`` 必填非空，且说明"何时使用"
* ``metadata`` 可选；解析为 dict
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9-]{1,64}$")
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


class SkillFormatError(Exception):
    """SKILL.md 格式错误（结构化，不导致宿主崩溃）。"""


@dataclass
class SkillDef:
    name: str
    description: str
    dir: Path
    license: str = ""
    metadata: dict = field(default_factory=dict)
    body: str = ""

    #: 来源：``builtin`` = 插件自带（随包分发）；``external`` = 用户文件夹里的 Skill
    origin: str = "builtin"
    #: external 专用：它来自哪个用户 skill 根（相对用户目录）
    root: str = ""
    #: 挂载方式：``junction`` / ``copy`` / ``native``（未挂载，直接就在插件目录里）
    link: str = "native"

    def summary(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "dir": str(self.dir),
            "license": self.license,
            "metadata": self.metadata,
            "origin": self.origin,
            "root": self.root,
            "link": self.link,
        }


def parse_skill_md(path: Path) -> dict:
    """解析 SKILL.md -> {name, description, license, metadata, body}。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillFormatError(f"cannot read {path}: {exc}") from exc
    m = FRONTMATTER_RE.match(raw)
    if not m:
        raise SkillFormatError(f"{path.name}: missing YAML frontmatter (--- ... ---)")
    meta_text = m.group(1)
    body = raw[m.end() :]
    data = _parse_yaml(meta_text)
    if not isinstance(data, dict):
        raise SkillFormatError(f"{path.name}: frontmatter must be a mapping")
    return {"body": body, **data}


def _parse_yaml(text: str) -> object:
    """优先 pyyaml（宿主自带），缺失时退回内置的最小 YAML 子集解析。"""
    try:
        import yaml

        return yaml.safe_load(text)
    except ImportError:
        return _mini_yaml(text)
    except Exception as exc:
        raise SkillFormatError(f"frontmatter YAML parse failed: {exc}") from exc


def _mini_yaml(text: str) -> dict:
    """frontmatter 专用：顶层标量 + 一层嵌套 metadata 映射。"""
    out: dict = {}
    current: dict | None = None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        key, _, val = line.strip().partition(":")
        val = val.strip().strip("'\"")
        if indent == 0:
            if val == "":
                out[key] = {}
                current = out[key] if isinstance(out[key], dict) else None
            else:
                out[key] = val
                current = None
        elif current is not None:
            current[key] = val
    return out


def discover(skills_root: Path) -> list[SkillDef]:
    """扫描 skills/ 下的一级子目录，返回通过校验的 SkillDef（按名称排序）。

    任一 SKILL.md 不合规即抛 SkillFormatError（严格模式，供校验/测试用）。
    """
    skills, errors = discover_with_errors(skills_root)
    if errors:
        raise SkillFormatError("; ".join(errors))
    return skills


def discover_with_errors(
    skills_root: Path, exclude: set[str] | None = None
) -> tuple[list[SkillDef], list[str]]:
    """宽容模式：逐文件收集错误，坏文件不挡其余 Skill 注册。

    ``exclude``：本次已挂载的外部 Skill 名。挂载点本身是合规目录，
    不排除的话会被当成「自带」，从而污染来源归属与注册决策。
    """
    root = Path(skills_root)
    if not root.is_dir():
        return [], []
    skip = exclude or set()
    found: list[SkillDef] = []
    errors: list[str] = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name in skip:
            continue
        skill_md = d / "SKILL.md"
        if not skill_md.exists():
            continue
        try:
            data = parse_skill_md(skill_md)
            name = data.get("name")
            if not isinstance(name, str) or not NAME_RE.match(name):
                raise SkillFormatError("invalid or missing 'name'")
            if name != d.name:
                raise SkillFormatError(f"name {name!r} != directory name")
            desc = data.get("description")
            if not isinstance(desc, str) or len(desc.strip()) < 8:
                raise SkillFormatError(
                    "'description' must explain what the skill does and when to use it"
                )
            meta = data.get("metadata") or {}
            if not isinstance(meta, dict):
                raise SkillFormatError("'metadata' must be a mapping")
            found.append(
                SkillDef(
                    name=name,
                    description=desc.strip(),
                    dir=d,
                    license=str(data.get("license", "")),
                    metadata=meta,
                    body=data.get("body", ""),
                )
            )
        except SkillFormatError as exc:
            errors.append(f"{d.name}/SKILL.md: {exc}")
    return found, errors
