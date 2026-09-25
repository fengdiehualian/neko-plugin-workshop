"""Claude Code Adapter — 本地 Claude 会话文件读取器。

照搬 cc-switch（farion1231/cc-switch）会话管理器的实现思路：
直接扫描 ``~/.claude/projects/`` 目录下的 ``*.jsonl`` 会话记录文件，
解析出会话标题、时间、消息内容等，供插件前端展示。

与 plugin 自身的 SessionManager（PluginStore 持久化的恢复索引）不同，
本模块读取的是 Claude CLI 原生的对话存档，包含完整的对话内容。

设计要点（对齐 cc-switch session_manager/providers/claude.rs）：
- 只读 head 10 行 + tail 30 行提取元数据，避免整文件加载
- 标题优先级：custom-title > 首条真实用户消息 > 项目目录名
- 跳过系统注入的 caveat 和 slash command（/clear、/compact 等）
- user 消息若全为 tool_result 则重分类为 "tool" 角色
- 跳过 ``agent-`` 前缀的子代理会话文件
- 删除会话时同时删除 jsonl 主文件和同名 sidecar 目录
"""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime
from typing import Any, Optional

TITLE_MAX_CHARS = 120
SUMMARY_MAX_CHARS = 160
CONTENT_MAX_CHARS = 8000
DEFAULT_SCAN_LIMIT = 200
HEAD_LINES = 10
TAIL_LINES = 30


# ---------------------------------------------------------------------------
# 路径与基础工具
# ---------------------------------------------------------------------------


def _windows_known_folder_home() -> Optional[str]:
    """用 Windows Known Folder API 获取用户主目录。

    等价于 cc-switch（Rust ``dirs::home_dir()``）的做法：不受进程
    环境变量（HOME/USERPROFILE）影响。实测发现 Steam 启动的 N.E.K.O
    插件进程环境变量与真实用户目录不一致，仅靠
    ``os.path.expanduser("~")`` 会扫错目录导致 0 会话。
    """
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", wintypes.BYTE * 8),
            ]

        # FOLDERID_Profile = {5E6C858F-0E22-4760-9AFE-EA3317B67173}
        folder_id = _GUID(
            0x5E6C858F,
            0x0E22,
            0x4760,
            (wintypes.BYTE * 8)(0x9A, 0xFE, 0xEA, 0x33, 0x17, 0xB6, 0x71, 0x73),
        )
        ptr = ctypes.c_wchar_p()
        hr = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(folder_id), 0, None, ctypes.byref(ptr)
        )
        if hr == 0 and ptr.value:
            path = ptr.value
            ctypes.windll.ole32.CoTaskMemFree(ptr)
            return path
    except Exception:
        return None
    return None


def _candidate_home_dirs(home: Optional[str]) -> list[str]:
    """按优先级返回去重后的 home 目录候选列表。"""
    candidates: list[str] = []
    for value in (
        home,
        _windows_known_folder_home(),
        os.environ.get("USERPROFILE"),
        os.path.expanduser("~"),
    ):
        if not value:
            continue
        normalized = os.path.normpath(value)
        if all(
            os.path.normcase(existing) != os.path.normcase(normalized)
            for existing in candidates
        ):
            candidates.append(normalized)
    return candidates


def candidate_project_roots(home: Optional[str] = None) -> list[str]:
    """按优先级返回会话存档目录候选列表。

    - 显式传入的 ``home``（测试隔离用）→ 只扫该目录
    - ``CLAUDE_CONFIG_DIR`` 环境变量（Claude Code 官方支持的配置目录）
    - 各 home 候选的 ``<home>/.claude/projects``
      （home 解析等价 cc-switch：Windows 优先 Known Folder API，
      避免启动器篡改环境变量导致扫错目录）
    """
    if home:
        return [os.path.join(home, ".claude", "projects")]

    roots: list[str] = []
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    if config_dir:
        roots.append(os.path.join(os.path.expanduser(config_dir), "projects"))
    for home_dir in _candidate_home_dirs(None):
        roots.append(os.path.join(home_dir, ".claude", "projects"))
    if not roots:
        roots.append(os.path.join(os.path.expanduser("~"), ".claude", "projects"))
    return roots


def claude_projects_root(home: Optional[str] = None) -> str:
    """返回 Claude 会话存档根目录 ``~/.claude/projects``。

    在多个候选中取第一个存在的目录；进程环境变量异常时
    （如 Steam 启动器）由 Windows Known Folder API 兜底。
    """
    roots = candidate_project_roots(home)
    for root in roots:
        if os.path.isdir(root):
            return root
    return roots[0]


def _collect_all_jsonl_files(home: Optional[str] = None) -> list[str]:
    """跨所有候选存档目录收集 .jsonl 文件（按规范化路径去重）。"""
    files: list[str] = []
    seen: set[str] = set()
    for root in candidate_project_roots(home):
        if not os.path.isdir(root):
            continue
        batch: list[str] = []
        _collect_jsonl_files(root, batch)
        for path in batch:
            key = os.path.normcase(os.path.abspath(path))
            if key not in seen:
                seen.add(key)
                files.append(path)
    return files


def _truncate(text: str, max_chars: int) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."


def _parse_timestamp_ms(value: Any) -> Optional[int]:
    """解析 jsonl 中的 timestamp 字段为毫秒（兼容 ISO 字符串和数字）。"""
    if isinstance(value, (int, float)):
        ts = float(value)
        return int(ts * 1000) if ts < 1e12 else int(ts)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            # Python 3.11+ 支持 Z 后缀；旧版本手动替换
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return int(dt.timestamp() * 1000)
        except (ValueError, TypeError):
            return None
    return None


def _collect_jsonl_files(root: str, files: list[str]) -> None:
    """递归收集目录下所有 .jsonl 文件。"""
    if not os.path.isdir(root):
        return
    try:
        entries = os.listdir(root)
    except OSError:
        return
    for name in entries:
        path = os.path.join(root, name)
        if os.path.isdir(path):
            _collect_jsonl_files(path, files)
        elif name.endswith(".jsonl"):
            files.append(path)


def _read_head_tail_lines(
    path: str, head: int = HEAD_LINES, tail: int = TAIL_LINES
) -> Optional[tuple[list[str], list[str]]]:
    """读取文件头部和尾部若干行（避免整文件加载）。"""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size <= 1024 * 1024:
                # 小文件（<1MB）直接全读，简单可靠
                data = f.read()
                lines = data.decode("utf-8", errors="replace").splitlines()
                return lines[:head], lines[-tail:] if tail else []
            # 大文件：头部直接读
            head_lines: list[str] = []
            for _ in range(head):
                line = f.readline()
                if not line:
                    break
                head_lines.append(line.decode("utf-8", errors="replace").rstrip("\r\n"))
            # 尾部：从文件末尾回读一个块
            chunk = min(size, 256 * 1024)
            f.seek(size - chunk)
            tail_data = f.read().decode("utf-8", errors="replace")
            tail_lines = [ln for ln in tail_data.splitlines() if ln.strip()]
            return head_lines, tail_lines[-tail:]
    except OSError:
        return None


def _is_agent_session(path: str) -> bool:
    """cc-switch 规则：agent- 前缀文件是子代理会话，不展示。"""
    return os.path.basename(path).startswith("agent-")


# ---------------------------------------------------------------------------
# 消息内容提取（对齐 cc-switch utils.extract_text）
# ---------------------------------------------------------------------------


def _extract_text(content: Any) -> str:
    """从 message.content 提取可读文本。

    content 可能是字符串，也可能是 content block 数组：
    - text 块 → 直接取 text
    - tool_use 块 → "[Tool: <name>]" 标记
    - tool_result 块 → 递归提取其 content
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            kind = item.get("type")
            if kind == "text":
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text)
            elif kind == "tool_use":
                name = item.get("name") or "tool"
                parts.append(f"[Tool: {name}]")
            elif kind == "tool_result":
                inner = _extract_text(item.get("content"))
                if inner.strip():
                    parts.append(inner)
        return "\n".join(parts)
    return str(content)


# ---------------------------------------------------------------------------
# 会话元数据解析
# ---------------------------------------------------------------------------


def _parse_session(path: str) -> Optional[dict[str, Any]]:
    """解析单个 jsonl 文件的会话元数据。返回 None 表示无法解析。"""
    if _is_agent_session(path):
        return None

    lines = _read_head_tail_lines(path)
    if lines is None:
        return None
    head, tail = lines

    session_id = ""
    project_dir = ""
    created_at: Optional[int] = None
    first_user_message = ""

    # 头部行：提取 sessionId / cwd / timestamp / 首条真实用户消息
    for line in head:
        try:
            value = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(value, dict):
            continue
        if not session_id and isinstance(value.get("sessionId"), str):
            session_id = value["sessionId"]
        if not project_dir and isinstance(value.get("cwd"), str):
            project_dir = value["cwd"]
        if created_at is None:
            created_at = _parse_timestamp_ms(value.get("timestamp"))
        if not first_user_message:
            message = value.get("message")
            is_user = value.get("type") == "user" or (
                isinstance(message, dict) and message.get("role") == "user"
            )
            if is_user and isinstance(message, dict):
                text = _extract_text(message.get("content")).strip()
                # 跳过系统注入的 caveat 和 slash command
                if (
                    text
                    and "<local-command-caveat>" not in text
                    and not text.startswith("<command-name>")
                ):
                    first_user_message = text

    # 尾部行（倒序）：last_active_at / summary / custom-title
    last_active_at: Optional[int] = None
    summary = ""
    custom_title = ""

    for line in reversed(tail):
        try:
            value = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(value, dict):
            continue
        if last_active_at is None:
            last_active_at = _parse_timestamp_ms(value.get("timestamp"))
        if not custom_title and value.get("type") == "custom-title":
            title = value.get("customTitle")
            if isinstance(title, str) and title.strip():
                custom_title = title.strip()
        if not summary:
            if value.get("isMeta") is True:
                continue
            message = value.get("message")
            if isinstance(message, dict):
                text = _extract_text(message.get("content"))
                if text.strip():
                    summary = text

    # session_id 回退：文件名 stem
    if not session_id:
        session_id = os.path.splitext(os.path.basename(path))[0]
    if not session_id:
        return None

    # 标题优先级：custom-title > 首条用户消息 > 项目目录名
    if custom_title:
        title = _truncate(custom_title, TITLE_MAX_CHARS)
    elif first_user_message:
        title = _truncate(first_user_message, TITLE_MAX_CHARS)
    else:
        title = os.path.basename(project_dir) if project_dir else session_id

    return {
        "session_id": session_id,
        "title": title,
        "summary": _truncate(summary, SUMMARY_MAX_CHARS),
        "project_dir": project_dir,
        "created_at": created_at,
        "last_active_at": last_active_at,
        "source_path": path,
        "resume_command": f"claude --resume {session_id}",
    }


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------


def scan_sessions(
    home: Optional[str] = None, limit: int = DEFAULT_SCAN_LIMIT
) -> list[dict[str, Any]]:
    """扫描所有 Claude 会话，按最近活跃时间降序返回。"""
    sessions: list[dict[str, Any]] = []
    for path in _collect_all_jsonl_files(home):
        meta = _parse_session(path)
        if meta is not None:
            sessions.append(meta)

    sessions.sort(key=lambda s: s.get("last_active_at") or 0, reverse=True)
    if limit > 0:
        sessions = sessions[:limit]
    return sessions


def normalize_session_ref(ref: str) -> str:
    """规范化猫娘传来的会话引用为裸 UUID（小写、去修饰）。

    兼容以下形态：
    - 裸 UUID：``d7d17ec1-bd47-49f0-a845-dfa5df8c33a6``
    - 命令形态：``claude --resume d7d17ec1-...``（面板复制出的完整命令）
    - UUID 前缀：``d7d17ec1``（截断形态，由 find_session_by_id 做前缀匹配）
    - 带引号/空白包裹
    """
    text = (ref or "").strip().strip("'\"")
    # 取最后一个空白分隔的 token（兼容 "claude --resume <uuid>"）
    tokens = [t for t in text.split() if t]
    if tokens:
        candidate = tokens[-1].strip("'\"")
        # 仅当 token 看起来像 UUID/前缀时才采用，否则保留原文
        if re.fullmatch(r"[0-9a-fA-F-]{4,}", candidate):
            text = candidate
    return text.lower()


def find_session_by_id(
    session_id: str,
    *,
    home: Optional[str] = None,
    limit: int = DEFAULT_SCAN_LIMIT,
) -> Optional[dict[str, Any]]:
    """按 UUID（或唯一前缀）在 ``~/.claude/projects`` 中定位历史会话。

    优先精确匹配 jsonl 文件名（``<uuid>.jsonl``），失败时回退到
    唯一前缀匹配（多个匹配返回 None，避免歧义）。
    返回结构与 ``scan_sessions`` 的单条会话一致（含 project_dir）。
    """
    sid = normalize_session_ref(session_id)
    if not sid:
        return None
    files = _collect_all_jsonl_files(home)

    # 精确匹配：文件名 stem == uuid
    for path in files:
        if _is_agent_session(path):
            continue
        if os.path.splitext(os.path.basename(path))[0].lower() == sid:
            return _parse_session(path)

    # 前缀匹配：要求唯一
    if len(sid) >= 8:
        matches = [
            p
            for p in files
            if not _is_agent_session(p)
            and os.path.splitext(os.path.basename(p))[0].lower().startswith(sid)
        ]
        if len(matches) == 1:
            return _parse_session(matches[0])

    if limit > 0:
        # 兜底：解析文件内容里的 sessionId（文件名与内容不一致时）
        for meta in scan_sessions(home=home, limit=limit):
            if meta["session_id"].lower() == sid:
                return meta
    return None


def load_messages(
    source_path: str,
    *,
    expected_session_id: str = "",
    max_messages: int = 400,
) -> list[dict[str, Any]]:
    """读取会话消息列表。

    Parameters
    ----------
    source_path:
        会话 jsonl 文件路径（来自 scan_sessions 的 source_path）。
    expected_session_id:
        若提供，会校验文件内 sessionId 一致（防串读）。
    max_messages:
        最多返回的消息条数（取最后 N 条，防止超长对话打爆前端）。
    """
    messages: list[dict[str, Any]] = []
    seen_session_id = ""

    try:
        with open(source_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(value, dict):
                    continue

                if not seen_session_id and isinstance(value.get("sessionId"), str):
                    seen_session_id = value["sessionId"]

                if value.get("isMeta") is True:
                    continue
                message = value.get("message")
                if not isinstance(message, dict):
                    continue

                role = message.get("role") or "unknown"
                content_raw = message.get("content")

                # user 消息若全部是 tool_result → 重分类为 tool（对齐 cc-switch）
                if role == "user" and isinstance(content_raw, list) and content_raw:
                    if all(
                        isinstance(item, dict) and item.get("type") == "tool_result"
                        for item in content_raw
                    ):
                        role = "tool"

                content = _extract_text(content_raw)
                if not content.strip():
                    continue

                messages.append(
                    {
                        "role": role,
                        "content": _truncate(content, CONTENT_MAX_CHARS),
                        "ts": _parse_timestamp_ms(value.get("timestamp")),
                    }
                )
    except OSError:
        return []

    if (
        expected_session_id
        and seen_session_id
        and seen_session_id != expected_session_id
    ):
        return []

    if max_messages > 0 and len(messages) > max_messages:
        messages = messages[-max_messages:]
    return messages


def delete_session(source_path: str, session_id: str) -> bool:
    """删除会话文件及同名 sidecar 目录（对齐 cc-switch 行为）。

    安全校验：先解析文件确认 session_id 一致才允许删除。
    """
    meta = _parse_session(source_path)
    if meta is None:
        return False
    if meta["session_id"] != session_id:
        return False

    # 同名 sidecar 目录（存放 subagents / tool-results）
    stem = os.path.splitext(source_path)[0]
    if os.path.isdir(stem):
        shutil.rmtree(stem, ignore_errors=True)

    try:
        os.remove(source_path)
    except OSError:
        return False
    return True


__all__ = [
    "claude_projects_root",
    "candidate_project_roots",
    "scan_sessions",
    "normalize_session_ref",
    "find_session_by_id",
    "load_messages",
    "delete_session",
]
