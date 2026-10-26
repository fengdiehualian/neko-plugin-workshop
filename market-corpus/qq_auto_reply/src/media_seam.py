# -*- coding: utf-8 -*-
"""QQ 开放平台富媒体的唯一入口：**优先宿主适配器**，没有才回退内置副本。

三种形态（按优先级）：

1. **宿主适配器**（现在期望的形态）：宿主的 ``QQOpenPlatformConnection`` 混入
   ``QQOpenPlatformMediaMixin`` 之后，连接对象自带 ``upload_image`` /
   ``send_private_image``；群图那条走连接对象自己的 ``send_group_image``
   （宿主那份内部就是"先上传再 msg_type=7"）。上游把富媒体合进本体并发出新包之后，
   插件这边就只跑这一条。
2. **内置副本**（回退）：``_vendor/connection_onebot/qq_open_platform_media.py`` ——
   插件自撰、以连接对象为第一参数的自由函数。宿主还没有那个 mixin 时用它。
3. 两者都没有：不该发生，调用会 AttributeError（而不是静默不发图）。

**判据是能力，不是类名、不是宿主版本号**：``upload_image`` 只有开放平台的媒体 mixin 提供；
``send_private_image`` **不能**当判据 —— OneBot 侧也有同名方法，签名还不一样（2 参、
没有 ``record_sent``）。宿主哪天换名字、换实现、把这套挪到别的模块，本模块都不用改。

为什么把这件事从 ``connector_seam`` 挪出来：``connector_seam`` 回答的是"连接器从哪来"
（宿主包 vs 副本包，判据是 9 个必需属性），富媒体是**连接对象的能力**，与连接器来源无关 ——
宿主连接器也可能还没混入媒体 mixin。两者混在一起，会让"宿主连接器 + 还没媒体"这种组合
无法表达。

**退役（上游打出带这个 mixin 的新包、插件声明的最低支持版本到位之后）**：
删掉 ``VENDORED_MODULE`` 与 ``_vendored()`` 以及三个函数里的回退分支，本模块就只剩宿主那一半；
``_vendor/connection_onebot/`` 整个目录也可以一起删 —— 富媒体这条路对副本的唯一引用就在这里
（``tests/test_qq_media_seam.py`` 有一条守卫盯着"只有本模块引用副本"）。
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any, Optional

#: 插件内置副本（宿主还没带媒体 mixin 时的回退）。退役时删这一行 + ``_vendored()``。
VENDORED_MODULE = f"{__package__}._vendor.connection_onebot.qq_open_platform_media"

#: 宿主适配器的能力指纹：**只有**开放平台的媒体 mixin 会提供这个方法。
HOST_CAPABILITY = "upload_image"

#: 宿主那两个方法所在的位置（文档/排查用；本模块不 import 它）。
HOST_IMPLEMENTATION = "utils.connection.qq.open_platform_media.QQOpenPlatformMediaMixin"


def is_open_platform(conn: Any) -> bool:
    """这个连接对象是不是 QQ 开放平台。

    读 ``CHANNEL``（``"open"``，与 ``OneBotClient.CHANNEL`` 同一取值域），再退回 ``mode``。
    两个属性都已经存在，**不新增协议成员**。这段判据原本在副本的媒体模块里；富媒体不再
    依赖副本，判据也就跟着搬进插件自己——它认的是连接对象的形状，不是谁实现的。
    """
    if str(getattr(conn, "CHANNEL", "") or "").strip() == "open":
        return True
    return str(getattr(conn, "mode", "") or "").strip() == "open_platform"


def host_adapter_available(conn: Any) -> bool:
    """连接对象自带宿主适配器吗（能自己上传/发图）。"""
    return callable(getattr(conn, HOST_CAPABILITY, None))


def source_of(conn: Any) -> str:
    """``"host"`` / ``"vendored"`` / ``"n/a"``（非开放平台通道）。"""
    if not is_open_platform(conn):
        return "n/a"
    return "host" if host_adapter_available(conn) else "vendored"


def describe(conn: Any) -> str:
    """给人看的一行：这条路现在由谁提供。启动时打进日志，排查"到底跑哪份"用。"""
    source = source_of(conn)
    if source == "host":
        return f"宿主适配器（连接对象自带 {HOST_CAPABILITY}）"
    if source == "vendored":
        return f"内置副本（{VENDORED_MODULE}）—— 宿主还没带媒体 mixin"
    return "不适用（非开放平台通道自己发图）"


def _vendored() -> ModuleType:
    """惰性导入内置副本：**宿主适配器可用时根本不会导入它**（退役时可以整段删）。"""
    return importlib.import_module(VENDORED_MODULE)


async def upload_image(conn: Any, *, scope: str, owner_id: str, source: str) -> str:
    """把一张图上传到 ``scope``（``"groups"`` / ``"users"``），返回 ``file_info`` 或空串。"""
    if host_adapter_available(conn):
        return await conn.upload_image(scope=scope, owner_id=owner_id, source=source)
    return await _vendored().upload_image(conn, scope=scope, owner_id=owner_id, source=source)


async def send_group_image(
    conn: Any, group_id: str, source: str, *,
    content: str = "", reply_message_id: str = "", at_user_id: str = "",
) -> Optional[str]:
    """群聊发一张图，返回消息 id 或 ``None``。

    **不收 ``record_sent``**：两边的群图都不记账（宿主那份内部固定 ``record_sent=False``，
    副本那份的群图调用点一直是 ``False``），写成参数只会让人以为可以打开。
    """
    if host_adapter_available(conn):
        if str(content or "").strip():
            # 宿主的 send_group_image 只发图、没有文字参数；有文字时改走段发送 ——
            # 图仍然由它先上传再 msg_type=7（宿主那份就是这么实现的），只是能带上文字。
            return await _host_group_image_with_content(
                conn, group_id, source, content=content,
                reply_message_id=reply_message_id, at_user_id=at_user_id,
            )
        return await conn.send_group_image(
            group_id, source, reply_message_id=reply_message_id, at_user_id=at_user_id,
        )
    return await _vendored().send_group_image(
        conn, group_id, source,
        content=content, reply_message_id=reply_message_id, at_user_id=at_user_id,
    )


async def _host_group_image_with_content(
    conn: Any, group_id: str, source: str, *,
    content: str, reply_message_id: str, at_user_id: str,
) -> Optional[str]:
    """宿主没有"群图带文字"的入口时，用段发送把文字/引用/@ 和图一起交给它。"""
    segments: list[dict[str, Any]] = []
    if str(reply_message_id or "").strip():
        segments.append({"type": "reply", "data": {"id": str(reply_message_id)}})
    if str(at_user_id or "").strip():
        segments.append({"type": "at", "data": {"qq": str(at_user_id)}})
    if str(content or "").strip():
        segments.append({"type": "text", "data": {"text": str(content)}})
    segments.append({"type": "image", "data": {"file": source}})
    return await conn.send_group_message_segments(group_id, segments, record_sent=False)


async def send_private_image(
    conn: Any, user_id: str, source: str, *,
    content: str = "", reply_message_id: str = "", record_sent: bool = True,
) -> Optional[str]:
    """单聊发一张图，返回消息 id 或 ``None``。``record_sent=False`` 用于表情包。

    宿主那份的签名与副本那份逐字一致（``content`` / ``reply_message_id`` / ``record_sent``），
    所以这条可以原样透传。
    """
    if host_adapter_available(conn):
        return await conn.send_private_image(
            user_id, source,
            content=content, reply_message_id=reply_message_id, record_sent=record_sent,
        )
    return await _vendored().send_private_image(
        conn, user_id, source,
        content=content, reply_message_id=reply_message_id, record_sent=record_sent,
    )
