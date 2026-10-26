"""MONETA FX — fictional forex paper-trading room.

N.E.K.O. 插件的加载入口。``plugin.toml`` 的 ``[plugin].entry`` 指向
``plugin.plugins.moneta_fx.plugin:MonetaFxPlugin``（子模块，便于静态校验），
本模块仍以 ``__getattr__`` 惰性转发同一个类。

运行时通过 ``__getattr__`` 惰性导入：裸 ``import moneta_fx`` 不会拉入
N.E.K.O. SDK，因此测试和云端服务端可以直接复用同级的 ``market`` /
``engine`` / ``store`` / ``auth`` / ``world`` 模块。

类型检查期（以及 ``neko-plugin check`` 的静态扫描）走 ``TYPE_CHECKING``
分支，那里有真正的类名可见。
"""

from typing import TYPE_CHECKING, Any

__all__ = ["MonetaFxPlugin"]

if TYPE_CHECKING:  # 仅供静态检查与类型提示，运行时不执行
    from .plugin import MonetaFxPlugin


def __getattr__(name: str) -> Any:
    if name == "MonetaFxPlugin":
        from .plugin import MonetaFxPlugin

        return MonetaFxPlugin
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
