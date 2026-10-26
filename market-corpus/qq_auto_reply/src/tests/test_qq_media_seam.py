# -*- coding: utf-8 -*-
"""富媒体走哪份：**能力优先**（宿主适配器），没有才回退内置副本。

背景（为什么要有这个文件）：开放平台的发图原本必须由插件自己实现 —— 宿主那份连接器
① 没有单聊富媒体方法、② 群聊图只实现已在真机失效的旧式直传（2026-09-26/27 现场日志）。
上游把富媒体合进本体、连接对象混入 `QQOpenPlatformMediaMixin` 之后，插件就该改用它。

判据是**能力**而不是类名/版本号：`upload_image` 只有那个 mixin 提供
（`send_private_image` 不行 —— OneBot 侧也有同名方法，签名还不一样）。于是：

* 连接对象有 `upload_image` → 调它的方法（宿主适配器）；
* 没有 → 用 `_vendor/connection_onebot/qq_open_platform_media.py`（内置副本）；
* 谁都不是 → 不该发生；这里也钉住"不会静默变成别的东西"。

最后一条是**退役守卫**：`_vendor` 里那个模块只允许被 `media_seam` 引用。上游新包落地后
删 `_vendor/` 时，要删的回退分支就只在一个文件里；多出一处引用就会漏删（而症状是
"某个功能突然不发图了"，不是 import 失败）。
"""

from __future__ import annotations

import asyncio
import pathlib
import re

import pytest
from plugin.plugins.qq_auto_reply import connector_seam, media_seam
from plugin.plugins.qq_auto_reply._vendor.connection_onebot import (
    qq_open_platform_media as vendored,
)

PLUGIN = pathlib.Path(__file__).resolve().parents[1]


class _HostConn:
    """宿主适配器形态：连接对象自带 `upload_image` / `send_private_image`（群图走自己那份）。"""

    CHANNEL = "open"
    mode = "open_platform"

    def __init__(self):
        self.calls: list[tuple] = []

    async def upload_image(self, *, scope, owner_id, source):
        self.calls.append(("upload_image", scope, owner_id, source))
        return "FI-host"

    async def send_private_image(self, user_id, source, *, content="", reply_message_id="", record_sent=True):
        self.calls.append(("send_private_image", user_id, source, content, reply_message_id, record_sent))
        return "MID-host"

    async def send_group_image(self, group_id, image_data, *, reply_message_id="", at_user_id="", sub_type=""):
        self.calls.append(("send_group_image", group_id, image_data, reply_message_id, at_user_id))
        return "MID-host-group"

    async def send_group_message_segments(self, group_id, segments, *, record_sent=True, keyboard=""):
        self.calls.append(("send_group_message_segments", group_id, segments, record_sent))
        return "MID-host-segments"


class _OldConn:
    """宿主还没带媒体 mixin：只有通道标记与连接器自带的发送方法。"""

    CHANNEL = "open"
    mode = "open_platform"

    def __init__(self):
        self.calls: list[tuple] = []


# ── 1. 能力判据本身 ────────────────────────────────────────────────────

def test_the_capability_marker_is_upload_image():
    """`upload_image` 是宿主媒体的指纹；用 `send_private_image` 会误判 OneBot。"""
    assert media_seam.HOST_CAPABILITY == "upload_image"
    assert media_seam.host_adapter_available(_HostConn()) is True
    assert media_seam.host_adapter_available(_OldConn()) is False
    # 有通道标记但没有上传能力：那是"开放平台的老宿主"，不是宿主适配器
    assert media_seam.is_open_platform(_OldConn()) is True


def test_the_version_qualified_helper_does_not_depend_on_the_connector_source():
    """连接器来自宿主还是副本，与"连接对象有没有媒体能力"是两件事。"""
    assert connector_seam.CONNECTOR_SOURCE in ("host", "vendored")   # 真实环境里两者都可能
    assert not hasattr(connector_seam, "open_platform_media"), (
        "connector_seam 不该再解析富媒体 —— 那会让「宿主连接器 + 还没媒体 mixin」这种组合无法表达"
    )


@pytest.mark.parametrize(
    ("conn", "expected"),
    [(_HostConn(), "host"), (_OldConn(), "vendored")],
)
def test_source_of_reports_which_half_is_used(conn, expected):
    assert media_seam.source_of(conn) == expected
    described = media_seam.describe(conn)
    if expected == "host":
        assert "宿主适配器" in described
    else:
        assert "内置副本" in described and media_seam.VENDORED_MODULE in described


def test_describe_names_the_vendored_module_when_falling_back():
    assert media_seam.VENDORED_MODULE in media_seam.describe(_OldConn())


# ── 2. 有宿主适配器时：调它的方法，**不碰副本** ─────────────────────────

def test_upload_image_prefers_the_host_adapter():
    conn = _HostConn()
    assert asyncio.run(media_seam.upload_image(conn, scope="groups", owner_id="G1", source="a.png")) == "FI-host"
    assert conn.calls == [("upload_image", "groups", "G1", "a.png")]


def test_send_private_image_prefers_the_host_adapter_and_forwards_record_sent():
    conn = _HostConn()
    mid = asyncio.run(media_seam.send_private_image(conn, "U1", "a.png", record_sent=False))
    assert mid == "MID-host"
    assert conn.calls == [("send_private_image", "U1", "a.png", "", "", False)]


def test_send_group_image_prefers_the_host_adapter():
    conn = _HostConn()
    mid = asyncio.run(media_seam.send_group_image(conn, "G1", "a.png", at_user_id="M1"))
    assert mid == "MID-host-group"
    assert conn.calls == [("send_group_image", "G1", "a.png", "", "M1")]


def test_group_image_with_text_goes_through_the_hosts_segment_send():
    """宿主那份 `send_group_image` 只发图、没有文字参数；有文字时改走段发送（图仍由它上传）。"""
    conn = _HostConn()
    asyncio.run(media_seam.send_group_image(conn, "G1", "a.png", content="看这个", at_user_id="M1"))
    kind, group_id, segments, record_sent = conn.calls[0]
    assert kind == "send_group_message_segments" and group_id == "G1"
    assert segments == [
        {"type": "at", "data": {"qq": "M1"}},
        {"type": "text", "data": {"text": "看这个"}},
        {"type": "image", "data": {"file": "a.png"}},
    ]
    assert record_sent is False


def test_the_host_path_never_touches_the_vendored_module(monkeypatch):
    """宿主适配器在时，副本模块**连导入都不该发生**（退役时才能整段删）。"""
    imported: list[str] = []

    def _boom(name):
        imported.append(name)
        raise AssertionError(f"不该导入 {name}")

    monkeypatch.setattr(media_seam.importlib, "import_module", _boom)
    conn = _HostConn()
    asyncio.run(media_seam.upload_image(conn, scope="users", owner_id="U1", source="a.png"))
    asyncio.run(media_seam.send_private_image(conn, "U1", "a.png", record_sent=False))
    asyncio.run(media_seam.send_group_image(conn, "G1", "a.png"))
    assert imported == []


# ── 3. 没有宿主适配器时：回退内置副本（行为与重构前一致） ───────────────

def test_upload_image_falls_back_to_the_vendored_module(monkeypatch):
    seen: list[tuple] = []

    async def _fake(conn, *, scope, owner_id, source):
        seen.append((conn, scope, owner_id, source))
        return "FI-vendored"

    monkeypatch.setattr(vendored, "upload_image", _fake)
    conn = _OldConn()
    assert asyncio.run(media_seam.upload_image(conn, scope="users", owner_id="U1", source="a.png")) == "FI-vendored"
    assert seen == [(conn, "users", "U1", "a.png")]


def test_send_private_image_falls_back_to_the_vendored_module(monkeypatch):
    seen: list[tuple] = []

    async def _fake(conn, user_id, source, **kw):
        seen.append((conn, user_id, source, kw))
        return "MID-vendored"

    monkeypatch.setattr(vendored, "send_private_image", _fake)
    conn = _OldConn()
    assert asyncio.run(media_seam.send_private_image(conn, "U1", "a.png", record_sent=False)) == "MID-vendored"
    assert seen[0][3] == {"content": "", "reply_message_id": "", "record_sent": False}


def test_send_group_image_falls_back_to_the_vendored_module(monkeypatch):
    seen: list[tuple] = []

    async def _fake(conn, group_id, source, **kw):
        seen.append((conn, group_id, source, kw))
        return "MID-vendored-group"

    monkeypatch.setattr(vendored, "send_group_image", _fake)
    conn = _OldConn()
    assert asyncio.run(media_seam.send_group_image(conn, "G1", "a.png")) == "MID-vendored-group"
    assert seen[0][1] == "G1" and seen[0][3].get("content") == ""


# ── 4. 退役守卫：副本只有一个引用点 ────────────────────────────────────

def test_only_the_media_seam_references_the_vendored_media_module():
    """`_vendor` 那个模块只允许被 `media_seam` 引用 —— 退役时删一个文件就够。

    查的是**可执行的引用**（`import` / `importlib.import_module`），不是注释里提一句。
    """
    offenders: list[str] = []
    for path in sorted(PLUGIN.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        code = re.sub(r"(?m)^\s*#.*$", "", text)
        if path.name == "media_seam.py":
            continue
        if "qq_open_platform_media" in code:
            offenders.append(path.name)
    assert not offenders, (
        f"这些文件还在直接引用内置副本的媒体模块: {offenders} —— "
        f"富媒体的回退必须收敛在 media_seam.py 里，否则退役删 _vendor 时会漏"
    )
    assert "qq_open_platform_media" in (PLUGIN / "media_seam.py").read_text(encoding="utf-8")


def test_the_vendored_module_is_still_shipped_for_older_hosts():
    """现在还不能删：宿主没带 mixin 的版本（含 Steam 上已发布的旧包）仍要靠它。"""
    assert (PLUGIN / "_vendor" / "connection_onebot" / "qq_open_platform_media.py").is_file()
