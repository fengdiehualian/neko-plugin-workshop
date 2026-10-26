"""neko_terraria LLM 工具层：目标指令、动作调用、意图解析。

Mixin 类统一在 entries/__init__.py 聚合；intent_parser 由 coordinator 直接导入，
此处不在模块级自动导入以避免触发 core → autonomous → bridge 循环依赖。
"""

from .goal_tools import GoalToolsMixin

# intent_parser 不能模块级导入（会触发 core → autonomous → bridge 循环依赖），
# 通过 PEP 562 __getattr__ 懒加载，from .llm import LLMIntentParser 依然可用。

__all__ = [
    "GoalToolsMixin",
    "LLMIntentParser",
    "IntentResult",
]


def __getattr__(name):
    if name in ("LLMIntentParser", "IntentResult"):
        from .intent_parser import IntentResult as _IR
        from .intent_parser import LLMIntentParser as _LP
        return {"LLMIntentParser": _LP, "IntentResult": _IR}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
