"""code_sandbox — 安全代码沙箱（Python）。

给 YUI 一个受限的 Python 执行环境：适合数学计算、数据处理、
算法验证、逻辑推理辅助。系统/网络/文件写操作一律拦截。
"""

from __future__ import annotations

from plugin.sdk.plugin import (
    NekoPluginBase,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
)

from .sandbox import run_code as _run_code


@neko_plugin
class CodeSandboxPlugin(NekoPluginBase):
    """N.E.K.O. 插件：安全 Python 代码沙箱。"""

    def __init__(self, ctx):
        super().__init__(ctx)

    # ══════════ 生命周期 ══════════

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        return {"status": "ready", "plugin": "code_sandbox"}

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        return {"status": "shutdown"}

    # ══════════ 工具：安全执行代码 ══════════

    @llm_tool(
        name="run_code",
        description=(
            "在安全沙箱中执行 Python 代码。何时用：用户要求'算一下/计算/跑一下这段代码'、"
            "'处理这段数据/验证算法/模拟一下'等需要运行代码的请求时。"
            "参数 code：要执行的 Python 代码（纯计算逻辑，如数学计算、数据处理、算法验证）；"
            "timeout：可选，超时秒数（1-60，默认 10），超时自动强制终止。"
            "限制：沙箱禁止系统命令、网络访问、文件读写等危险操作，违规会被安全拦截并说明原因。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "要执行的 Python 代码"},
                "timeout": {"type": "number", "description": "可选：超时秒数，默认 10"},
            },
            "required": ["code"],
        },
        timeout=70.0,
    )
    async def run_code(self, *, code: str = "", timeout: float = 10.0, **_):
        params = _.get("parameters") or {}
        code = (code or params.get("code") or "").strip()
        timeout = timeout or params.get("timeout") or 10.0
        try:
            timeout = float(timeout)
        except (TypeError, ValueError):
            timeout = 10.0
        return _run_code(code, timeout=timeout)

    # ══════════ 自动路由入口（plugin_entry：TaskExecutor 评估可见） ══════════
    # v0.1.0 只注册了 llm_tool、无 plugin_entry，导致市场面板"入口点: 0"、
    # 评估侧永远无法路由到本插件（代码执行功能实际不可用）。
    # v0.1.1 注册 run 通用入口修复（方法名不能以下划线开头，bili_viewer v0.6.22 教训）。

    @plugin_entry(
        id="run",
        name="代码沙箱",
        description=(
            "代码沙箱（code_sandbox）：在受限安全沙箱中执行 Python 代码。"
            "用户要求'算一下/计算/跑一下这段代码''处理这段数据/验证算法/模拟一下'等需要运行代码的请求时使用。"
            "参数 code 必填（要执行的 Python 代码，纯计算逻辑：数学计算、数据处理、算法验证、逻辑推理）；"
            "timeout 可选（超时秒数 1-60，默认 10，超时自动强制终止）。"
            "限制：沙箱禁止系统命令、网络访问、文件读写等危险操作，违规会被安全拦截并说明原因；"
            "结果以文本输出返回（stdout/stderr 各自截断到上限，防止刷屏）。"
            "回复纪律：直接给出运行结果或拦截原因，不加多余话术，不编造输出。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "要执行的 Python 代码，必填"},
                "timeout": {"type": "number", "description": "可选：超时秒数，默认 10"},
            },
            "required": ["code"],
        },
        timeout=70.0,
    )
    async def entry_run(self, **kwargs):
        params = kwargs.get("parameters") or {}
        code = str(kwargs.get("code") or params.get("code") or "").strip()
        timeout = kwargs.get("timeout") or params.get("timeout") or 10.0
        return await self.run_code(code=code, timeout=timeout, parameters={})
