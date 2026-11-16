#!/usr/bin/env python3
"""插件内核与随包 CLI 共用的错误文本脱敏规则。

只处理要回传给模型或终端的错误文本，不用于改写仓库内容。新增规则必须在这里
统一维护，并由两个调用方的契约测试覆盖，避免 N.E.K.O 适配层再次漏项。
"""

from __future__ import annotations

import os
import re

_SECRET_RX = re.compile(
    r"(?i)(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"bearer\s+[A-Za-z0-9._~+/=-]{8,})"
)
# JSON/日志里的字段名可能带引号：{"access_token":"…"} 也必须命中；保留字段名，方便定位错误。
_FIELD_SECRET_RX = re.compile(
    r'(?i)(["\']?(?:access[_-]?token|api[_-]?key|authorization|token|password|secret)'
    r'["\']?\s*[:=]\s*)(?:"[^"]*"|\'[^\']*\'|\S+)'
)
_URL_CRED_RX = re.compile(r"(?i)(https?://)[^/\s:@]+(?::[^/\s@]*)?@")
_HEADER_RX = re.compile(r"(?i)((?:cookie|authorization)\s*[:=]\s*)[^\r\n]+")


def _home_variants(home: str) -> list[str]:
    """Windows 路径大小写不敏感，且可能用 / 或 \\，甚至 JSON 里的双反斜杠。"""
    variants = {home, home.replace("\\", "/"), home.replace("/", "\\")}
    if "\\" in home:
        variants.add(home.replace("\\", "\\\\"))
    return sorted((v for v in variants if v), key=len, reverse=True)


def _redact_home(text: str, home: str) -> str:
    # 根目录与极短路径会命中大量无关文本；C:、C:\、C:/ 等盘符形式也必须跳过。
    if (not home or home in {"/", "\\"} or len(home.strip("/\\")) <= 1
            or re.fullmatch(r"[A-Za-z]:[\\/]*", home)):
        return text
    flags = re.IGNORECASE if os.name == "nt" else 0
    pattern = re.compile(
        "|".join(re.escape(variant) for variant in _home_variants(home)),
        flags,
    )
    return pattern.sub("~", text)


def redact_text(value: object, limit: int = 300) -> str:
    """隐藏常见凭据与 home 路径，再按字符数截断。

    URL userinfo 与 Cookie / Authorization 头必须先于通用 token 规则处理；
    字段名（含 JSON 引号形式）保留，只替换值，方便定位错误类型。
    """
    text = str(value or "")
    home = os.path.expanduser("~")
    text = _redact_home(text, home)
    text = _URL_CRED_RX.sub(r"\1[redacted]@", text)
    text = _HEADER_RX.sub(r"\1[redacted]", text)
    text = _FIELD_SECRET_RX.sub(r"\1[redacted]", text)
    text = _SECRET_RX.sub("[redacted]", text)
    return text[:limit]
