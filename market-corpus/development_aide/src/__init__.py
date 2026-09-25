from __future__ import annotations

import asyncio
import os
import re
from typing import Any

try:
    from plugin.sdk.plugin import (
        Err,
        NekoPluginBase,
        Ok,
        SdkError,
        lifecycle,
        neko_plugin,
        plugin_entry,
        tr,
        ui,
    )
except ImportError:  # pragma: no cover - fallback for tests and standalone import
    class SdkError(RuntimeError):
        pass

    class Ok(dict):
        def __init__(self, data: Any = None):
            super().__init__(data if isinstance(data, dict) else {})

    class Err(dict):
        def __init__(self, error: Any = None):
            super().__init__()
            self.error = error

    class _InMemoryConfig:
        """Minimal stand-in for the SDK PluginConfig facade."""

        def __init__(self, initial: dict | None = None):
            self._data: dict[str, Any] = {"settings": dict(initial or {})}

        async def dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            return {"settings": dict(self._data.get("settings", {}))}

        async def set(self, path: str, value: Any, *args: Any, **kwargs: Any) -> None:
            parts = [part for part in str(path).split(".") if part]
            current = self._data
            for part in parts[:-1]:
                nxt = current.get(part)
                if not isinstance(nxt, dict):
                    nxt = {}
                    current[part] = nxt
                current = nxt
            current[parts[-1]] = value

        async def update(self, patch: dict[str, Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
            for key, value in (patch or {}).items():
                self._data[key] = value
            return await self.dump()

    class NekoPluginBase:
        def __init__(self, ctx: Any = None):
            self.ctx = ctx
            self.config = _InMemoryConfig()
            self.logger = type(
                "Logger",
                (),
                {
                    "exception": lambda self, *args, **kwargs: None,
                    "warning": lambda self, *args, **kwargs: None,
                    "info": lambda self, *args, **kwargs: None,
                },
            )()

    def neko_plugin(cls):
        return cls

    def lifecycle(id=None):
        def decorator(fn):
            return fn
        return decorator

    def plugin_entry(**_):
        def decorator(fn):
            return fn
        return decorator

    def tr(key: str, default: str | None = None):
        return default if default is not None else key

    class _Ui:
        def action(self, **_):
            def decorator(fn):
                return fn
            return decorator

        def context(self, **_):
            def decorator(fn):
                return fn
            return decorator

    ui = _Ui()


# ---------------------------------------------------------------------------
# Bounded resource budgets (issue #5: limits must bound actual reads/walks)
# ---------------------------------------------------------------------------

DEFAULT_EXTENSIONS = (".py", ".ts", ".tsx", ".md", ".toml", ".json")
DEFAULT_MAX_CHARS = 4000
MAX_CHARS_HARD_LIMIT = 1_000_000
MAX_BATCH_FILES = 25
MAX_BATCH_TOTAL_CHARS = 50_000
MAX_SCAN_FILES = 500
MAX_SCAN_DIRS = 200

# Resolving a bare file name may match several files; report at most this many.
MAX_NAME_MATCHES = 20
# A skill directory is recognized by one of these marker files.
SKILL_MARKERS = ("SKILL.md", "skill.md", "SKILL.toml", "skill.toml")
# Bounded scan for skill directories.
MAX_SKILL_SCAN_DIRS = 300
MAX_SKILL_CANDIDATES = 50
# Text read through errors="replace" is treated as binary above this ratio.
MAX_REPLACEMENT_RATIO = 0.05


def _normalize_extensions(extensions: Any | None) -> set[str]:
    """Normalize and validate file extensions.

    ``"*"`` means "no extension filter" and yields an empty set; callers treat
    an empty set as "accept every file".
    """
    if extensions == "*":
        return set()
    if not extensions:
        return set(DEFAULT_EXTENSIONS)
    normalized: set[str] = set()
    for ext in extensions:
        value = str(ext).strip()
        if not value:
            continue
        normalized.add(value if value.startswith(".") else f".{value}")
    return normalized or set(DEFAULT_EXTENSIONS)


def coerce_read_limit(value: Any, default: int = DEFAULT_MAX_CHARS) -> tuple[int, bool]:
    """Validate a per-file character budget.

    Zero and negative values are rejected. Absurdly large values are capped to
    the hard limit. Returns ``(limit, capped)``.
    """
    raw = default if value is None else value
    if isinstance(raw, bool):
        raise ValueError("max_chars 必须是正整数。")
    try:
        number = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("max_chars 必须是正整数。") from exc
    if number <= 0:
        raise ValueError("max_chars 必须是大于 0 的正整数。")
    return min(number, MAX_CHARS_HARD_LIMIT), number > MAX_CHARS_HARD_LIMIT


# ---------------------------------------------------------------------------
# Workspace path sandbox (issue #1: no traversal / links escape the workspace)
# ---------------------------------------------------------------------------

def _is_within(target: str, root: str) -> bool:
    """True when ``target`` equals or lives below ``root`` (real, normalized)."""
    root_norm = os.path.normcase(os.path.normpath(root)).rstrip(os.sep)
    target_norm = os.path.normcase(os.path.normpath(target))
    return target_norm == root_norm or target_norm.startswith(root_norm + os.sep)


def _real_root(root: Any) -> str | None:
    """Resolve the configured workspace root; empty/unset is rejected.

    An empty string must never fall through to abspath() (which would turn it
    into the process working directory).
    """
    if isinstance(root, os.PathLike):
        root = os.fspath(root)
    if not isinstance(root, str) or not root.strip():
        return None
    return os.path.realpath(os.path.abspath(root))


def resolve_scan_root(root: str, relative_path: str = "") -> str | None:
    """Resolve a scan directory, rejecting absolute paths and link escapes."""
    base = _real_root(root)
    if base is None:
        return None
    rel = relative_path or ""
    if "\x00" in rel or os.path.isabs(rel):
        return None
    candidate = os.path.realpath(os.path.abspath(os.path.join(base, rel))) if rel else base
    return candidate if _is_within(candidate, base) else None


def resolve_member_path(root: str, relative_path: str) -> str | None:
    """Resolve a single member file, rejecting traversal and link escapes."""
    base = _real_root(root)
    if base is None or not isinstance(relative_path, str) or not relative_path.strip():
        return None
    if "\x00" in relative_path or os.path.isabs(relative_path):
        return None
    candidate = os.path.realpath(os.path.abspath(os.path.join(base, relative_path)))
    return candidate if _is_within(candidate, base) else None


def collect_project_files_budgeted(
    root: str,
    relative_path: str = "",
    extensions: Any | None = None,
    *,
    max_files: int = MAX_SCAN_FILES,
    max_dirs: int = MAX_SCAN_DIRS,
) -> tuple[list[str], dict[str, Any]]:
    """Collect project files with extension filtering and traversal budgets.

    Symlinks/junctions are never descended; every matched file is re-checked
    against the workspace after realpath resolution.
    """
    scan_root = resolve_scan_root(root, relative_path)
    if scan_root is None or not os.path.isdir(scan_root):
        return [], {"exists": False, "dirs_scanned": 0, "file_count": 0, "scan_truncated": False}

    base = os.path.realpath(os.path.abspath(root))
    allowed = _normalize_extensions(extensions)
    files: list[str] = []
    dirs_scanned = 0
    truncated_files = False
    truncated_dirs = False

    for dirpath, dirnames, filenames in os.walk(scan_root, followlinks=False):
        dirs_scanned += 1
        if dirs_scanned > max_dirs:
            truncated_dirs = True
            break
        # Prune symlinked/junction directories whose real target escapes root.
        kept_dirs: list[str] = []
        for dirname in dirnames:
            real_dir = os.path.realpath(os.path.join(dirpath, dirname))
            if _is_within(real_dir, base):
                kept_dirs.append(dirname)
        dirnames[:] = sorted(kept_dirs)

        budget_left = max_files - len(files)
        for filename in sorted(filenames):
            if budget_left <= 0:
                truncated_files = True
                break
            if allowed and not any(filename.lower().endswith(ext.lower()) for ext in allowed):
                continue
            full_path = os.path.join(dirpath, filename)
            if not _is_within(os.path.realpath(full_path), base):
                continue
            files.append(os.path.relpath(full_path, base).replace(os.sep, "/"))
            budget_left -= 1
        if truncated_files:
            break

    meta = {
        "exists": True,
        "dirs_scanned": dirs_scanned,
        "file_count": len(files),
        "scan_truncated": truncated_files or truncated_dirs,
    }
    return sorted(files), meta


def collect_project_files(root: str, relative_path: str = "", extensions: Any | None = None) -> list[str]:
    """Backward-compatible wrapper returning only the relative file list."""
    files, _ = collect_project_files_budgeted(root, relative_path, extensions)
    return files


def read_text_limited(path: str, limit: int) -> tuple[str, bool]:
    """Read at most ``limit`` characters directly from disk (no unbounded read)."""
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        chunk = handle.read(max(limit, 0) + 1)
    return chunk[:limit], len(chunk) > limit


def read_project_members(
    root: str,
    rel_paths: list[str],
    *,
    per_file_limit: int,
    total_limit: int = MAX_BATCH_TOTAL_CHARS,
    max_files: int = MAX_BATCH_FILES,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Read multiple workspace members under per-file and total char budgets."""
    contents: dict[str, str] = {}
    truncated_files: list[str] = []
    skipped: list[str] = []
    chars_read = 0
    file_count_truncated = len(rel_paths) > max_files
    total_budget_truncated = False

    for rel in rel_paths[:max_files]:
        full_path = resolve_member_path(root, rel)
        if not full_path or not os.path.isfile(full_path):
            skipped.append(rel)
            continue
        remaining = total_limit - chars_read
        if remaining <= 0:
            total_budget_truncated = True
            skipped.append(rel)
            continue
        text, truncated = read_text_limited(full_path, min(per_file_limit, remaining))
        contents[rel] = text
        chars_read += len(text)
        if truncated:
            truncated_files.append(rel)

    meta = {
        "files_requested": len(rel_paths),
        "files_read": len(contents),
        "files_skipped": skipped,
        "chars_read": chars_read,
        "per_file_limit": per_file_limit,
        "total_limit": total_limit,
        "truncated_files": truncated_files,
        "file_count_truncated": file_count_truncated,
        "total_budget_truncated": total_budget_truncated,
    }
    return contents, meta


def _is_probably_binary(text: str) -> bool:
    """True when decoded text looks like binary rather than source content."""
    if "\x00" in text:
        return True
    if not text:
        return False
    return text.count("\ufffd") / len(text) > MAX_REPLACEMENT_RATIO


def find_files_by_name(
    root: str,
    name: str,
    *,
    max_files: int = MAX_SCAN_FILES,
    max_matches: int = MAX_NAME_MATCHES,
) -> list[str]:
    """Find workspace files whose base name matches, bounded and sandboxed."""
    target = os.path.basename((name or "").strip())
    if not target:
        return []
    files, _ = collect_project_files_budgeted(root, "", "*", max_files=max_files)
    matches = [rel for rel in files if os.path.basename(rel).lower() == target.lower()]
    return matches[:max_matches]


def resolve_reference(
    root: str,
    reference: str,
    *,
    max_files: int = MAX_SCAN_FILES,
    max_matches: int = MAX_NAME_MATCHES,
) -> tuple[str | None, list[str], str]:
    """Resolve a user/AI supplied file reference inside the workspace.

    Accepts a workspace-relative path, an absolute path that lives inside the
    workspace, or a bare file name that is searched for inside the workspace.
    Returns ``(relative_path, candidates, reason)`` where *reason* is one of
    ``ok``, ``empty``, ``invalid``, ``outside``, ``directory``, ``ambiguous``
    or ``missing``.
    """
    base = _real_root(root)
    if base is None:
        return None, [], "invalid"
    raw = reference.strip() if isinstance(reference, str) else ""
    if not raw:
        return None, [], "empty"
    if "\x00" in raw:
        return None, [], "invalid"

    if os.path.isabs(raw):
        resolved = os.path.realpath(os.path.abspath(raw))
        if not _is_within(resolved, base):
            return None, [], "outside"
        if os.path.isdir(resolved):
            return None, [], "directory"
        if os.path.isfile(resolved):
            return os.path.relpath(resolved, base).replace(os.sep, "/"), [], "ok"
        return None, [], "missing"

    inside = resolve_member_path(base, raw)
    if inside and os.path.isfile(inside):
        return os.path.relpath(inside, base).replace(os.sep, "/"), [], "ok"
    if ".." in re.split(r"[\\/]+", raw) and inside is None:
        return None, [], "outside"
    if inside and os.path.isdir(inside):
        return None, [], "directory"

    matches = find_files_by_name(base, raw, max_files=max_files, max_matches=max_matches)
    if len(matches) == 1:
        return matches[0], [], "ok"
    if len(matches) > 1:
        return None, matches, "ambiguous"
    return None, [], "missing"


def describe_coverage(scan_meta: dict[str, Any] | None, read_meta: dict[str, Any] | None) -> tuple[str, bool]:
    """Render a coverage note plus whether only part of the project was inspected.

    Analysis entries must state how much was actually read: otherwise a report
    that inspected a truncated slice looks identical to a full review.
    """
    scan = scan_meta or {}
    read = read_meta or {}
    scanned = int(scan.get("file_count", 0) or 0)

    if not read:
        note = f"覆盖度：工作区扫描到 {scanned} 个文件（仅统计文件名，未读取内容）。"
        return note, bool(scan.get("scan_truncated"))

    read_count = int(read.get("files_read", 0) or 0)
    chars = int(read.get("chars_read", 0) or 0)
    truncated = list(read.get("truncated_files") or [])
    skipped = list(read.get("files_skipped") or [])
    partial = bool(
        truncated
        or skipped
        or read.get("file_count_truncated")
        or read.get("total_budget_truncated")
        or scan.get("scan_truncated")
    )

    parts = [f"覆盖度：工作区扫描到 {scanned} 个文件，实际读取 {read_count} 个，共 {chars} 字符"]
    if truncated:
        parts.append(f"{len(truncated)} 个文件内容被截断")
    if skipped:
        parts.append(f"{len(skipped)} 个文件被跳过（不存在、越界或超出预算）")
    if partial:
        parts.append("结论仅覆盖上述部分内容")
    return "，".join(parts) + "。", partial


def default_skill_roots() -> list[str]:
    """Well-known local locations that may contain importable skills."""
    home = os.path.expanduser("~")
    roots = [
        os.path.join(home, ".trae", "skills"),
        os.path.join(home, ".trae-cn", "skills"),
    ]
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(os.path.join(appdata, "Trae CN", "skills"))
    return roots


def scan_skill_directories(
    roots: list[str],
    *,
    max_dirs: int = MAX_SKILL_SCAN_DIRS,
    max_candidates: int = MAX_SKILL_CANDIDATES,
) -> tuple[list[dict[str, Any]], list[str], bool]:
    """Find skill directories (holding a skill marker file) under *roots*.

    Returns ``(candidates, scanned_roots, truncated)``.
    """
    candidates: list[dict[str, Any]] = []
    scanned_roots: list[str] = []
    truncated = False
    seen: set[str] = set()

    for raw_root in roots:
        root = _real_root(raw_root)
        if root is None or not os.path.isdir(root) or root in scanned_roots:
            continue
        scanned_roots.append(root)
        visited = 0
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            visited += 1
            if visited > max_dirs:
                truncated = True
                break
            dirnames[:] = sorted(name for name in dirnames if not name.startswith("."))
            marker = next((name for name in SKILL_MARKERS if name in filenames), None)
            if marker is None:
                continue
            dirnames[:] = []
            if dirpath in seen:
                continue
            seen.add(dirpath)
            candidates.append({"path": dirpath, "name": os.path.basename(dirpath) or dirpath, "marker": marker})
            if len(candidates) >= max_candidates:
                truncated = True
                break
        if len(candidates) >= max_candidates:
            break

    return candidates, scanned_roots, truncated


def detect_skill_marker(directory: str) -> str | None:
    """Return the skill marker file present in *directory*, if any."""
    try:
        entries = set(os.listdir(directory))
    except OSError:
        return None
    return next((name for name in SKILL_MARKERS if name in entries), None)


def detect_common_issues(content: str, file_name: str) -> list[str]:
    """Detect common code issues and anti-patterns."""
    issues: list[str] = []
    lowered = content.lower()

    if "todo" in lowered or "fixme" in lowered:
        issues.append(f"{file_name}: contains TODO/FIXME markers that should be resolved or tracked.")
    if re.search(r"['\"](?:/tmp|/var|/etc|~|[A-Za-z]:\\)", content):
        issues.append(f"{file_name}: contains hardcoded filesystem paths which can break portability.")
    if "exec(" in lowered or "eval(" in lowered:
        issues.append(f"{file_name}: dynamic execution is risky and should be validated carefully.")
    if "subprocess" in lowered and "shell=True" in lowered:
        issues.append(f"{file_name}: subprocess call may be vulnerable to shell injection when shell=True is used.")
    if "print(" in lowered and "debug" in lowered:
        issues.append(f"{file_name}: debug logging may be left in production code.")
    if not issues:
        issues.append(f"{file_name}: no obvious issues detected in the inspected snippet.")
    return issues


def build_project_summary(files: list[str], project_root: str) -> dict[str, Any]:
    """Build a summary of project structure and file distribution."""
    ext_counts: dict[str, int] = {}
    for file_name in files:
        _, ext = os.path.splitext(file_name)
        ext = ext.lower() or "<no-extension>"
        ext_counts[ext] = ext_counts.get(ext, 0) + 1

    summary_text = (
        f"Project root: {project_root}\n"
        f"Files inspected: {len(files)}\n"
        f"Extensions: {', '.join(f'{key}={value}' for key, value in sorted(ext_counts.items())) or 'none'}"
    )

    return {
        "project_root": project_root,
        "files": files,
        "counts_by_extension": ext_counts,
        "summary": summary_text,
    }


def build_code_review_report(files_by_path: dict[str, str]) -> dict[str, Any]:
    """Generate a comprehensive code review report."""
    review_items: list[str] = []
    issue_count = 0
    for file_path, content in files_by_path.items():
        issues = detect_common_issues(content, file_path)
        if issues:
            review_items.extend(issues)
            issue_count += len(issues)

    return {
        "review": "Code review finished in read-only mode.",
        "issues": review_items,
        "issue_count": issue_count,
    }


def build_error_fix_report(files_by_path: dict[str, str]) -> dict[str, Any]:
    """Generate error detection and fix suggestions."""
    fixes: list[str] = []
    for file_path, content in files_by_path.items():
        lowered = content.lower()
        if "todo" in lowered or "fixme" in lowered:
            fixes.append(f"{file_path}: replace stale TODO/FIXME markers with actionable tracked issues or remove them.")
        if re.search(r"['\"](?:/tmp|/var|/etc|~|[A-Za-z]:\\)", content):
            fixes.append(f"{file_path}: move filesystem paths to config or runtime input instead of hardcoded paths.")
        if "exec(" in lowered or "eval(" in lowered:
            fixes.append(f"{file_path}: avoid dynamic execution unless strictly necessary; prefer explicit APIs and validation.")
        if "shell=True" in lowered and "subprocess" in lowered:
            fixes.append(f"{file_path}: remove shell=True or sanitize parameters to reduce injection risk.")
    if not fixes:
        fixes.append("No urgent fix suggestions detected in the inspected files.")
    return {"fixes": fixes}


def build_multi_file_summary(files_by_path: dict[str, str]) -> dict[str, Any]:
    """Build a multi-file cross-cutting summary."""
    ordered_files = list(files_by_path.keys())
    total_chars = sum(len(content) for content in files_by_path.values())
    summary_text = (
        f"Project overview: {len(ordered_files)} files, {total_chars} chars inspected.\n"
        f"Primary files: {', '.join(ordered_files[:10])}"
    )
    return {
        "project": {
            "file_count": len(ordered_files),
            "total_chars": total_chars,
            "files": ordered_files,
        },
        "summary": summary_text,
    }


def build_quick_audit(
    files_by_path: dict[str, str],
    tone: str = "professional",
    *,
    include_review: bool = True,
    include_fixes: bool = True,
    include_structure: bool = True,
    coverage: str = "",
) -> dict[str, Any]:
    """Build a quick audit combining the analysis sections that are enabled."""
    content_parts: list[str] = [coverage] if coverage else []
    audit: dict[str, Any] = {}
    if coverage:
        audit["coverage"] = coverage
    if include_review:
        review = build_code_review_report(files_by_path)
        audit["review"] = review
        content_parts.extend(["Review:", *review["issues"][:5]])
    if include_fixes:
        fixes = build_error_fix_report(files_by_path)
        audit["fixes"] = fixes
        content_parts.extend(["Fixes:", *fixes["fixes"][:5]])
    if include_structure:
        structure = build_multi_file_summary(files_by_path)
        audit["structure"] = structure
        content_parts.extend(["Structure:", structure["summary"]])
    audit["formatted"] = format_response("\n".join(content_parts), tone)
    return audit


def format_response(text: str, tone: str) -> str:
    """Format response with appropriate tone."""
    if tone == "catgirl":
        return f"喵~ meow~ nya~ {text}"
    return f"Professional review:\n{text}"


@neko_plugin
class DevelopmentAidePlugin(NekoPluginBase):
    """开发辅助型插件：导入 Skill、审查代码、分析结构并给出只读建议。"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.skill_path = ""
        self.workspace_root = ""
        self.read_only = True
        self.max_chars = DEFAULT_MAX_CHARS
        self.default_file_extensions = list(DEFAULT_EXTENSIONS)
        self.analysis_tone = "professional"
        self.enable_code_review = True
        self.enable_error_fix = True
        self.enable_project_summary = True
        self.enable_multi_file_summary = True

    # -- lifecycle ---------------------------------------------------------

    @lifecycle(id="startup")
    async def on_startup(self, **_) -> Ok | Err:
        """Initialize plugin on startup."""
        try:
            await self._reload_settings()
            return Ok({"status": "ready", "read_only": self.read_only})
        except Exception as e:
            return Err(SdkError(f"Startup failed: {str(e)}"))

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_) -> Ok | Err:
        """Clean up on shutdown."""
        return Ok({"status": "stopped"})

    @lifecycle(id="config_change")
    async def on_config_change(self, **_) -> Ok | Err:
        """Reload settings when configuration changes."""
        try:
            await self._reload_settings()
            return Ok({"status": "reloaded", "read_only": self.read_only})
        except Exception as e:
            return Err(SdkError(f"Config reload failed: {str(e)}"))

    async def _reload_settings(self) -> None:
        """Load and apply plugin settings from persistent configuration."""
        cfg = await self.config.dump()
        settings = cfg.get("settings", {}) if isinstance(cfg, dict) else {}
        if not isinstance(settings, dict):
            settings = {}
        self.skill_path = str(settings.get("skill_path", self.skill_path) or "")
        self.workspace_root = str(settings.get("workspace_root", self.workspace_root) or "")
        self.read_only = bool(settings.get("read_only", self.read_only))
        try:
            self.max_chars = coerce_read_limit(settings.get("max_chars", self.max_chars))[0]
        except ValueError:
            self.max_chars = DEFAULT_MAX_CHARS
        self.analysis_tone = str(settings.get("analysis_tone", self.analysis_tone) or "professional")
        self.enable_code_review = bool(settings.get("enable_code_review", self.enable_code_review))
        self.enable_error_fix = bool(settings.get("enable_error_fix", self.enable_error_fix))
        self.enable_project_summary = bool(settings.get("enable_project_summary", self.enable_project_summary))
        self.enable_multi_file_summary = bool(
            settings.get("enable_multi_file_summary", self.enable_multi_file_summary)
        )
        extensions = settings.get("default_file_extensions", self.default_file_extensions)
        if isinstance(extensions, (list, tuple, set)) and extensions:
            self.default_file_extensions = sorted(_normalize_extensions(extensions))

    def _settings_payload(self) -> dict[str, Any]:
        return {
            "skill_path": self.skill_path,
            "workspace_root": self.workspace_root,
            "read_only": self.read_only,
            "max_chars": self.max_chars,
            "analysis_tone": self.analysis_tone,
            "enable_code_review": self.enable_code_review,
            "enable_error_fix": self.enable_error_fix,
            "enable_project_summary": self.enable_project_summary,
            "enable_multi_file_summary": self.enable_multi_file_summary,
            "default_file_extensions": list(self.default_file_extensions),
        }

    async def _persist_settings(self) -> None:
        """Persist the current settings so a config reload keeps them."""
        await self.config.set("settings", self._settings_payload())

    # -- guards ------------------------------------------------------------

    def _configured_root(self) -> str | None:
        """Return the resolved workspace root, or None when unset/missing."""
        root = resolve_scan_root(self.workspace_root)
        if root is None or not os.path.isdir(root):
            return None
        return root

    def _feature_enabled_error(self, attr: str, label: str) -> Err | None:
        if bool(getattr(self, attr, False)):
            return None
        return Err(SdkError(f"{label}功能已在设置中关闭，请在面板开启后再使用。"))

    def _ok(self, entry: str, payload: dict[str, Any]) -> Ok:
        """Return a success result and record the outcome for diagnostics."""
        detail = payload.get("result") or payload.get("status") or ""
        self.logger.info(f"[{entry}] ok {detail}".rstrip())
        return Ok(payload)

    def _err(self, entry: str, message: str) -> Err:
        """Return an error result and record why it failed."""
        self.logger.warning(f"[{entry}] failed: {message}")
        return Err(SdkError(message))

    def _workspace_or_error(self, entry: str) -> tuple[str | None, Err | None]:
        """Resolve the workspace root, explaining precisely what is wrong."""
        raw = str(self.workspace_root or "").strip()
        if not raw:
            return None, self._err(
                entry,
                "尚未配置工作区目录。请在插件设置面板把「项目工作区路径」填成项目根目录的绝对路径并保存。",
            )
        root = self._configured_root()
        if root is None:
            return None, self._err(entry, f"工作区目录不存在或不是目录：{raw}。请在设置面板修正后重新保存。")
        return root, None

    async def _resolve_file_reference(self, entry: str, reference: str) -> tuple[str | None, Err | None]:
        """Resolve a file reference (relative, absolute-in-workspace or file name)."""
        root, error = self._workspace_or_error(entry)
        if error is not None:
            return None, error
        assert root is not None
        relative, candidates, reason = await asyncio.to_thread(resolve_reference, root, reference)
        if reason == "ok" and relative:
            return relative, None
        if reason == "empty":
            return None, self._err(entry, "请给出要读取的文件：工作区相对路径、工作区内的绝对路径或文件名均可。")
        if reason == "invalid":
            return None, self._err(entry, "文件路径无效。")
        if reason == "outside":
            return None, self._err(
                entry,
                f"该路径不在当前工作区内，已拒绝：{reference}\n当前工作区：{root}\n"
                "请把文件放进工作区，或把工作区路径改成该文件所在目录后重新保存设置。",
            )
        if reason == "directory":
            return None, self._err(entry, f"这是一个目录而不是文件：{reference}。可先用 list_project_files 查看目录内容。")
        if reason == "ambiguous":
            joined = "、".join(candidates)
            return None, self._err(
                entry,
                f"工作区内有 {len(candidates)} 个同名文件，请改用完整路径重试：{joined}",
            )
        return None, self._err(
            entry,
            f"工作区内找不到该文件：{reference}\n当前工作区：{root}\n可先用 list_project_files 列出可用文件。",
        )

    async def _collect_workspace_files(
        self,
        relative_path: str,
        extensions: list[str] | None,
    ) -> tuple[str | None, list[str], dict[str, Any], Err | None]:
        root = self._configured_root()
        if root is None:
            if not str(self.workspace_root or "").strip():
                return None, [], {}, Err(SdkError("尚未配置工作区目录，请先在设置面板保存 workspace_root。"))
            return None, [], {}, Err(SdkError(f"工作区不存在或不是目录：{self.workspace_root}"))
        if ("\x00" in (relative_path or "")) or ((relative_path or "") and os.path.isabs(relative_path)):
            return None, [], {}, Err(SdkError("relative_path 必须是工作区内的相对路径，不允许绝对路径。"))
        if resolve_scan_root(root, relative_path) is None:
            return None, [], {}, Err(SdkError(f"扫描路径越出工作区，已拒绝：{relative_path}"))
        files, meta = await asyncio.to_thread(
            collect_project_files_budgeted,
            root,
            relative_path or "",
            extensions or self.default_file_extensions,
        )
        return root, files, meta, None

    # -- AI/tool entries ---------------------------------------------------

    @plugin_entry(
        id="import_skill",
        name="导入 Skill 技能包",
        description=(
            "导入本机的 Skill 技能目录并持久化保存，供后续开发辅助使用。"
            "当用户说「导入技能包」「用某个 skill」「帮我把这个技能装上」时调用。"
            "skill_path 必须是绝对路径，也可以是 SKILL.md 的路径，插件会自动取它所在目录。只读取该目录，不修改任何文件。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "skill_path": {
                    "type": "string",
                    "description": "技能目录的绝对路径；或技能标记文件（如 SKILL.md）的绝对路径。",
                },
                "skill_name": {
                    "type": "string",
                    "description": "可选，技能显示名；省略时使用目录名。",
                },
            },
            "required": ["skill_path"],
        },
        llm_result_fields=["result", "skill_path", "skill_name", "message"],
    )
    @ui.action(id="import_skill", label=tr("actions.import_skill.label", default="导入 Skill"), tone="primary")
    async def import_skill(self, skill_path: str = "", skill_name: str = "", **_) -> Ok | Err:
        """Import a skill directory (or a skill marker file) for enhanced capabilities."""
        entry = "import_skill"
        raw = str(skill_path or "").strip()
        if not raw:
            return self._err(entry, "请提供技能目录的绝对路径（或 SKILL.md 的绝对路径）。")
        if not os.path.isabs(raw):
            return self._err(entry, f"skill_path 必须是绝对路径：{raw}")
        target = os.path.abspath(raw)
        if os.path.isfile(target):
            target = os.path.dirname(target)
        if not os.path.isdir(target):
            return self._err(entry, f"技能目录不存在或不是目录：{target}")
        marker = await asyncio.to_thread(detect_skill_marker, target)
        if marker is None:
            expected = " / ".join(SKILL_MARKERS)
            return self._err(
                entry,
                f"该目录没有技能标记文件（{expected}）：{target}\n请选择技能目录本身，或直接选择其中的 SKILL.md。",
            )
        self.skill_path = target
        self.read_only = True
        try:
            await self._persist_settings()
        except Exception as exc:
            return self._err(entry, f"保存技能路径失败：{exc}")
        return self._ok(
            entry,
            {
                "result": "success",
                "skill_path": target,
                "skill_name": str(skill_name or "").strip() or os.path.basename(target) or target,
                "skill_marker": marker,
                "message": "Skill 已导入并保存，当前为只读访问模式。",
            },
        )

    @plugin_entry(
        id="scan_skill_dirs",
        name="扫描技能目录",
        description=(
            "只读扫描本机常见的技能存放位置（以及可选的自定义根目录），列出可作为 Skill 导入的技能目录，"
            "每个候选都包含 SKILL.md 等标记文件。用户说「有哪些技能」「扫描技能」或需要从列表里挑一个技能时调用；"
            "把返回的 path 交给 import_skill 即可完成导入。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "extra_roots": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选，额外的绝对路径根目录（例如用户自己存放技能的文件夹），会一并扫描。",
                },
            },
        },
        llm_result_fields=["result", "candidates", "count", "scanned_roots"],
    )
    @ui.action(id="scan_skill_dirs", label=tr("actions.scan_skill_dirs.label", default="扫描技能目录"), tone="info")
    async def scan_skill_dirs(self, extra_roots: list[str] | None = None, **_) -> Ok | Err:
        """List candidate skill directories under well-known local locations."""
        entry = "scan_skill_dirs"
        roots = default_skill_roots()
        workspace = str(self.workspace_root or "").strip()
        if workspace and os.path.isabs(workspace):
            roots.append(workspace)
        for extra in extra_roots or []:
            value = str(extra or "").strip()
            if value and os.path.isabs(value):
                roots.append(value)
        candidates, scanned_roots, truncated = await asyncio.to_thread(scan_skill_directories, roots)
        if not candidates:
            return self._err(
                entry,
                "没有扫描到可导入的技能目录（需要目录内包含 "
                + " / ".join(SKILL_MARKERS)
                + "）。已扫描位置："
                + ("、".join(scanned_roots) if scanned_roots else "无可用位置")
                + "\n可以把技能目录的绝对路径直接告诉我，或用 import_skill 指定。",
            )
        return self._ok(
            entry,
            {
                "result": "success",
                "candidates": candidates,
                "count": len(candidates),
                "scanned_roots": scanned_roots,
                "truncated": truncated,
                "message": "扫描完成，请选择一个技能目录后调用 import_skill 导入。",
            },
        )

    @plugin_entry(
        id="list_project_files",
        name="列出项目文件",
        description=(
            "只读列出工作区内的文件清单，用于确认有哪些源码/配置文件可看。"
            "当用户问「项目里有什么文件」「有哪些代码」或需要先知道文件名再读取时调用。遍历有文件数与目录数上限。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "relative_path": {
                    "type": "string",
                    "description": "可选，工作区内的相对子目录（如 src）；省略表示整个工作区。",
                },
                "extensions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选，只列出这些扩展名，例如 [\".py\", \".ts\"]；省略使用面板配置的扩展名。",
                },
            },
        },
        llm_result_fields=["result", "files", "count", "truncated"],
    )
    async def list_project_files(self, relative_path: str = "", extensions: list[str] | None = None, **_) -> Ok | Err:
        """List files in project directory with optional filtering."""
        entry = "list_project_files"
        root, files, meta, error = await self._collect_workspace_files(relative_path, extensions)
        if error is not None:
            return error
        return self._ok(
            entry,
            {
                "result": "success",
                "files": files,
                "count": len(files),
                "root": root,
                "truncated": bool(meta.get("scan_truncated")),
                "dirs_scanned": meta.get("dirs_scanned", 0),
            },
        )

    @plugin_entry(
        id="read_project_file",
        name="读取项目文件（只读）",
        description=(
            "读取单个文件的内容。当用户想让你看某个文件、问「这个 py 文件写了什么」「帮我看看 xxx」时调用。"
            "relative_path 支持三种写法：① 工作区内相对路径（推荐，如 src/main.py）② 工作区内的绝对路径 "
            "③ 只给文件名（如 main.py），插件会在工作区内自动查找；找不到或有重名时会明确告知原因。"
            "只读，任何情况下都不会写入或修改文件。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "relative_path": {
                    "type": "string",
                    "description": "要读取的文件：工作区相对路径、工作区内的绝对路径，或文件名（自动查找）。",
                },
                "max_chars": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "可选，本次最多读取的字符数；省略使用面板配置的单文件上限。",
                },
            },
            "required": ["relative_path"],
        },
        llm_result_fields=["result", "path", "content", "truncated"],
    )
    @ui.action(id="read_project_file", label=tr("actions.read_file.label", default="读取文件"), tone="info")
    async def read_project_file(self, relative_path: str = "", max_chars: int | None = None, **_) -> Ok | Err:
        """Read and return file content within a bounded per-file char limit."""
        entry = "read_project_file"
        try:
            limit, capped = coerce_read_limit(max_chars, self.max_chars)
        except ValueError as exc:
            return self._err(entry, str(exc))
        relative, error = await self._resolve_file_reference(entry, relative_path)
        if error is not None:
            return error
        assert relative is not None
        root = self._configured_root()
        assert root is not None
        full_path = resolve_member_path(root, relative)
        if not full_path or not os.path.isfile(full_path):
            return self._err(entry, f"文件不可读或指向了工作区外部：{relative}")
        try:
            content, truncated = await asyncio.to_thread(read_text_limited, full_path, limit)
        except OSError as exc:
            return self._err(entry, f"读取文件失败：{exc}")
        if _is_probably_binary(content):
            return self._err(entry, f"该文件不是文本文件（检测到二进制内容），已停止读取：{relative}")
        return self._ok(
            entry,
            {
                "result": "success",
                "path": relative,
                "resolved_path": full_path,
                "workspace_root": root,
                "content": content,
                "truncated": truncated,
                "chars_read": len(content),
                "limit": limit,
                "limit_capped": capped,
                "read_only": self.read_only,
            },
        )

    @plugin_entry(
        id="code_review",
        name="代码审查",
        description=(
            "只读审查工作区（或指定子目录）的代码，给出潜在问题、质量提示和关注点，并在结果中说明实际读取了多少文件。"
            "当用户说「审查代码」「帮我看看代码有没有问题」「代码质量如何」时调用。受功能开关 enable_code_review 控制。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "relative_path": {
                    "type": "string",
                    "description": "可选，工作区内的相对子目录（如 src）；省略表示整个工作区。",
                },
                "extensions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选，只审查这些扩展名，例如 [\".py\", \".ts\"]。",
                },
                "tone": {
                    "type": "string",
                    "enum": ["professional", "catgirl"],
                    "description": "可选，回复语气；省略时使用面板配置的语气。",
                },
            },
        },
        llm_result_fields=["result", "report"],
    )
    @ui.action(id="code_review", label=tr("actions.code_review.label", default="代码审查"), tone="info")
    async def code_review(
        self,
        relative_path: str = "",
        extensions: list[str] | None = None,
        tone: str = "",
        **_,
    ) -> Ok | Err:
        """Perform comprehensive code review with bounded file reads."""
        entry = "code_review"
        gate = self._feature_enabled_error("enable_code_review", "代码审查")
        if gate is not None:
            return gate
        root, files, scan_meta, error = await self._collect_workspace_files(relative_path, extensions)
        if error is not None:
            return error
        if not files:
            return self._err(entry, "工作区内没有找到可审查的代码文件。可先用 list_project_files 确认文件清单。")
        limit = coerce_read_limit(None, self.max_chars)[0]
        inferred_files, read_meta = await asyncio.to_thread(
            read_project_members,
            root,
            files,
            per_file_limit=limit,
            total_limit=MAX_BATCH_TOTAL_CHARS,
            max_files=MAX_BATCH_FILES,
        )
        if not inferred_files:
            return self._err(entry, "候选文件均不可读（不存在、越界或非文本），未做任何分析。")
        coverage, partial = describe_coverage(scan_meta, read_meta)
        report = build_code_review_report(inferred_files)
        report["formatted"] = format_response(
            f"{coverage}\n" + "\n".join(report["issues"]),
            tone or self.analysis_tone,
        )
        report["coverage"] = coverage
        report["partial"] = partial
        report["budget"] = {"scan": scan_meta, "read": read_meta}
        return self._ok(entry, {"result": "success", "partial": partial, "report": report})

    @plugin_entry(
        id="error_fix",
        name="错误定位与修复建议",
        description=(
            "只读定位常见错误（TODO/FIXME、硬编码路径、exec/eval、shell=True 等）并给出修复建议，同时说明实际读取范围。"
            "当用户说「帮我看报错」「定位问题」「怎么修」时调用。受功能开关 enable_error_fix 控制。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "relative_path": {
                    "type": "string",
                    "description": "可选，工作区内的相对子目录；省略表示整个工作区。",
                },
                "extensions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选，只检查这些扩展名，例如 [\".py\"]。",
                },
                "tone": {
                    "type": "string",
                    "enum": ["professional", "catgirl"],
                    "description": "可选，回复语气；省略时使用面板配置的语气。",
                },
            },
        },
        llm_result_fields=["result", "fixes"],
    )
    @ui.action(id="error_fix", label=tr("actions.error_fix.label", default="修复建议"), tone="warning")
    async def error_fix(
        self,
        relative_path: str = "",
        extensions: list[str] | None = None,
        tone: str = "",
        **_,
    ) -> Ok | Err:
        """Detect and suggest fixes for common errors."""
        entry = "error_fix"
        gate = self._feature_enabled_error("enable_error_fix", "错误定位与修复建议")
        if gate is not None:
            return gate
        root, files, scan_meta, error = await self._collect_workspace_files(relative_path, extensions)
        if error is not None:
            return error
        if not files:
            return self._err(entry, "工作区内没有找到可检查的文件。可先用 list_project_files 确认文件清单。")
        limit = coerce_read_limit(None, self.max_chars)[0]
        inferred_files, read_meta = await asyncio.to_thread(
            read_project_members,
            root,
            files,
            per_file_limit=limit,
            total_limit=MAX_BATCH_TOTAL_CHARS,
            max_files=MAX_BATCH_FILES,
        )
        if not inferred_files:
            return self._err(entry, "候选文件均不可读（不存在、越界或非文本），未做任何分析。")
        coverage, partial = describe_coverage(scan_meta, read_meta)
        fixes = build_error_fix_report(inferred_files)
        fixes["formatted"] = format_response(
            f"{coverage}\n" + "\n".join(fixes["fixes"]),
            tone or self.analysis_tone,
        )
        fixes["coverage"] = coverage
        fixes["partial"] = partial
        fixes["budget"] = {"scan": scan_meta, "read": read_meta}
        return self._ok(entry, {"result": "success", "partial": partial, "fixes": fixes})

    @plugin_entry(
        id="project_summary",
        name="项目结构摘要",
        description=(
            "只读统计工作区的文件分布与扩展名构成，生成项目结构概览（只统计文件名，不读取文件内容）。"
            "当用户问「项目结构」「有哪些文件类型」「工程大概什么样」时调用。受功能开关 enable_project_summary 控制。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "relative_path": {
                    "type": "string",
                    "description": "可选，工作区内的相对子目录；省略表示整个工作区。",
                },
                "extensions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选，只统计这些扩展名，例如 [\".py\"]。",
                },
                "tone": {
                    "type": "string",
                    "enum": ["professional", "catgirl"],
                    "description": "可选，回复语气；省略时使用面板配置的语气。",
                },
            },
        },
        llm_result_fields=["result", "summary"],
    )
    @ui.action(id="project_summary", label=tr("actions.project_summary.label", default="结构摘要"), tone="primary")
    async def project_summary(
        self,
        relative_path: str = "",
        extensions: list[str] | None = None,
        tone: str = "",
        **_,
    ) -> Ok | Err:
        """Generate project structure summary."""
        entry = "project_summary"
        gate = self._feature_enabled_error("enable_project_summary", "项目结构分析摘要")
        if gate is not None:
            return gate
        root, files, scan_meta, error = await self._collect_workspace_files(relative_path, extensions)
        if error is not None:
            return error
        if not files:
            return self._err(entry, "工作区内没有找到可分析的项目文件。可先用 list_project_files 确认文件清单。")
        coverage, partial = describe_coverage(scan_meta, None)
        summary = build_project_summary(files, root)
        summary["formatted"] = format_response(
            summary["summary"] + "\n" + coverage,
            tone or self.analysis_tone,
        )
        summary["coverage"] = coverage
        summary["partial"] = partial
        summary["budget"] = {"scan": scan_meta}
        return self._ok(entry, {"result": "success", "partial": partial, "summary": summary})

    @plugin_entry(
        id="multi_file_summary",
        name="多文件汇总",
        description=(
            "读取多个文件内容后形成跨文件总览、热点与建议，并在结果中说明实际读取了多少文件、是否被截断。"
            "当用户想让你「综合看几个文件」「整体评估一下」时调用。受功能开关 enable_multi_file_summary 控制，读取有文件数与总字符预算。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "relative_path": {
                    "type": "string",
                    "description": "可选，工作区内的相对子目录；省略表示整个工作区。",
                },
                "extensions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选，只汇总这些扩展名，例如 [\".py\"]。",
                },
                "tone": {
                    "type": "string",
                    "enum": ["professional", "catgirl"],
                    "description": "可选，回复语气；省略时使用面板配置的语气。",
                },
            },
        },
        llm_result_fields=["result", "summary"],
    )
    @ui.action(id="multi_file_summary", label=tr("actions.multi_file_summary.label", default="多文件汇总"), tone="success")
    async def multi_file_summary(
        self,
        relative_path: str = "",
        extensions: list[str] | None = None,
        tone: str = "",
        **_,
    ) -> Ok | Err:
        """Summarize insights across multiple files."""
        entry = "multi_file_summary"
        gate = self._feature_enabled_error("enable_multi_file_summary", "多文件汇总")
        if gate is not None:
            return gate
        root, files, scan_meta, error = await self._collect_workspace_files(relative_path, extensions)
        if error is not None:
            return error
        if not files:
            return self._err(entry, "工作区内没有找到可汇总的文件。可先用 list_project_files 确认文件清单。")
        limit = coerce_read_limit(None, self.max_chars)[0]
        selected_files, read_meta = await asyncio.to_thread(
            read_project_members,
            root,
            files,
            per_file_limit=limit,
            total_limit=MAX_BATCH_TOTAL_CHARS,
            max_files=12,
        )
        if not selected_files:
            return self._err(entry, "候选文件均不可读（不存在、越界或非文本），未做任何分析。")
        coverage, partial = describe_coverage(scan_meta, read_meta)
        summary = build_multi_file_summary(selected_files)
        summary["formatted"] = format_response(
            summary["summary"] + "\n" + coverage,
            tone or self.analysis_tone,
        )
        summary["coverage"] = coverage
        summary["partial"] = partial
        summary["budget"] = {"scan": scan_meta, "read": read_meta}
        return self._ok(entry, {"result": "success", "partial": partial, "summary": summary})

    @plugin_entry(
        id="quick_audit",
        name="一键开发审查",
        description=(
            "一次性执行代码审查、修复建议和结构摘要，生成总览式开发建议，并说明实际读取范围。"
            "当用户说「整体看一遍」「帮我做个全面检查」时调用。只执行设置中已启用的功能，全部关闭时拒绝执行。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "relative_path": {
                    "type": "string",
                    "description": "可选，工作区内的相对子目录；省略表示整个工作区。",
                },
                "extensions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选，只看这些扩展名，例如 [\".py\"]。",
                },
                "tone": {
                    "type": "string",
                    "enum": ["professional", "catgirl"],
                    "description": "可选，回复语气；省略时使用面板配置的语气。",
                },
            },
        },
        llm_result_fields=["result", "audit"],
    )
    @ui.action(id="quick_audit", label=tr("actions.quick_audit.label", default="一键开发审查"), tone="primary")
    async def quick_audit(
        self,
        relative_path: str = "",
        extensions: list[str] | None = None,
        tone: str = "",
        **_,
    ) -> Ok | Err:
        """Execute comprehensive quick audit combining enabled analyses."""
        entry = "quick_audit"
        include_review = self.enable_code_review
        include_fixes = self.enable_error_fix
        include_structure = self.enable_multi_file_summary
        if not (include_review or include_fixes or include_structure):
            return self._err(entry, "代码审查、修复建议与多文件汇总功能均已关闭，无法执行一键开发审查。")
        root, files, scan_meta, error = await self._collect_workspace_files(relative_path, extensions)
        if error is not None:
            return error
        if not files:
            return self._err(entry, "工作区内没有找到可审查的文件。可先用 list_project_files 确认文件清单。")
        limit = coerce_read_limit(None, self.max_chars)[0]
        selected_files, read_meta = await asyncio.to_thread(
            read_project_members,
            root,
            files,
            per_file_limit=limit,
            total_limit=MAX_BATCH_TOTAL_CHARS,
            max_files=12,
        )
        if not selected_files:
            return self._err(entry, "候选文件均不可读（不存在、越界或非文本），未做任何分析。")
        coverage, partial = describe_coverage(scan_meta, read_meta)
        audit = build_quick_audit(
            selected_files,
            tone=tone or self.analysis_tone,
            include_review=include_review,
            include_fixes=include_fixes,
            include_structure=include_structure,
            coverage=coverage,
        )
        audit["partial"] = partial
        audit["budget"] = {"scan": scan_meta, "read": read_meta}
        return self._ok(entry, {"result": "success", "partial": partial, "audit": audit})

    # -- UI actions (must carry BOTH @plugin_entry and @ui.action, #2) -----

    @plugin_entry(
        id="save_settings",
        name="保存设置",
        description=(
            "保存 Development-Aide 面板设置（工作区、读取上限、语气、功能开关）并持久化，重载后不丢失。"
            "返回值会说明工作区目录是否存在，便于在读取失败前先发现问题。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "config": {
                    "type": "object",
                    "description": "要保存的完整设置对象。",
                    "properties": {
                        "skill_path": {"type": "string", "description": "已导入技能目录的绝对路径，可为空。"},
                        "workspace_root": {
                            "type": "string",
                            "description": "项目根目录的绝对路径；留空表示暂不启用文件读取。",
                        },
                        "read_only": {"type": "boolean", "description": "只读模式，建议保持 true。"},
                        "max_chars": {
                            "type": "integer",
                            "minimum": 1,
                            "description": "单文件最多读取的字符数。",
                        },
                        "analysis_tone": {
                            "type": "string",
                            "enum": ["professional", "catgirl"],
                            "description": "回复语气。",
                        },
                        "enable_code_review": {"type": "boolean", "description": "是否启用代码审查。"},
                        "enable_error_fix": {"type": "boolean", "description": "是否启用错误定位与修复建议。"},
                        "enable_project_summary": {"type": "boolean", "description": "是否启用项目结构摘要。"},
                        "enable_multi_file_summary": {"type": "boolean", "description": "是否启用多文件汇总。"},
                    },
                },
            },
            "required": ["config"],
        },
        llm_result_fields=["status", "config", "message"],
    )
    @ui.action(
        id="save_settings",
        label=tr("actions.save.label", default="保存设置"),
        tone="primary",
        refresh_context=True,
    )
    async def save_settings(self, config: dict | None = None, **_) -> Ok | Err:
        """Validate, apply and persist plugin configuration."""
        entry = "save_settings"
        if not isinstance(config, dict):
            return self._err(entry, "config 必须是对象。")
        workspace_root = str(config.get("workspace_root", self.workspace_root) or "").strip()
        if workspace_root and not os.path.isabs(workspace_root):
            return self._err(entry, "工作区路径必须是绝对路径，例如 /home/you/project 或 D:\\projects\\demo。")
        try:
            max_chars, capped = coerce_read_limit(config.get("max_chars", self.max_chars))
        except ValueError as exc:
            return self._err(entry, str(exc))
        analysis_tone = str(config.get("analysis_tone", self.analysis_tone) or "professional")
        if analysis_tone not in {"professional", "catgirl"}:
            return self._err(entry, "analysis_tone 只能是 professional 或 catgirl。")

        self.skill_path = str(config.get("skill_path", self.skill_path) or "").strip()
        self.workspace_root = workspace_root
        self.read_only = bool(config.get("read_only", self.read_only))
        self.max_chars = max_chars
        self.analysis_tone = analysis_tone
        self.enable_code_review = bool(config.get("enable_code_review", self.enable_code_review))
        self.enable_error_fix = bool(config.get("enable_error_fix", self.enable_error_fix))
        self.enable_project_summary = bool(config.get("enable_project_summary", self.enable_project_summary))
        self.enable_multi_file_summary = bool(
            config.get("enable_multi_file_summary", self.enable_multi_file_summary)
        )
        try:
            await self._persist_settings()
        except Exception as exc:
            return self._err(entry, f"保存设置失败：{exc}")

        exists = bool(workspace_root) and os.path.isdir(workspace_root)
        if not workspace_root:
            message = "设置已保存，但还没有配置工作区目录；在读取文件前请先填写项目根目录。"
        elif not exists:
            message = f"设置已保存，但该目录不存在：{workspace_root}；读取文件会失败，请修正后重新保存。"
        else:
            message = "设置已保存并已生效。"
        return self._ok(
            entry,
            {
                "status": "saved",
                "config": self._settings_payload(),
                "workspace_configured": bool(workspace_root),
                "workspace_exists": exists,
                "max_chars_capped": capped,
                "message": message,
            },
        )

    @ui.context(id="settings")
    async def settings_context(self) -> dict[str, Any]:
        """Provide settings context for UI."""
        return {
            "config": self._settings_payload(),
            "status": {
                "ready": True,
                "mode": self.analysis_tone,
                "read_only": self.read_only,
                "skill_path": self.skill_path,
                "skill_loaded": bool(self.skill_path) and os.path.isdir(self.skill_path),
                "workspace_root": self.workspace_root,
                "workspace_configured": bool(str(self.workspace_root or "").strip()),
                "workspace_exists": self._configured_root() is not None,
                "features_enabled": {
                    "code_review": self.enable_code_review,
                    "error_fix": self.enable_error_fix,
                    "project_summary": self.enable_project_summary,
                    "multi_file_summary": self.enable_multi_file_summary,
                },
            },
        }
