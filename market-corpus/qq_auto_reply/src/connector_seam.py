"""QQ 传输连接器的唯一解析点：优先宿主第一方包，回退插件内置副本。

宿主优先（PR #2996 的 ``utils.connection.onebot``）；拿不到时用 ``_vendor`` 里的副本
（出处见 ``_vendor/connection_onebot/PROVENANCE.md``）。这样插件在"有 PR 的宿主"和
"还没有 PR 的宿主"上都能跑，且合并后自动切回宿主 —— 不需要再发一次版。

**只在需要连接对象时导入本模块**（``_make_qq_connection`` 里）：解析会连带拉进
``websockets`` / ``httpx``，顶层导入会拖慢插件进程启动握手。类型注解走
``TYPE_CHECKING``，运行时零成本。

TODO(#2996): PR 合并、且插件声明的最低支持宿主版本都带这个包之后，删掉 ``_vendor/``，
把本模块收敛成一行 re-export（或直接改回 ``from utils.connection.onebot import …``）。
"""

from __future__ import annotations

import importlib
from types import ModuleType

#: 宿主第一方包（PR #2996 落地于此）。
HOST_PACKAGE = "utils.connection.onebot"

#: 插件内置副本。
VENDORED_PACKAGE = f"{__package__}._vendor.connection_onebot"

#: 认定"宿主真的提供了连接器"所必需的属性 —— 5 个导出 + 4 个子模块。
_REQUIRED_ATTRS = (
    "OneBotClient",
    "OneBotConnectionBase",
    "OneBotConnector",
    "QQOpenPlatformConnection",
    "create_onebot_connection",
    "factory",
    "onebot_client",
    "onebot_connection",
    "qq_open_plat",
)


def _provides_connector(module: ModuleType) -> bool:
    """模块是否**真的**提供了连接器。

    不能只看 import 成没成功：切分支留下的 ``__pycache__`` 会让
    ``utils/connection/onebot/`` 这种"目录还在、``.py`` 源码没了"的路径变成**空命名空间包**
    —— 导入成功、属性一个没有。只看导入结果的话，这里会误判成宿主可用，
    然后在取属性时炸掉，回退根本没机会生效。
    """
    return all(hasattr(module, name) for name in _REQUIRED_ATTRS)


def _load(host_package: str = HOST_PACKAGE) -> tuple[ModuleType, str]:
    """返回 ``(连接器包模块, 来源)``，来源是 ``"host"`` 或 ``"vendored"``。

    ``host_package`` 可覆写，测试靠它注入一个假的宿主包来验证"宿主优先"这条分支。
    """
    try:
        module = importlib.import_module(host_package)
    except (ImportError, ModuleNotFoundError):
        pass
    else:
        if _provides_connector(module):
            return module, "host"
    return importlib.import_module(VENDORED_PACKAGE), "vendored"


_provider, CONNECTOR_SOURCE = _load()

#: 解析到的连接的模块全名。``CONNECTOR_SOURCE`` 只说"宿主还是副本"，这个说"具体是谁" ——
#: PR #2996 合并后用它确认拿到的是 ``utils.connection.onebot`` 而不是残留的副本。
CONNECTOR_MODULE = _provider.__name__

# 子模块对象一并透出：调用方与测试会摸到 onebot_client / qq_open_plat 这类更深的名字。
factory = _provider.factory
onebot_client = _provider.onebot_client
onebot_connection = _provider.onebot_connection
qq_open_plat = _provider.qq_open_plat

OneBotClient = _provider.OneBotClient
OneBotConnectionBase = _provider.OneBotConnectionBase
OneBotConnector = _provider.OneBotConnector
QQOpenPlatformConnection = _provider.QQOpenPlatformConnection
create_onebot_connection = _provider.create_onebot_connection


# 墓碑（2026-09-28）：本模块以前还负责解析富媒体模块
# （``{CONNECTOR_MODULE}.qq_open_platform_media``，拿不到就回退 ``_vendor``）。那时富媒体
# 只能靠"以连接对象为第一参数的自由函数"，所以它得跟着连接器来源走。
#
# 现在宿主的连接对象自己混入了媒体 mixin（``upload_image`` 等），富媒体变成**连接对象的能力**，
# 与"连接器来自宿主还是副本"无关 —— 判据换成能力探测，整件事搬到了 ``media_seam.py``
# （那里也是 ``_vendor`` 唯一的富媒体引用点，退役时删那一个模块就够）。

__all__ = [
    "CONNECTOR_MODULE",
    "CONNECTOR_SOURCE",
    "HOST_PACKAGE",
    "VENDORED_PACKAGE",
    "OneBotClient",
    "OneBotConnectionBase",
    "OneBotConnector",
    "QQOpenPlatformConnection",
    "create_onebot_connection",
    "factory",
    "onebot_client",
    "onebot_connection",
    "qq_open_plat",
]
