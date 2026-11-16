#!/usr/bin/env python3
"""插件内核与随包 CLI 共用的 GitHub 检索辅助逻辑。"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import re
import shutil
import subprocess


def _load_redaction():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "redaction.py")
    spec = importlib.util.spec_from_file_location("rbb_redaction_for_gh_helpers", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("随包脱敏模块不可用")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


redact_text = _load_redaction().redact_text

GH_CREATIONFLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

PERMISSIVE = {"mit", "apache-2.0", "bsd-3-clause", "bsd-2-clause", "isc", "mpl-2.0", "0bsd", "unlicense"}

AUTH_HINT = (
    "gh 无法确认可用（可能是未登录、无权限，也可能是网络/主机配置不通）。"
    "先执行 `gh auth status` 查看，必要时 `gh auth login`；"
    "完全没有 gh 时改用 references/discovery-playbook.md 的网页检索串。"
)
FAIL_HINT = (
    "检索失败 ≠ 没有同类项目。检查 `gh auth status` 与网络后重跑，"
    "或改用 references/discovery-playbook.md 的网页检索串手工复核；不要据此断言不存在。"
)

# 关键词里的检索限定符会覆盖调用方自己的 stars/pushed/archived 过滤条件。
_QUALIFIER_RX = re.compile(
    r"(?i)(?:^|\s)(?:stars|archived|pushed|created|updated|is|topic|language|license|"
    r"owner|repo|user|org|size|fork|mirror|template):"
)
KEYWORD_MAX = 200


def which_path_only(name: str) -> str:
    """只从 PATH 解析可执行文件，并对 Windows 的隐式当前目录结果再做硬校验。"""
    raw = os.environ.get("PATH", "")
    cwd = os.path.normcase(os.path.realpath(os.curdir))
    entries = [
        part for part in raw.split(os.pathsep)
        if part and os.path.normcase(os.path.realpath(os.path.abspath(part))) != cwd
    ]
    if not entries:
        return ""
    path = os.pathsep.join(entries)
    # Windows 优先真正的 gh.exe，不让 gh.cmd/gh.bat 抢先进入 cmd 参数解释层。
    names = ("gh.exe", name) if os.name == "nt" and name.lower() == "gh" else (name,)
    for candidate in names:
        found = shutil.which(candidate, path=path) or ""
        if not found or not os.path.isabs(found):
            continue
        real = os.path.normcase(os.path.realpath(found))
        if os.path.dirname(real) == cwd:
            continue
        return found
    return ""


def validate_keyword(value: object) -> str:
    """关键词必须是纯文本：拒绝空、NUL/控制字符、超长与能覆盖过滤条件的限定符。"""
    text = str(value or "").strip()
    if not text:
        raise ValueError("关键词不能为空")
    if len(text) > KEYWORD_MAX:
        raise ValueError(f"单个关键词不能超过 {KEYWORD_MAX} 字符")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise ValueError("关键词里不能有 NUL 或其它控制字符")
    if _QUALIFIER_RX.search(text):
        raise ValueError("关键词里不能带 stars:/pushed:/archived:/user:/org: 等检索限定符；"
                         "门槛请用 --stars / --updated-since")
    return text


def parse_search_output(out: str) -> list[dict]:
    """解析 gh 的 JSON 输出，只保留 dict 行；错误片段先脱敏再返回。"""
    text = (out or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise RuntimeError(f"gh 输出不是 JSON（{exc}）：{redact_text(text, 200)}") from None
    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    return []


def search_args(keyword: str, stars: int, updated_since: str, limit: int) -> list[str]:
    """所有选项放 `--` 之前，检索串放 `--` 之后，避免关键词被当成 gh 选项。"""
    query = f"{keyword} stars:>={stars} pushed:>={updated_since}"
    fields = "url,name,owner,stargazersCount,pushedAt,license,isArchived,language,openIssuesCount,description"
    return ["search", "repos", "--archived=false", "--limit", str(limit), "--json", fields, "--", query]


def normalize_search_row(row: dict, keyword: str) -> dict:
    """补 full_name / matched_keyword，不改 gh 原字段。"""
    owner = (row.get("owner") or {}).get("login", "")
    row["full_name"] = f"{owner}/{row.get('name', '')}" if owner else row.get("name", "")
    row["matched_keyword"] = keyword
    return row


def unique_keywords(values: object) -> list[str]:
    """按 casefold 去重，保留首次出现顺序；GitHub 检索本身不区分大小写。"""
    if isinstance(values, str):
        items = [values]
    elif isinstance(values, (list, tuple)):
        items = list(values)
    else:
        items = []
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        text = str(item)
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def default_since(days: int | dt.date = 365, today: dt.date | None = None) -> str:
    """默认时间窗用天数回退，避免闰日 2/29 的 replace(year-1) 抛 ValueError。

    兼容旧 CLI 的 `default_since(today)` 与插件内核的 `default_since(days, today)`。
    """
    if isinstance(days, dt.date):
        today, days = days, 365
    today = today or dt.date.today()
    return (today - dt.timedelta(days=max(1, int(days)))).isoformat()


def activity_points(pushed_at: object) -> int:
    if not isinstance(pushed_at, str) or not pushed_at:
        return 0
    try:
        pushed = dt.datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
        days = (dt.datetime.now(dt.timezone.utc) - pushed).days
    except (AttributeError, TypeError, ValueError, OverflowError):
        return 0
    if days <= 90:
        return 2
    if days <= 365:
        return 1
    return 0


def license_flags(license_obj: dict | None) -> tuple[int, str]:
    key = ((license_obj or {}).get("key") or "").lower()
    if not key:
        return 0, "无 LICENSE ⚠"
    if key in PERMISSIVE:
        return 2, (license_obj or {}).get("spdxId") or key
    return 1, (license_obj or {}).get("spdxId") or key


def md_cell(value: object) -> str:
    """表格单元格转义：竖线会切断 Markdown 表格，换行会切断行。"""
    if value is None:
        return "-"
    text = str(value)
    if text == "":
        return "-"
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def render_table(rows: list[dict]) -> str:
    header = "| 仓库 | ★ | 最近推送 | 语言 | 许可 | open issues | 命中关键词 | URL |"
    sep = "|---|---|---|---|---|---|---|---|"
    lines = [header, sep]
    for row in rows:
        pushed = (row.get("pushedAt") or "")[:10] or "-"
        if row.get("_activity", 1) == 0:
            pushed = f"{pushed} ⚠停滞"
        lines.append(
            "| {name} | {stars} | {pushed} | {lang} | {lic} | {issues} | {kw} | {url} |".format(
                name=md_cell(row.get("full_name")),
                stars=md_cell(row.get("stargazersCount") or 0),
                pushed=md_cell(pushed),
                lang=md_cell(row.get("language")),
                lic=md_cell(row.get("_lic_label")),
                issues=md_cell(row.get("openIssuesCount")),
                kw=md_cell(", ".join(row.get("matched_keyword") or [])),
                url=md_cell(row.get("url")),
            )
        )
    return "\n".join(lines)
