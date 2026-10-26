"""私聊发图：两条通道的分流，以及"发不出去就当没发出去"。

以前 `_send_sticker` 第一行就是 `if plan.target_type != "group": return False` ——
表情包在私聊里**静默消失**（不是没实现协议，是这条路根本没写）。这轮补上了，
两条通道的走法不同，所以更要钉住分流本身：

* **开放平台**：走 `media_seam` —— 它优先用连接对象自带的**宿主适配器**，
  宿主还没带媒体 mixin 时才回退内置副本（哪个后端由 `tests/test_qq_media_seam.py` 钉）；
* **OneBot（NapCat 等）**：`send_private_msg` 本来就认 image 段，用现成接口。

走错任何一条的症状都是"私聊里表情包没了"，而且不会报错。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from plugin.plugins.qq_auto_reply import media_seam
from plugin.plugins.qq_auto_reply.pipeline_models import QQDeliveryPlan, QQMessageBlock
from plugin.plugins.qq_auto_reply.reply_delivery_node import QQReplyDeliveryNode

STICKER_PATH = "C:/fake/sticker.png"


class _Client:
    """按需记录：群图 / 私聊 segments / 通道标记。"""

    def __init__(self, *, channel: str = "", mode: str = "", needs_attention: bool = True):
        self.CHANNEL = channel
        self.mode = mode
        self.needs_attention = needs_attention
        self.group_images: list[tuple[str, str]] = []
        self.private_segments: list[tuple[str, list]] = []

    async def send_group_image(self, group_id, file, **kw):
        self.group_images.append((str(group_id), str(file)))
        return "mid-group-image"

    async def send_private_message_segments(self, user_id, segments, **kw):
        self.private_segments.append((str(user_id), segments))
        return "mid-private-segments"


def _node(client, *, sticker_ok: bool = True):
    plugin = SimpleNamespace(
        qq_client=client,
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
        _emit_log=lambda *a, **k: None,
        _resolve_sticker_path=lambda sid: STICKER_PATH if sticker_ok else "",
        _mask_token=lambda t: t,
        _get_reply_mode=lambda: "text",
        i18n=SimpleNamespace(t=lambda key, default=None: default or key),
    )
    return QQReplyDeliveryNode(plugin)


def _sticker_plan(*, is_group: bool) -> QQDeliveryPlan:
    return QQDeliveryPlan(
        target_type="group" if is_group else "private",
        target_id="1048307485" if is_group else "820040531",
        blocks=[QQMessageBlock(sticker="7")],
    )


def test_group_stickers_still_go_through_send_group_image():
    """**OneBot** 的群聊那条不许被这次改动碰到（回归守卫）。"""
    client = _Client()
    node = _node(client)

    delivered = asyncio.run(node._send_sticker(_sticker_plan(is_group=True), QQMessageBlock(sticker="7")))

    assert delivered is True
    assert client.group_images == [("1048307485", STICKER_PATH)]
    assert client.private_segments == []


def test_group_sticker_on_the_open_platform_goes_through_the_media_helper(monkeypatch):
    """群聊在开放平台上也必须走富媒体流程。

    这是**真机实测逼出来的**：连接器那份（宿主副本）的群聊图只实现旧式直传，
    而 2026-09-26 的现场日志证明旧式直传在开放平台已经失效：

        [QQOpenPlatform] 图片直传上传未拿到 file_info
        [QQOpenPlatform] 图片上传成功(分片): wIFo43EanZwsn01Ru9mCJ9rU

    也就是说这条路不接的话，群聊表情包只会静默降级成「[图片]」三个字。
    """
    calls: list[tuple] = []

    async def _fake(conn, group_id, source, **kw):
        calls.append((conn, group_id, source, kw))
        return "mid-group-image"

    monkeypatch.setattr(media_seam, "send_group_image", _fake)
    client = _Client(channel="open", mode="open_platform", needs_attention=False)
    node = _node(client)

    delivered = asyncio.run(node._send_sticker(_sticker_plan(is_group=True), QQMessageBlock(sticker="7")))

    assert delivered is True
    assert calls and calls[0][1] == "1048307485" and calls[0][2] == STICKER_PATH
    # 群图不带额外关键字：记不记账由后端决定（宿主与副本的群图都不记账），不是调用方传的
    assert calls[0][3] == {}
    # 走对了路就不该再往连接器那条（只会降级的）群聊图接口上发一遍
    assert client.group_images == []


def test_private_sticker_on_the_open_platform_goes_through_the_media_helper(monkeypatch):
    """开放平台：走富媒体入口（表情包一律 `record_sent=False`，不进"她说过的话"的 id 缓存）。"""
    calls: list[tuple] = []

    async def _fake(conn, user_id, source, **kw):
        calls.append((conn, user_id, source, kw))
        return "mid-private-image"

    monkeypatch.setattr(media_seam, "send_private_image", _fake)
    client = _Client(channel="open", mode="open_platform", needs_attention=False)
    node = _node(client)

    delivered = asyncio.run(node._send_sticker(_sticker_plan(is_group=False), QQMessageBlock(sticker="7")))

    assert delivered is True
    assert calls and calls[0][1] == "820040531" and calls[0][2] == STICKER_PATH
    assert calls[0][0] is client, "必须把**当前这个**连接对象传进去"
    # 走对了路就不该再往 OneBot 的 segments 接口上发一遍
    assert client.private_segments == []


def test_private_sticker_on_onebot_uses_an_image_segment():
    client = _Client(channel="onebot", mode="napcat", needs_attention=True)
    node = _node(client)

    delivered = asyncio.run(node._send_sticker(_sticker_plan(is_group=False), QQMessageBlock(sticker="7")))

    assert delivered is True
    assert client.private_segments == [
        ("820040531", [{"type": "image", "data": {"file": STICKER_PATH}}]),
    ]
    assert client.group_images == []


def test_a_failed_private_upload_reports_undelivered(monkeypatch):
    """传不上去要如实报 False —— 上层据此把这一块记成"没投递"，别写进"已说出口"的记忆。"""
    async def _fail(conn, user_id, source, **kw):
        return None

    monkeypatch.setattr(media_seam, "send_private_image", _fail)
    client = _Client(channel="open", mode="open_platform", needs_attention=False)
    node = _node(client)

    delivered = asyncio.run(node._send_sticker(_sticker_plan(is_group=False), QQMessageBlock(sticker="7")))

    assert delivered is False
    assert client.private_segments == []


def test_an_unresolvable_sticker_id_sends_nothing_on_either_target():
    for is_group in (True, False):
        client = _Client(channel="open", mode="open_platform", needs_attention=False)
        node = _node(client, sticker_ok=False)

        assert asyncio.run(node._send_sticker(_sticker_plan(is_group=is_group), QQMessageBlock(sticker="999"))) is False
        assert client.group_images == [] and client.private_segments == []


@pytest.mark.parametrize(
    ("channel", "mode", "expected"),
    [
        ("open", "", True),
        ("", "open_platform", True),
        ("onebot", "napcat", False),
        ("", "napcat_forward", False),
    ],
)
def test_the_open_platform_branch_is_decided_by_the_channel_marker(channel, mode, expected):
    """分流判据就是 `media_seam.is_open_platform`：两个字段任一命中即可（宿主/副本取值域不同）。"""
    client = _Client(channel=channel, mode=mode)
    assert media_seam.is_open_platform(client) is expected
