"""bridge — 把本机 Agent CLI 派发能力封装成可复用的子包。

这个子包是插件 `agent_bridge` 的实现层；它**不再**是 MCP 服务端，也没有
`server` 模块（那个名字是插件化之前的遗留，历史上指向一个零依赖的 MCP stdio
JSON-RPC 服务端，早已不存在）。对外入口在插件根目录的 `__init__.py`，由宿主的
`@plugin_entry` / `@llm_tool` 承担。

模块布局：
    registry   Agent 登记表 + Windows shim 解析（可执行文件定位）
    runner     子进程派发（超时、进程树清理、输出限幅）
    tools      结果排版（format_agent_result）与审计日志（write_audit）
    config     可选的外部配置覆盖
"""

from __future__ import annotations

__version__ = "1.0.0"
__all__ = ["__version__"]
