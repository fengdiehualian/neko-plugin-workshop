"""最小 N.E.K.O SDK 桩：只为了让插件类能被导入并在本机跑一遍。

它**不是** N.E.K.O 的替代品：真机行为（权限、UI 渲染、推送、10 秒就绪窗口）必须在宿主里验证。
但装饰器签名与真 SDK 保持一致（`plugin/sdk/plugin/{decorators,llm_tool,ui}.py@main`），
这样写错参数名会在本地就炸，而不是等到市场 CI 才发现。
"""

import logging

_TOOL_NAME_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")


class SdkError(Exception):
    def __init__(self, message="", *, code=None, details=None):
        super().__init__(message)
        self.code = code
        self.details = details


class Ok:
    """与真 SDK `plugin.sdk.shared.models.result.Ok` 同形：`.value` + `.is_ok()`。"""

    __slots__ = ("value",)

    def __init__(self, value=None):
        self.value = value

    def is_ok(self):
        return True

    def is_err(self):
        return False

    def unwrap(self):
        return self.value

    def __repr__(self):
        return f"Ok({self.value!r})"


class Err:
    """与真 SDK `Err` 同形：`.error` + `.is_err()`；构造函数接受异常或任意值。"""

    __slots__ = ("error",)

    def __init__(self, error=None):
        self.error = error

    def is_ok(self):
        return False

    def is_err(self):
        return True

    def unwrap(self):
        if isinstance(self.error, BaseException):
            raise self.error
        raise SdkError(str(self.error))

    def __repr__(self):
        return f"Err({self.error!r})"


def neko_plugin(cls):
    return cls


def plugin_entry(
    *,
    id=None,
    name=None,
    description="",
    input_schema=None,
    params=None,
    kind="action",
    auto_start=False,
    persist=None,
    model_validate=True,
    timeout=None,
    llm_result_fields=None,
    llm_result_model=None,
    fields=None,
    metadata=None,
    quick_action=False,
):
    if not isinstance(id, str) or not id:
        raise TypeError("plugin_entry() 需要非空 id")

    def wrap(func):
        return func

    return wrap


def lifecycle(*, id, name=None, description="", metadata=None):
    allowed = {"startup", "shutdown", "reload", "freeze", "unfreeze", "config_change"}
    if id not in allowed:
        raise TypeError(f"lifecycle() id 必须是 {sorted(allowed)} 之一，收到 {id!r}")

    def wrap(func):
        return func

    return wrap


def llm_tool(*, name=None, description="", parameters=None, timeout=30.0, role=None, reply_tail=False):
    resolved = name if isinstance(name, str) and name else None
    if parameters is not None and not isinstance(parameters, dict):
        raise TypeError(f"@llm_tool parameters 必须是 dict，收到 {type(parameters).__name__}")
    if float(timeout) <= 0:
        raise ValueError(f"@llm_tool timeout 必须 > 0，收到 {timeout}")
    if resolved is not None:
        bad = set(resolved) - _TOOL_NAME_OK
        if bad or not (1 <= len(resolved) <= 64):
            raise TypeError(f"@llm_tool name 不合法：{resolved!r}")

    def wrap(func):
        func.__llm_tool_meta__ = {"name": resolved or func.__name__, "description": description,
                                  "parameters": parameters or {"type": "object", "properties": {}},
                                  "timeout": float(timeout)}
        return func

    return wrap


class _Ui:
    @staticmethod
    def action(*, id=None, label=None, icon=None, tone="default", group=None, order=0,
               confirm=False, refresh_context=True):
        def wrap(func):
            return func

        return wrap

    @staticmethod
    def context(*, id="main", title=None):
        def wrap(func):
            return func

        return wrap


ui = _Ui()


class NekoPluginBase:
    def __init__(self, ctx):
        self.ctx = ctx
        self.config = ctx.config
        self.logger = logging.getLogger("stub.plugin")
        self.last_status = None

    def report_status(self, status):
        self.last_status = status

    def data_path(self, *parts):
        import os
        base = os.path.join(os.path.expanduser("~"), "N.E.K.O", "stub")
        path = os.path.join(base, *[str(p) for p in parts])
        os.makedirs(path if not parts or parts[-1] == "" else os.path.dirname(path), exist_ok=True)
        return path

    def cache_path(self, *parts):
        return self.data_path(*parts)

    def push_message(self, **_):
        return None
