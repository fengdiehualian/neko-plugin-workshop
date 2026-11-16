"""reuse-before-build · 宿主无关的纯逻辑层（N.E.K.O 适配版）。

这一层不 import 任何 N.E.K.O SDK，只用标准库，因此可以在没有宿主的机器上直接跑测试。
它做四件事：
  1. search_candidates()  用 gh CLI 检索 GitHub 候选仓库，并把「检索失败」与「成功但 0 命中」分开；
  2. check_deliverable()  机械检查一段交付文本是否含四段格式、结论词与不可复用原因门禁行；
  3. verify_localized()   调用随包校验器检查本地化副本是否保住了不可变块；
  4. skill_text() / canonical_hash()  读取随包技能本体与正典哈希。

内核 code 与 CLI 退出码是两套独立契约，消费方不能混用：
  内核：0 成功 · 2 gh 不可用/参数非法 · 3 全部关键词失败 · 4 预算耗尽。
  CLI：0 成功 · 2 用法错误 · 3 全部关键词失败 · 4 gh 不可用 · 5 写文件失败 · 6 部分成功。
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import logging
import os
import re
import subprocess
import sys
import time
import tomllib
from typing import Any, Callable

_logger = logging.getLogger(__name__)

EMPTY_HINT = (
    "检索成功，但 0 命中。0 命中不等于不存在：换英文同义词、降 stars、放宽时间窗再跑一轮，"
    "或用 references/discovery-playbook.md 的生态检索补查。"
)
BUDGET_HINT = (
    "检索预算用完了，部分关键词没有真正查。不要据此断言「没有同类项目」；"
    "增大 scan_timeout / 工具 timeout，或减少关键词后重跑。"
)

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.join(HERE, "skills", "reuse-before-build")
VERIFY_SCRIPT = os.path.join(SKILL_DIR, "scripts", "verify_localized.py")
REDACTION_SCRIPT = os.path.join(SKILL_DIR, "scripts", "redaction.py")
SKILL_MD = os.path.join(SKILL_DIR, "SKILL.md")
PLUGIN_TOML = os.path.join(HERE, "plugin.toml")

# gh 只允许这两个文件名：配置项和显式调用都不能指向 .bat/.cmd 或任意程序。
GH_NAMES = {"gh", "gh.exe"}
_REDACTION_MODULE: Any = None


def _redaction_module() -> Any:
    """加载随包的唯一脱敏实现；CLI 与本插件内核共用同一份规则。"""
    global _REDACTION_MODULE
    if _REDACTION_MODULE is None:
        spec = importlib.util.spec_from_file_location("rbb_redaction", REDACTION_SCRIPT)
        if spec is None or spec.loader is None:
            raise RuntimeError("随包脱敏模块不可用")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _REDACTION_MODULE = module
    return _REDACTION_MODULE


def redact_text(value: object, limit: int = 300) -> str:
    """错误文本回给模型前脱敏，不把凭据或本机 home 路径原样返回。"""
    return _redaction_module().redact_text(value, limit)


def normalize_gh_binary(value: object) -> str:
    """配置项只接受 `gh` / `gh.exe`；任何自定义路径都回退到 PATH 里的 gh。"""
    raw = str(value or "").strip()
    if raw.lower() in GH_NAMES:
        return raw.lower()
    return "gh"


def resolve_gh_executable(value: object) -> str:
    """只接受字面量 gh / gh.exe；.bat/.cmd 会经 cmd.exe 解释参数，直接拒绝。

    返回空串表示值非法，调用方把它当成「gh 不可用」，而不是悄悄换成另一个程序。
    """
    raw = str(value or "").strip()
    if raw.lower() in GH_NAMES:
        return raw.lower()
    return ""


GH_HELPERS_SCRIPT = os.path.join(SKILL_DIR, "scripts", "gh_helpers.py")


def _gh_helpers_module() -> Any:
    """加载与随包 CLI 共用的检索辅助实现，避免两端规则再次漂移。"""
    spec = importlib.util.spec_from_file_location("rbb_gh_helpers_core", GH_HELPERS_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("随包检索辅助模块不可用")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_gh_helpers = _gh_helpers_module()
which_path_only = _gh_helpers.which_path_only
validate_keyword = _gh_helpers.validate_keyword
parse_search_output = _gh_helpers.parse_search_output
search_args = _gh_helpers.search_args
normalize_search_row = _gh_helpers.normalize_search_row
unique_keywords = _gh_helpers.unique_keywords
GH_CREATIONFLAGS = _gh_helpers.GH_CREATIONFLAGS
PERMISSIVE = _gh_helpers.PERMISSIVE
AUTH_HINT = _gh_helpers.AUTH_HINT
FAIL_HINT = _gh_helpers.FAIL_HINT
default_since = _gh_helpers.default_since
activity_points = _gh_helpers.activity_points
license_flags = _gh_helpers.license_flags
md_cell = _gh_helpers.md_cell
render_table = _gh_helpers.render_table


def resolve_gh_path(value: object) -> str:
    """白名单校验 + 只在 PATH 里解析成绝对路径；空串表示不可用。"""
    name = resolve_gh_executable(value)
    if not name:
        return ""
    return which_path_only(name)


def command_path(value: object) -> str:
    """给 gh_json/auth_ok 用：只接受字面量 gh/gh.exe，或由同一 PATH 解析出的绝对路径。

    `search_candidates()` 为了避免当前目录的 `gh.exe` 劫持，会先调用
    `resolve_gh_path()` 拿到绝对路径，再把它传给 `auth_ok()` / `_scan_one()`。
    因此这里不能只认字面量：绝对路径必须与当前 PATH 解析结果指向同一个文件，
    否则仍视为非法。这样既不会把自家解析结果拦掉，也不会放行任意路径。
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    if os.path.isabs(raw):
        base = os.path.basename(raw).lower()
        if base not in GH_NAMES:
            return ""
        resolved = which_path_only(base)
        if not resolved:
            return ""
        if os.path.normcase(os.path.realpath(resolved)) != os.path.normcase(os.path.realpath(raw)):
            return ""
        return resolved
    name = resolve_gh_executable(raw)
    # PATH 未命中也必须返回空串：退回裸名会让 Windows 再查当前目录。
    return which_path_only(name) if name else ""


def normalize_keywords(keywords: object) -> tuple[list[str], str]:
    """返回 (合法关键词, 错误)。错误非空时调用方按 code=2 处理，不发检索。"""
    if isinstance(keywords, (list, tuple)):
        items = list(keywords)
    else:
        items = str(keywords or "").replace("，", ",").split(",")
    clean: list[str] = []
    for item in items:
        try:
            kw = validate_keyword(item)
        except ValueError as exc:
            return [], str(exc)
        if kw not in clean:
            clean.append(kw)
    return unique_keywords(clean), ""

# 紧急降级值：不是「默认值」。真正的默认值只在 plugin.toml 的 [settings] 写一份；
# 只有 plugin.toml 缺失/解析失败时才落到这里，并把 enable_github_scan 关掉、条数压到最小。
SETTINGS_DEFAULTS: dict[str, Any] = {
    "enable_github_scan": False,
    "gh_binary": "gh",
    "default_stars": 0,
    "default_limit": 1,
    "updated_days": 1,
    "scan_timeout": 5,
}


def load_manifest_settings(plugin_toml: str = "") -> tuple[dict[str, Any], str]:
    """读 plugin.toml 的 [settings]；返回 (配置, 警告)。失败时警告非空，不静默。"""
    path = plugin_toml or PLUGIN_TOML
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except Exception as exc:
        warning = f"plugin.toml 读取/解析失败（{path}）：{exc}"
        _logger.warning("%s；回退代码内置兜底默认值", warning)
        return {}, warning
    section = data.get("settings")
    if not isinstance(section, dict):
        warning = f"plugin.toml 缺少 [settings] 表（{path}）"
        _logger.warning("%s；回退代码内置兜底默认值", warning)
        return {}, warning
    return section, ""


def manifest_settings(plugin_toml: str = "") -> dict[str, Any]:
    """读 plugin.toml 里的 [settings] 作为默认值；读不到就返回空表（不抛）。"""
    return load_manifest_settings(plugin_toml)[0]


def _as_bool(value: Any, default: bool) -> bool:
    """显式解析布尔值：TOML 布尔直接用，字符串 "false"/"0"/"off" 不许被 bool() 当成 True。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().casefold()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off", ""}:
        return False
    return default


def resolve_settings(section: Any = None, *, plugin_toml: str = "") -> dict[str, Any]:
    """把「代码兜底 → plugin.toml 默认 → 运行时配置段」三层合并成可用的六个值。

    宿主侧 `self.config.dump()` 不可用时（例如在没有真实 ctx 的测试环境），
    调用方传 None 即可，这里仍然返回一套可运行的值，不让插件因为读不到配置就启动失败。
    `settings_source` / `settings_warning` 供 rbb_status 如实显示当前用的是哪一层。
    """
    merged: dict[str, Any] = dict(SETTINGS_DEFAULTS)
    manifest, warning = load_manifest_settings(plugin_toml)
    merged.update({k: v for k, v in manifest.items() if k in SETTINGS_DEFAULTS})
    if isinstance(section, dict):
        merged.update({k: v for k, v in section.items() if k in SETTINGS_DEFAULTS})

    def _int(key: str, lo: int, hi: int) -> int:
        try:
            value = int(merged.get(key))
        except (TypeError, ValueError):
            value = int(SETTINGS_DEFAULTS[key])
        return max(lo, min(hi, value))

    gh_binary = normalize_gh_binary(merged.get("gh_binary"))
    if str(merged.get("gh_binary") or "gh").strip().lower() not in GH_NAMES:
        warning = (warning + "；" if warning else "") + (
            "gh_binary 只允许 gh / gh.exe，已回退到 gh（不支持自定义可执行路径）"
        )
    source = "runtime" if isinstance(section, dict) and section else (
        "plugin.toml" if manifest else "emergency-defaults")
    return {
        "enable_github_scan": _as_bool(merged.get("enable_github_scan"),
                                       bool(SETTINGS_DEFAULTS["enable_github_scan"])),
        "gh_binary": gh_binary,
        "default_stars": _int("default_stars", 0, 10_000_000),
        "default_limit": _int("default_limit", 1, 50),
        "updated_days": _int("updated_days", 1, 3650),
        "scan_timeout": _int("scan_timeout", 5, 300),
        "settings_source": source,
        "settings_warning": warning,
    }



# ────────────────────────── gh 调用 ──────────────────────────


def _gh(gh_binary: str, args: list[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(
        [gh_binary, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        shell=False,
        creationflags=GH_CREATIONFLAGS,
    )


def gh_json(gh_binary: str, args: list[str], timeout: float = 30) -> list[dict]:
    exe = command_path(gh_binary)
    if not exe:
        raise RuntimeError("gh_binary 非法：只允许 gh / gh.exe")
    try:
        proc = _gh(exe, args, timeout)
    except FileNotFoundError:
        raise RuntimeError("gh 可执行文件不在了（PATH 变了？）") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"gh 超时（>{timeout:g}s）") from None
    except ValueError as exc:
        raise RuntimeError(f"gh 参数非法：{redact_text(exc)}") from None
    except OSError as exc:
        raise RuntimeError(f"gh 启动失败：{redact_text(exc)}") from None
    if proc.returncode != 0:
        raise RuntimeError(f"gh 失败（exit {proc.returncode}）：{redact_text(proc.stderr.strip())}")
    return parse_search_output(proc.stdout)


def auth_ok(gh_binary: str = "gh", timeout: float = 30) -> tuple[bool, str]:
    """只查检索要用的那一个 host：`gh auth status` 在任一已配置 host 掉线时会整体非零。"""
    exe = command_path(gh_binary)
    if not exe:
        return False, "gh_binary 非法：只允许 gh / gh.exe"
    host = os.environ.get("GH_HOST") or "github.com"
    try:
        proc = _gh(exe, ["auth", "status", "-h", host], timeout)
    except FileNotFoundError:
        return False, "gh 可执行文件不在了（PATH 变了？）"
    except subprocess.TimeoutExpired:
        return False, f"`gh auth status` 超时（>{timeout:g}s）"
    except ValueError as exc:
        return False, f"gh 参数非法：{redact_text(exc, 200)}"
    except OSError as exc:
        return False, f"gh 启动失败：{redact_text(exc, 200)}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return False, f"{host}：{redact_text(detail[0], 200) if detail else 'gh auth status 返回非零'}"
    return True, ""


def _as_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_scan_params(
    stars: object, limit: object, updated_since: object, updated_days: object
) -> tuple[int, int, str, str]:
    """夹取直调参数：stars / limit / updated_days 有上限，updated_since 必须可解析。"""
    stars_n = max(0, min(1_000_000, _as_int(stars, 50)))
    limit_n = max(1, min(10, _as_int(limit, 6)))
    days_n = max(1, min(3650, _as_int(updated_days, 365)))
    raw_since = str(updated_since or "").strip()
    note = ""
    if raw_since:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_since):
            note = f"updated_since={raw_since!r} 不是 YYYY-MM-DD，已回退到最近 {days_n} 天"
            raw_since = ""
        else:
            try:
                dt.date.fromisoformat(raw_since)
            except ValueError:
                note = f"updated_since={raw_since!r} 不是有效日期，已回退到最近 {days_n} 天"
                raw_since = ""
    return stars_n, limit_n, raw_since or default_since(days_n), note


def _scan_one(kw: str, gh_binary: str, stars: int, updated_since: str,
              limit: int, timeout: float) -> tuple[list[dict], str]:
    """限流与次限流只退避重试一次；等待与重试共用本关键词剩余预算。"""
    args = search_args(kw, stars, updated_since, limit)
    try:
        rows = gh_json(gh_binary, args, timeout)
    except RuntimeError as exc:
        detail = str(exc)
        if timeout <= 4 or not re.search(r"rate limit|429|secondary rate", detail, re.IGNORECASE):
            return [], detail
        time.sleep(2)
        try:
            rows = gh_json(gh_binary, args, max(1.0, timeout - 2))
        except (OSError, RuntimeError, ValueError) as retry_exc:
            return [], f"限流退避后仍失败：{retry_exc}"
    except (OSError, ValueError) as exc:
        return [], str(exc)
    for row in rows:
        normalize_search_row(row, kw)
    return rows, ""


def search_candidates(
    keywords: list[str],
    stars: int = 50,
    limit: int = 6,
    updated_since: str = "",
    gh_binary: str = "gh",
    timeout: int = 30,
    updated_days: int = 365,
    clock: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """检索 GitHub 候选仓库，返回结构化结果（不抛异常）。

    code=0 检索成功（rows 可能为空）· code=2 gh 不可用/参数非法
    code=3 全部关键词检索失败 · code=4 预算耗尽、检索未完成
    调用方必须把 code!=0 当成「没查成」，绝不等于「不存在」。

    `timeout` 是整次检索的总预算（登录检查 + 所有关键词），不是每个关键词各给一份。
    """
    clean, kw_error = normalize_keywords(keywords)
    result: dict[str, Any] = {
        "code": 0, "ok": False, "rows": [], "table": "", "keywords": clean,
        "failed": [], "skipped": [], "reason": "", "note": "", "hint": "",
    }
    if kw_error:
        result.update(code=2, reason=f"关键词非法：{kw_error}", hint=AUTH_HINT)
        return result
    if not clean:
        result.update(code=2, reason="没有可用的检索关键词（keywords 为空）。", hint=AUTH_HINT)
        return result

    exe = resolve_gh_path(gh_binary)
    if not exe:
        result.update(code=2,
                      reason="gh 不可用：只允许 PATH 里的 gh / gh.exe，且不接受当前目录里的同名文件。",
                      hint=AUTH_HINT)
        return result

    try:
        total_budget = max(5, min(300, int(timeout)))
    except (TypeError, ValueError):
        total_budget = 30
    now = clock or time.monotonic
    deadline = now() + total_budget
    # 登录检查最多拿 40% 预算（上限 10s），剩下的留给关键词；否则 auth 一慢，
    # 后面每个关键词都只剩 0.x 秒，会被误报成 code=3「gh 坏了」。
    auth_budget = max(2.0, min(10.0, total_budget * 0.4))
    ok, why = auth_ok(exe, auth_budget)
    if not ok:
        result.update(code=2, reason=f"gh 未就绪：{why}", hint=AUTH_HINT)
        return result
    if deadline - now() <= 0.2:
        result.update(
            code=2,
            reason=f"`gh auth status` 用完了总时限（{total_budget}s），还没开始检索关键词。",
            hint=AUTH_HINT,
        )
        return result

    stars_n, limit_n, since, note = normalize_scan_params(stars, limit, updated_since, updated_days)
    if note:
        result["note"] = note
    merged: dict[str, dict] = {}
    failed: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []
    for kw in clean:
        remaining = deadline - now()
        if remaining <= 0.2:
            skipped.append(kw)
            errors.append(f"{kw}: 总时限 {total_budget}s 已用完，未检索（skipped）")
            continue
        rows, err = _scan_one(kw, exe, stars_n, since, limit_n, min(float(total_budget), remaining))
        if err:
            failed.append(kw)
            errors.append(f"{kw}: {err}")
        for row in rows:
            key = (row.get("url") or row.get("full_name") or "").rstrip("/").lower()
            if not key:
                continue  # 没有 URL 也没有 full_name 的行无法去重，也不能作为候选
            if key in merged:
                prev = merged[key].get("matched_keyword")
                if isinstance(prev, list):
                    if kw not in prev:
                        prev.append(kw)
                else:
                    merged[key]["matched_keyword"] = [prev, kw]
                continue
            row["matched_keyword"] = kw
            merged[key] = row

    rows = list(merged.values())
    for row in rows:
        row["_activity"] = activity_points(row.get("pushedAt", ""))
        row["_lic_score"], row["_lic_label"] = license_flags(row.get("license"))
        if not isinstance(row["matched_keyword"], list):
            row["matched_keyword"] = [row["matched_keyword"]]
    rows.sort(key=lambda r: (-r["_lic_score"], -r["_activity"], -(r.get("stargazersCount") or 0)))

    result["rows"] = rows
    result["table"] = render_table(rows) if rows else ""
    result["failed"] = failed
    result["skipped"] = skipped
    result["stars"], result["updated_since"], result["limit"] = stars_n, since, limit_n

    notes = [note] if note else []
    if skipped and not rows and len(failed) + len(skipped) == len(clean):
        result.update(
            code=4,
            reason="检索未完成（预算耗尽）：" + "; ".join(errors)[:600],
            hint=BUDGET_HINT,
        )
        return result
    if failed and len(failed) == len(clean):
        result.update(
            code=3,
            reason="全部关键词检索失败：" + "; ".join(errors)[:600],
            hint=FAIL_HINT,
        )
        return result
    if failed:
        notes.append(f"{len(failed)}/{len(clean)} 个关键词检索失败，结果可能偏少。")
    if skipped:
        notes.append(f"{len(skipped)}/{len(clean)} 个关键词因预算耗尽未检索（skipped），结果可能偏少。")
    if not rows and not failed and not skipped:
        notes.append(EMPTY_HINT)
    elif rows:
        notes.append(f"共 {len(rows)} 个去重候选（{len(clean)} 个关键词）。下一步：按 "
                     "references/repo-triage.md 读每个仓库的 CONTRIBUTING 与最近合并的 PR，做六维打分。")
    result["note"] = "；".join(notes)
    result["ok"] = result["code"] == 0
    return result


# ────────────────────────── 交付物机械检查 ──────────────────────────

SECTIONS = [
    ("found", ("What I found", "找到了什么")),
    ("why", ("Why this one", "为什么选它")),
    ("change", ("What I will add", "What I'll add", "我要加什么", "我要加的东西")),
    ("risk", ("Alternatives", "Alternatives and risks", "备选与风险", "备选和风险", "备选方案与风险", "备选")),
]
VERDICTS = ("Join", "Extend", "Fork", "Build", "加入", "扩展", "分叉", "新建")
VERDICT_ALIASES = {
    "join": "Join", "加入": "Join", "extend": "Extend", "扩展": "Extend",
    "fork": "Fork", "分叉": "Fork", "build": "Build", "新建": "Build",
}
MODIFIABILITY_ALIASES = {"green": "green", "绿": "green", "yellow": "yellow", "黄": "yellow", "red": "red", "红": "red"}
# 门禁只看「结论是哪一条」，不看全文有没有出现过 Build——备选段几乎总会提到它。
CONCLUSION_RX = re.compile(
    r"^(?:最终|final\s+)?(?:结论|verdict|conclusion)\s*[:：]\s*[*_`> ]*"
    r"(Join|Extend|Fork|Build|加入|扩展|分叉|新建)(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
GATE_RX = re.compile(
    r"^(?:不可复用原因|Not\s+reusable\s+because)"
    r"(?:\s*/\s*(?:不可复用原因|Not\s+reusable\s+because))?\s*[:：]\s*(.*)$",
    re.IGNORECASE,
)
MODIFIABILITY_RX = re.compile(
    r"^(?:modifiability(?:\s+grade)?|可改性(?:档位|等级)?)\s*[:：]\s*[*_`> ]*"
    r"(green|yellow|red|绿|黄|红)(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
# 快速档没命中是独立形态：一行说明 + 列出检索串，不套完整档四段模板。
QUICK_NO_HIT_RX = re.compile(
    r"^(?:未找到|没有找到|没找到|未发现|没有发现|未命中|没有命中|"
    r"no\s+(?:match|matches|equivalent)|none\s+found|not\s+found|nothing\s+matched)",
    re.IGNORECASE,
)
QUICK_SEARCH_RX = re.compile(
    r"(?i)(github|gitlab|pypi|npm|crates|maven|awesome[- ]?list|"
    r"检索|搜索|关键词|search|query|stars?|★)"
)


def _plain_line(line: str) -> str:
    """去掉行首的标题/引用/列表标记与成对的强调符号，得到可判定的一行。"""
    text = (line or "").strip()
    text = re.sub(r"^>\s*", "", text)
    text = re.sub(r"^#{1,6}\s*", "", text)
    text = re.sub(r"^[-+*]\s+", "", text)
    text = text.strip()
    text = text.strip("*_` ")
    return text.strip()


def _heading_text(line: str) -> str:
    """标题行归一化：允许 `##`、`**`、`1.` 前缀与结尾冒号，但正文里的包含不算。"""
    text = _plain_line(line)
    text = re.sub(r"^\d+[.)、]\s*", "", text)
    return text.rstrip("：:").strip().casefold()


def _has_section(body: str, variants: tuple[str, ...]) -> bool:
    """接受正典变体与括号后缀，但不把以标题词开头的普通长句当标题。"""
    for line in body.splitlines():
        heading = _heading_text(line)
        for variant in variants:
            wanted = variant.casefold()
            if heading == wanted or re.match(rf"^{re.escape(wanted)}\s*[\(（\[【]", heading):
                return True
    return False


def _conclusion_verdicts(text: str) -> list[str]:
    """按出现顺序返回所有显式结论行里的统一英文判决词。"""
    hits: list[str] = []
    for line in text.splitlines():
        match = CONCLUSION_RX.match(_plain_line(line))
        if match:
            hits.append(VERDICT_ALIASES[match.group(1).casefold()])
    return hits


def _ascii_verdict_hits(text: str) -> list[str]:
    """没有结论行时的退化判定：只认 ASCII 整词，中文常见词不能全文扫。"""
    hits = []
    for verdict in VERDICTS:
        if verdict.isascii() and re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(verdict)}(?![A-Za-z0-9_])",
            text, re.IGNORECASE,
        ):
            hits.append(VERDICT_ALIASES[verdict.casefold()])
    return hits


def _gate_parts(line: str) -> tuple[bool, str]:
    match = GATE_RX.match(_plain_line(line))
    if not match:
        return False, ""
    reason = match.group(1).strip().lstrip("*_`>：: ").strip().rstrip("*_` ").strip()
    return True, reason


def _quick_lane_result(lines: list[str]) -> dict[str, Any] | None:
    """快速档是独立契约：三个有序非空字段，允许 Build 时前置门禁行。"""
    first_is_gate, first_reason = _gate_parts(lines[0]) if lines else (False, "")
    # 没命中是快速档的另一种合法形态：一行说明 + 检索串；Build 回复允许前面带门禁行。
    lead = lines[1:] if first_is_gate else lines
    if lead and QUICK_NO_HIT_RX.match(_plain_line(lead[0])):
        if len(lead) > 3:
            return None
        has_searches = any(QUICK_SEARCH_RX.search(_plain_line(line)) for line in lead)
        notes = ["lane: quick", "no hit — 0 候选也是结论，不得据此编造仓库"]
        missing: list[str] = []
        if first_is_gate and not first_reason:
            missing.append("gate_reason")
        if not has_searches:
            missing.append("quick:searches")
        return {
            "ok": not missing, "missing": missing, "notes": notes,
            "verdict": "Build" if first_is_gate else "", "modifiability": "", "lane": "quick-no-match",
        }
    quick = lead
    if not quick or not re.match(r"^(?:Reused|复用)\s*[:：]", _plain_line(quick[0]), re.IGNORECASE):
        return None
    expected = (("Reused", "复用"), ("Change", "改动"), ("Risk", "风险"))
    missing: list[str] = []
    if len(quick) != 3:
        missing.append(f"quick_line_count({len(quick)}!=3)")
    for index, names in enumerate(expected):
        if index >= len(quick):
            missing.append(f"quick:{names[0].lower()}")
            continue
        match = re.match(
            rf"^(?:{names[0]}|{names[1]})\s*[:：]\s*(.*)$",
            _plain_line(quick[index]), re.IGNORECASE,
        )
        if not match:
            missing.append(f"quick:{names[0].lower()}")
        elif not match.group(1).strip().strip("*_` "):
            missing.append(f"quick:{names[0].lower()}_empty")
    if first_is_gate and not first_reason:
        missing.append("gate_reason")
    return {
        "ok": not missing, "missing": missing, "notes": ["lane: quick"],
        "verdict": "", "modifiability": "", "lane": "quick",
    }


def check_deliverable(text: str) -> dict[str, Any]:
    """检查完整档/快速档结构、统一判决字段、可改性硬闸与 Build 首行门禁。"""
    missing: list[str] = []
    notes: list[str] = []
    body = text or ""
    lines = [line for line in body.splitlines() if line.strip()]
    quick = _quick_lane_result(lines)
    if quick is not None:
        return quick

    for key, variants in SECTIONS:
        if not _has_section(body, variants):
            missing.append(f"section:{key}")

    explicit = _conclusion_verdicts(body)
    ascii_hits = _ascii_verdict_hits(body) if not explicit else []
    verdict_hit = list(dict.fromkeys(explicit + ascii_hits))
    if not verdict_hit:
        missing.append("verdict")
    else:
        notes.append("verdict: " + ", ".join(verdict_hit))

    # 多个结论行时取最后一条：备选段里的「结论：Build」不能盖掉最终结论。
    if explicit:
        verdict = explicit[-1]
    elif len(ascii_hits) == 1:
        verdict = ascii_hits[0]
    else:
        verdict = ""
        if ascii_hits:
            notes.append("结论不唯一：建议补一行「结论：Join/Extend/Fork/Build」；Build 门禁只按结论判定，不按备选段里的提及。")

    grades: list[str] = []
    for line in body.splitlines():
        match = MODIFIABILITY_RX.match(_plain_line(line))
        if match:
            grade = MODIFIABILITY_ALIASES[match.group(1).casefold()]
            if grade not in grades:
                grades.append(grade)
    table_rows = sum(1 for line in body.splitlines() if line.strip().startswith("|"))
    has_candidate_rows = table_rows >= 3
    modifiability = grades[0] if len(grades) == 1 else ""
    if not grades:
        if verdict == "Build" and not has_candidate_rows:
            # Build 的正当理由可能就是“没有同类仓库”：没有候选可评，不硬要可改性档位。
            notes.append("modifiability omitted: Build with no candidate table (allowed)")
        else:
            missing.append("modifiability")
    elif len(grades) > 1:
        missing.append("modifiability_conflict")
    else:
        notes.append(f"modifiability: {modifiability}")
    if verdict == "Join" and modifiability and modifiability != "green":
        missing.append("join_requires_green")

    if verdict == "Build":
        first_is_gate, first_reason = _gate_parts(lines[0]) if lines else (False, "")
        if not first_is_gate:
            missing.append("gate_line")
            if any(_gate_parts(line)[0] for line in lines[1:]):
                notes.append("Build 门禁必须位于回复首个非空行，不能放在文末。")
        elif not first_reason:
            missing.append("gate_reason")

    if table_rows < 2:
        notes.append("no candidate table (| … | rows) — 有候选时应当给出表格")

    if re.search(r"\b\d{4}-\d{2}-\d{2}\b", body) is None:
        notes.append("no date seen — 引用仓库状态时建议带最近推送日期")

    return {
        "ok": not missing, "missing": missing, "notes": notes,
        "verdict": verdict, "modifiability": modifiability, "lane": "full",
    }


# ────────────────────────── 随包技能与校验器 ──────────────────────────

def skill_text(rel: str = "SKILL.md") -> str:
    path = os.path.join(SKILL_DIR, rel)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def skill_section(heading: str) -> str:
    """原样抽取正典二级章节，避免入口里的手写摘要与规则漂移。"""
    match = re.search(
        rf"(?ms)^##\s+{re.escape(heading)}\s*$\n(.*?)(?=^##\s+|\Z)",
        skill_text("SKILL.md"),
    )
    return f"## {heading}\n{match.group(1).strip()}" if match else ""


def mandatory_rules() -> str:
    """默认工具返回必须覆盖核心规则、判决硬闸、硬约束与不可信内容。

    任一必需章节抽不到时返回空串，由调用方返回 Err；不能把缺章当成“只是少了点内容”继续 Ok。
    """
    core = core_rules_block()
    sections = [
        skill_section(name) for name in (
            "Step 2 — Evaluate", "Step 3 — Contribute", "Hard constraints", "Untrusted repository content",
        )
    ]
    if not core or any(not section for section in sections):
        return ""
    return "\n\n".join([core, *sections])


def flow_steps() -> list[dict[str, str]]:
    """从正典提取四步标题与正文，不维护另一份容易过期的流程。"""
    text = skill_text("SKILL.md")
    steps = [
        {"title": match.group(1), "content": match.group(2).strip()}
        for match in re.finditer(
            r"(?ms)^##\s+(Step\s+[1-4]\s+—\s+[^\n]+)\s*$\n(.*?)(?=^##\s+|\Z)", text,
        )
    ]
    return steps if len(steps) == 4 else []


_VERIFY_MODULE: Any = None


def _verify_module() -> Any:
    """按文件路径加载随包校验器，共用它的围栏/标记解析，避免两份实现漂移。"""
    global _VERIFY_MODULE
    if _VERIFY_MODULE is None:
        spec = importlib.util.spec_from_file_location("rbb_verify_localized", VERIFY_SCRIPT)
        if spec is None or spec.loader is None:
            raise RuntimeError("随包校验器不可用")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _VERIFY_MODULE = module
    return _VERIFY_MODULE


def core_rules_block() -> str:
    """取 SKILL.md 里 RBB:INVARIANTS 标记之间的正典规则段（整行锚定 + 跳过围栏示例）。"""
    text = skill_text("SKILL.md")
    return _verify_module().extract_block(
        text, "RBB:INVARIANTS:BEGIN", "RBB:INVARIANTS:END"
    ) or ""


def _looks_like_python(path: str) -> bool:
    """嵌入式宿主的 sys.executable 可能是宿主进程本身，不能再把它当解释器起一份。"""
    base = os.path.basename(path).lower()
    return bool(re.fullmatch(r"(?:python|pythonw)(?:\d+(?:\.\d+)*)?(?:\.exe)?", base)
                or base in {"py", "py.exe"})


def resolve_python(python_exe: str = "") -> str:
    """优先用真正的 Python 解释器；Windows 只有 py 启动器时也能找到。"""
    raw = str(python_exe or "").strip()
    if raw:
        return raw
    if sys.executable and os.path.isfile(sys.executable) and _looks_like_python(sys.executable):
        return sys.executable
    for name in ("python", "python3", "py"):
        found = which_path_only(name)
        if found:
            return found
    # 与 gh 一样不退回裸程序名，避免 Windows 从当前目录启动同名文件。
    return ""


def canonical_hash(python_exe: str = "", timeout: float = 30) -> tuple[str, str]:
    """跑随包校验器 --print-hash，返回 (hash, 错误信息)。"""
    py = resolve_python(python_exe)
    try:
        proc = subprocess.run(
            [py, "-X", "utf8", VERIFY_SCRIPT, "--print-hash", SKILL_MD],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=max(1, float(timeout)), shell=False,
        )
    except subprocess.TimeoutExpired:
        return "", f"规则哈希计算超时（>{timeout:g}s）"
    except OSError as exc:
        return "", f"规则哈希计算失败：{redact_text(exc)}"
    if proc.returncode != 0:
        return "", redact_text((proc.stderr or proc.stdout).strip())
    return redact_text(proc.stdout.strip(), 200), ""


def resolve_localized_path(target: str) -> tuple[str, str]:
    """把待校验路径限制在插件工作目录内，且必须是 .md。

    模型能传任意路径；不设防等于给它一个「读本机文件」的入口。相对路径按插件目录解析
    （与工具描述一致），realpath 会穿透 `..` 与符号链接，逃出工作目录的一律拒绝。
    """
    raw = str(target or "").strip()
    if not raw:
        return "", "缺少 path（待校验副本路径）。给插件目录内的 .md 路径再调一次。"
    candidate = raw if os.path.isabs(raw) else os.path.join(HERE, raw)
    real = os.path.realpath(os.path.expanduser(candidate))
    if not real.lower().endswith(".md"):
        return "", "只允许校验 .md 文件；其它扩展名一律拒绝。"
    root = os.path.realpath(HERE)
    try:
        inside = os.path.commonpath([real, root]) == root
    except ValueError:
        inside = False
    if not inside:
        return "", "目标不在插件工作目录内，已拒绝读取。把副本复制到插件目录内再校验。"
    if not os.path.isfile(real):
        return "", f"文件不存在：{os.path.basename(real)}（在插件工作目录内未找到）"
    return real, ""


def verify_localized(target: str, python_exe: str = "",
                     extra: list[str] | None = None, timeout: float = 30) -> dict[str, Any]:
    """校验一份本地化副本；rc=0 才算保住规则。路径、参数与输出都做收敛。

    `extra` 只允许 `--quiet`：`--canonical` 等能跳过声明检查的开关不能由模型侧注入。
    """
    real, path_err = resolve_localized_path(target)
    if path_err:
        return {"rc": 2, "out": "", "err": path_err[:400]}
    safe_extra = [arg for arg in (extra or []) if arg == "--quiet"]
    py = resolve_python(python_exe)
    args = [py, "-X", "utf8", VERIFY_SCRIPT, real, *safe_extra]
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=max(1, float(timeout)), shell=False,
        )
    except subprocess.TimeoutExpired:
        return {"rc": -1, "out": "", "err": f"校验超时（>{timeout:g}s）"}
    except OSError as exc:
        return {"rc": -1, "out": "", "err": f"找不到 Python 解释器或无法启动：{redact_text(exc, 200)}"}
    return {
        "rc": proc.returncode,
        "out": redact_text(proc.stdout.strip(), 800),
        "err": redact_text(proc.stderr.strip(), 400),
    }
