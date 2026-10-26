"""猫娘计划(N.E.K.O.)插件:Agent 工作台安装器 —— 包根入口

布局说明(评审 B-2 与官方校验的平衡点):
- 全部实现体在 plugin/plugins/workbench_installer/(与 entry 包路径同形);
- 本文件保留官方 check --market-release 静态校验强制要求的入口声明 ——
  entry 类必须以 @neko_plugin 装饰、继承 NekoPluginBase、并定义 startup/shutdown,
  否则校验直接拒绝(实测原文:"plugin.entry class 'WorkbenchInstallerPlugin'
  must be decorated with @neko_plugin"等);此处只做声明与委托,无业务逻辑。

注意:本包内的 plugin/ 目录会遮蔽 SDK 的顶层 plugin 包(从插件根以 python -m
运行测试/工具时必现),加载实现前会临时把插件根移出 sys.path。
"""
import importlib.util as _ilu
import sys as _sys
from pathlib import Path as _P

_local_root = _P(__file__).resolve().parent
_impl_path = _local_root / "plugin" / "plugins" / "workbench_installer" / "__init__.py"
_spec = _ilu.spec_from_file_location("workbench_installer_impl", _impl_path)
if _spec is None or _spec.loader is None:  # pragma: no cover - 文件恒存在
    raise ImportError(f"无法加载实现模块: {_impl_path}")

_impl_mod = _ilu.module_from_spec(_spec)


def _load_impl():
    """防遮蔽加载实现:插件根暂不参与解析,SDK 的 plugin 包才有机会被命中。"""
    poisoned = _sys.modules.get("plugin")
    if poisoned is not None:
        p = getattr(poisoned, "__file__", None) or next(iter(getattr(poisoned, "__path__", [])), "")
        if p and _P(str(p)).resolve().is_relative_to(_local_root):
            del _sys.modules["plugin"]
    saved = _sys.path[:]
    _sys.path[:] = [
        e for e in _sys.path
        if _P(e or ".").resolve() != _local_root
    ]
    try:
        _spec.loader.exec_module(_impl_mod)
    finally:
        _sys.path[:] = saved


_load_impl()

# SDK 符号经实现模块转取(其导入过程已受防遮蔽保护),供下方官方入口声明使用
NekoPluginBase = _impl_mod.NekoPluginBase
neko_plugin = _impl_mod.neko_plugin
lifecycle = _impl_mod.lifecycle

InstallParams = _impl_mod.InstallParams
Ok = _impl_mod.Ok
Err = _impl_mod.Err
SdkError = _impl_mod.SdkError


@neko_plugin
class WorkbenchInstallerPlugin(_impl_mod.WorkbenchInstallerPlugin, NekoPluginBase):
    """入口类(官方静态校验要求声明在包根);实现继承自嵌套实现,无逻辑分支。"""

    @lifecycle(id="startup")
    async def startup(self, **kw):
        return await super().startup(**kw)

    @lifecycle(id="shutdown")
    async def shutdown(self, **kw):
        return await super().shutdown(**kw)


__all__ = ["WorkbenchInstallerPlugin", "InstallParams", "Ok", "Err", "SdkError"]
