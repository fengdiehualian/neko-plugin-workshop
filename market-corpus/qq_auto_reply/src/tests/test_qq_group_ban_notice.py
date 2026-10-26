# -*- coding: utf-8 -*-
"""连接层：**别人**被禁言/解禁必须往上游送（改之前是被丢掉的）。

改之前的代码（两份连接器都有）：

    if not is_whole_group and not is_self:
        return  # someone else muted; not our concern

于是插件永远不知道群里有人被禁言 —— 使用者 2026-09-29 要的
「对正在聊天的人的禁言做出反应」根本无从谈起。

这里只测**归一化与入队**（连接层的职责），"该不该开口"由派发层判断
（见 `test_qq_ban_reaction.py`）。用 `object.__new__` 绕过 `__init__`：
后者会建 WS 服务器与后台任务，而这两条路径都不需要它们。

⚠️ 宿主仓库里那份 `utils/connection/onebot/onebot_client.py` 是**活的那份**
（真机日志：`[QQ] 连接器来源: host`），这里是插件里的回退副本。两份必须同改，
所以这条用例守的是同一份代码形状。
"""
from __future__ import annotations

import asyncio
import json

from plugin.plugins.qq_auto_reply._vendor.connection_onebot.onebot_client import OneBotClient

GROUP = "1048307485"
ALICE = "1782348687"
ADMIN = "10001"
BOT = "3281414178"


def _client(*, self_id: str = BOT) -> OneBotClient:
    client = object.__new__(OneBotClient)      # 绕过 __init__（见文件头）
    client._message_queue = asyncio.Queue()
    client._pending_actions = {}
    client._group_muted = {}
    client._self_id = self_id
    client.logger = None
    client._emit_log = lambda *a, **k: None
    return client


def _feed(client: OneBotClient, payload: dict) -> None:
    asyncio.run(client._process_incoming(json.dumps(payload)))


def _take(client: OneBotClient):
    """出队一条并归一化（走真实的 `receive_message`）。"""
    return asyncio.run(client.receive_message(timeout=0.05))


def _ban(*, user_id: str, sub_type: str = "ban", duration: int = 600, self_id: str = BOT) -> dict:
    """一条原始 OneBot 事件：每个事件都带 bot 自己的 `self_id`。"""
    return {
        "post_type": "notice", "notice_type": "group_ban", "sub_type": sub_type,
        "group_id": GROUP, "user_id": user_id, "operator_id": ADMIN,
        "duration": duration, "time": 1_790_000_000, "self_id": self_id,
    }


# ── 一、第三方被禁言 / 解禁：入队 ────────────────────────────────────

def test_a_third_party_ban_is_enqueued_and_normalized():
    client = _client()
    _feed(client, _ban(user_id=ALICE))

    assert client._message_queue.qsize() == 1, "别人被禁言又被丢掉了"

    notice = _take(client)
    assert notice["message_type"] == "notice"
    assert notice["notice_type"] == "group_ban", "事件种类没取出来（poke 取的是 sub_type）"
    assert notice["sub_type"] == "ban"
    assert notice["user_id"] == ALICE
    assert notice["operator_id"] == ADMIN
    assert notice["duration"] == 600
    assert notice["group_id"] == GROUP


def test_a_third_party_lift_ban_is_enqueued_too():
    """解禁也要送（使用者选的是"解禁也反应"）。"""
    client = _client()
    _feed(client, _ban(user_id=ALICE, sub_type="lift_ban", duration=0))

    notice = _take(client)
    assert notice["notice_type"] == "group_ban"
    assert notice["sub_type"] == "lift_ban"


# ── 二、她自己 / 全员被禁言：只记账，不入队 ──────────────────────────

def test_her_own_ban_is_not_enqueued_but_tracked():
    client = _client(self_id=ALICE)
    _feed(client, _ban(user_id=ALICE))

    assert client._message_queue.qsize() == 0, "自己被禁言不该开一轮生成（她也发不出去）"
    assert client.is_group_muted(GROUP) is True, "自己被禁言没有记进 _group_muted"


def test_whole_group_ban_is_not_enqueued_but_tracked():
    client = _client()
    _feed(client, _ban(user_id="0", duration=0))       # duration=0 = 全员禁言（无期限）

    assert client._message_queue.qsize() == 0
    assert client.is_group_muted(GROUP) is True


def test_her_own_lift_ban_clears_the_flag_without_enqueueing():
    client = _client(self_id=ALICE)
    _feed(client, _ban(user_id=ALICE))
    _feed(client, _ban(user_id=ALICE, sub_type="lift_ban", duration=0))

    assert client._message_queue.qsize() == 0
    assert client.is_group_muted(GROUP) is False


# ── 三、戳一戳那条路不受影响 ────────────────────────────────────────

def test_poke_notice_normalization_is_unchanged():
    """poke 的形状是 `notice_type=notify, sub_type=poke`，事件名仍取 sub_type。"""
    client = _client()
    _feed(client, {
        "post_type": "notice", "notice_type": "notify", "sub_type": "poke",
        "group_id": GROUP, "user_id": ALICE, "target_id": "3281414178",
        "time": 1_790_000_000,
    })

    notice = _take(client)
    assert notice["notice_type"] == "poke"
    assert notice["user_id"] == ALICE
    assert notice["target_id"] == "3281414178"


def test_other_notices_are_still_dropped():
    """其它通知（这里是群名片变更）仍然不处理 —— 别顺手把它们也放进来了。"""
    client = _client()
    _feed(client, {
        "post_type": "notice", "notice_type": "group_card", "sub_type": "group_card",
        "group_id": GROUP, "user_id": ALICE,
    })

    assert client._message_queue.qsize() == 0


# ── 四、身份：她自己被禁言（登录信息还没到）────────────────────────────

def test_her_own_ban_before_login_info_is_still_hers():
    """禁言通知可能先于 get_login_info（或任何群消息）到达，那时 `_self_id` 还是空的。

    按空身份分类会把她自己的禁言判成"第三方"：通知错误入队，而 `_group_muted` 不更新
    —— 她会继续在一个自己已被禁言的群里尝试说话。通知自带 `self_id`，就用它。
    """
    client = _client(self_id="")
    assert client._self_id == "", "夹具应当从「没有身份」开始"

    _feed(client, _ban(user_id=BOT))

    assert client._message_queue.qsize() == 0, "她自己的禁言被当成第三方送出去了"
    assert client.is_group_muted(GROUP) is True, "她自己的禁言没有记进 _group_muted"
    assert client._self_id == BOT, "通知没有教会客户端自己的 id"


def test_a_third_party_ban_with_an_unknown_identity_is_still_forwarded():
    client = _client(self_id="")

    _feed(client, _ban(user_id=ALICE))

    assert client._message_queue.qsize() == 1
    assert client.is_group_muted(GROUP) is False, "别人的禁言不该把她自己按下去"
    assert client._self_id == BOT


# ── 五、通知也要进 inbound sink ──────────────────────────────────────

def test_the_normalized_notice_reaches_the_registered_sink():
    """只挂 sink 的消费方也必须看得到通知（消息那条路对每条消息都分发）。"""
    seen: list[dict] = []

    async def scenario():
        await client.receive_message(timeout=0.05)
        await asyncio.sleep(0.05)          # sink 跑在自己的任务上
        return seen

    client = _client()
    client.set_inbound_sink(lambda message: _collect(seen, message))
    # 先入队、再进事件循环：`_feed` 自己会调 asyncio.run，而这里**不能**嵌套
    # （宿主套件的 conftest 用 nest_asyncio 允许嵌套，插件套件没有那个补丁 ——
    #  写在协程里就等于只在一个仓库里有效）。
    _feed(client, _ban(user_id=ALICE))

    seen = asyncio.run(scenario())

    assert len(seen) == 1, "sink 没有收到禁言通知"
    assert seen[0]["notice_type"] == "group_ban"
    assert seen[0]["user_id"] == ALICE


async def _collect(bucket: list[dict], message: dict) -> None:
    bucket.append(message)
