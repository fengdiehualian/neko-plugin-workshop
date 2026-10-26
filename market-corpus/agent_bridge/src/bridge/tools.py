"""结果排版与审计日志。

这个模块**不再是** MCP 工具定义层。历史上它同时带着一套 `TOOL_DEFINITIONS` /
`HANDLERS` / `handle_*`，那是 `neko_agent_bridge` MCP 服务端的产物；插件化之后
调用入口全部由宿主上的 `@plugin_entry` / `@llm_tool` 承担，那一整套没有任何调用方，
是纯粹的重复实现（而且和 `__init__.py` 里的入口定义已经开始漂移）。
只保留仍然被真正引用的两个函数：

* :func:`format_agent_result` —— 把 :class:`AgentResult` 排版成给模型看的文本；
* :func:`write_audit` —— 把一条派发记录追加进 jsonl 审计日志。

文本排版的硬约束：宿主会把结果拼成 `summary` 再按 token 上限截断，所以**最有价值
的信息必须放在最前面**，尾部允许被截掉。
"""

from __future__ import annotations

import json
import os
from typing import Any

from .config import BridgeConfig
from .runner import AgentResult

JSONType = dict[str, Any]

_STDERR_TAIL_CHARS = 1500


def _fence(text: str) -> str:
    return text.rstrip() or "(空)"


def format_agent_result(result: AgentResult) -> str:
    if result.needs_login:
        status = "NEEDS_LOGIN"
    elif result.ok:
        status = "OK"
    else:
        status = "TIMEOUT" if result.timed_out else "FAILED"
    lines = [
        f"[agent_run] {result.agent} · {status} · exit={result.exit_code} · {result.duration_ms / 1000:.1f}s",
    ]
    if result.needs_login:
        # 别把那句登录提示留在 body 里让模型自己品：直接点名「换个已登录的 Agent」。
        lines.append("note: 该 CLI 未登录，任务根本没有执行；换一个 ready 的 Agent 重试。")
    cwd = result.extra.get("cwd")
    if cwd:
        lines.append(f"cwd: {cwd}")
    lines.append(f"cmd: {result.command_preview}")
    if result.error:
        lines.append(f"note: {result.error}")
    if result.output_truncated:
        lines.append("note: 输出超过限幅，已截断")

    body = result.output_text()
    lines.append("---")
    lines.append(_fence(body) if body else "(无输出)")

    stderr = result.stderr.strip()
    if stderr and stderr != body:
        lines.append("--- stderr (tail) ---")
        lines.append(stderr[-_STDERR_TAIL_CHARS:])
    return "\n".join(lines)


def write_audit(config: BridgeConfig, record: JSONType) -> None:
    if not config.audit_log:
        return
    path = os.path.abspath(os.path.expanduser(config.audit_log))
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass
