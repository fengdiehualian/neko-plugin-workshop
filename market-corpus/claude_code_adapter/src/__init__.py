"""Claude Code Adapter Plugin

通过 @llm_tool 将 Claude Code CLI 注册为猫娘可调用的工具集。

猫娘可以通过以下工具调用 Claude Code 开发项目：
- claude_code_execute: 执行 Claude Code 任务（写代码、改 bug、跑测试等）
- claude_code_check_health: 检查 Claude CLI 是否可用
- claude_code_list_sessions: 列出所有会话
- claude_code_clear_session: 清除会话记录
- claude_code_get_config: 获取当前适配器配置

设计参考：
- Paperclip `claude-local` 适配器的执行流程
- N.E.K.O `neko_claudian` 插件的跨平台 spawn 工具
- N.E.K.O `mcp_adapter` 插件的 Plugin 范式
- `outputs/02-Python实现Claude-Code适配器完整方案.md` 的数据结构设计

注意：插件主类位于 `_plugin_main.py`（模块顶层定义），
以满足 neko-plugin release check 对 entry 类的 AST 静态校验。
"""

import sys

# ---------------------------------------------------------------------------
# 测试环境保护：pytest 会导入根目录 __init__.py 作为包初始化文件，
# 但测试环境没有 N.E.K.O SDK。检测到测试环境时跳过 SDK 相关导入。
# ---------------------------------------------------------------------------
_IN_TEST = "pytest" in sys.modules

if not _IN_TEST:
    from ._plugin_main import ClaudeCodeAdapterPlugin

    __all__ = ["ClaudeCodeAdapterPlugin"]
else:
    __all__ = []
