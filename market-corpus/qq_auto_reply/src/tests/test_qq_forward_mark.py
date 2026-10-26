"""`<mark/>` + `<forward>` 合并转发：从标记落盘到真正发出去的端到端验证。

使用者确认的做法（原话「就按文档原意接」）：标记之后的**多人多句原文**，连模型写的
那句总结一起，用合并转发发出去。

用**真实** `QQBacklogStore`（临时目录）+ 记录型 qq_client，而不是全桩 —— 这条链路
的价值就在于"标记写进去、转发时取出来、取的是对的那一段"，全桩就把被测对象换掉了。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from plugin.plugins.qq_auto_reply.backlog_models import QQBacklogMessage
from plugin.plugins.qq_auto_reply.backlog_store import QQBacklogStore
from plugin.plugins.qq_auto_reply.pipeline_models import QQReplyOutcome, QQReplyRequest
from plugin.plugins.qq_auto_reply.reply_delivery_node import QQReplyDeliveryNode
from plugin.plugins.qq_auto_reply.reply_pipeline import QQReplyPipelineRunner

GROUP = "1048307485"
OTHER_GROUP = "985066274"
ADMIN_QQ = "820040531"


class _RecordingClient:
    needs_attention = True  # NapCat

    def __init__(self) -> None:
        self.self_id = "3900000001"
        self.forwards: list[tuple[str, str, list]] = []
        self.fail_next = False

    async def send_group_forward_msg(self, group_id, messages):
        self.forwards.append(("group", str(group_id), messages))
        return None if self.fail_next else {"status": "ok"}

    async def send_private_forward_msg(self, user_id, messages):
        self.forwards.append(("private", str(user_id), messages))
        return None if self.fail_next else {"status": "ok"}

    async def set_msg_emoji_like(self, message_id, emoji_id):
        return {"status": "ok"}


class _MemoryBridge:
    """记录转发记录被写进了哪个域（真实性靠 `_seed` 的分布式断言保证）。"""

    def __init__(self) -> None:
        self.legacy: list[tuple[str, list]] = []
        self.scoped: list[tuple[dict, list]] = []

    @staticmethod
    def group_subject(gid) -> dict:
        return {"subject_kind": "group_chat", "subject_id": f"qq:{gid}"}

    @staticmethod
    def participant_subject(sid) -> dict:
        return {"subject_kind": "participant", "subject_id": f"qq:{sid}"}

    async def post_memory_history(self, endpoint, her_name, messages, timeout=5.0):
        self.legacy.append((endpoint, messages))
        return {"status": "ok"}

    async def post_scoped_memory_history(self, her_name, messages, *, subject, timeout=10.0):
        self.scoped.append((subject, messages))
        return {"status": "ok"}


def _plugin(tmp_path, *, groups=(GROUP,)):
    client = _RecordingClient()
    store = QQBacklogStore(tmp_path)
    logs: list[tuple[str, str]] = []
    bridge = _MemoryBridge()
    mgr = SimpleNamespace(list_groups=lambda: [{"group_id": g} for g in groups])
    plugin = SimpleNamespace(
        qq_client=client,
        backlog_store=store,
        group_permission_mgr=mgr,
        memory_bridge=bridge,
        attention_service=SimpleNamespace(_current_time=lambda: 1000),
        _emit_log=lambda level, msg: logs.append((level, msg)),
        _bot_nickname="皖萱",
        _qq_settings={
            "group_memory_enabled": True,
            "private_participant_memory_enabled": True,
        },
        permission_mgr=SimpleNamespace(
            get_permission_level=lambda sid: "admin" if str(sid) == ADMIN_QQ else "trusted",
        ),
        _user_sessions={f"group:{GROUP}": {"her_name": "宅久皖萱"}},
        _build_session_key=lambda *, sender_id, is_group, group_id=None: (
            f"group:{group_id}" if is_group else f"private:{sender_id}"
        ),
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
    )
    plugin.reply_delivery_node = QQReplyDeliveryNode(plugin)
    return plugin, client, store, logs


async def _seed(tmp_path, plugin, messages):
    """把消息写进真实 backlog。"""
    store: QQBacklogStore = plugin.backlog_store
    await store.ensure_group_placeholder(GROUP, group_display_name="测试群")
    for sender_id, name, text, mid, ts in messages:
        await store.append_message(
            QQBacklogMessage(
                conversation_key=f"group:{GROUP}",
                conversation_type="group",
                source_id=GROUP,
                sender_id=sender_id,
                sender_name=name,
                text=text,
                message_id=mid,
                timestamp=ts,
                group_id=GROUP,
                raw={},
            ),
            conversation_display_name="测试群",
            group_display_name="测试群",
        )


def _request(*, message_id="m-anchor"):
    return QQReplyRequest(
        message_text="先打个标记",
        sender_id="111",
        is_group=True,
        group_id=GROUP,
        current_message_id=message_id,
    )


def _outcome(**kw):
    kw.setdefault("action", "reply")
    return QQReplyOutcome(**kw)


def _runner(plugin):
    return QQReplyPipelineRunner(plugin)


# ── 节点组装 ─────────────────────────────────────────────────────────

def test_summary_goes_first_as_the_bot_node():
    nodes = QQReplyDeliveryNode.build_forward_nodes(
        [{"sender_nickname": "小明", "sender_id": "111", "text": "他先骂我的"}],
        summary="我赢了！", bot_name="皖萱", bot_uin="3900000001",
    )
    assert nodes[0]["data"]["name"] == "皖萱"
    assert nodes[0]["data"]["content"][0]["data"]["text"] == "我赢了！"
    assert nodes[1]["data"]["name"] == "小明"
    assert nodes[1]["data"]["uin"] == "111"
    assert nodes[1]["data"]["content"][0]["data"]["text"] == "他先骂我的"


def test_empty_messages_are_dropped_from_the_forward():
    nodes = QQReplyDeliveryNode.build_forward_nodes(
        [{"sender_nickname": "a", "sender_id": "1", "text": "   "},
         {"sender_nickname": "b", "sender_id": "2", "text": "有内容"}],
        summary="",
    )
    assert len(nodes) == 1 and nodes[0]["data"]["name"] == "b"


# ── 标记 → 转发 端到端 ───────────────────────────────────────────────

def test_mark_is_persisted_with_the_triggering_message(tmp_path):
    plugin, _client, store, _logs = _plugin(tmp_path)
    _runner(plugin).__class__  # noqa: B018 - 只为可读性，实际调用在下一行
    asyncio.run(_runner(plugin)._handle_forward_marks(
        _request(message_id="m-42"), _outcome(forward_mark=True),
    ))
    mark = asyncio.run(store.get_forward_mark(GROUP))
    assert mark == {"message_id": "m-42", "timestamp": 1000}


def test_forward_sends_everything_after_the_mark(tmp_path):
    """标记之后的多人多句全部进转发，且**排除标记那一条本身**。"""
    plugin, client, store, _logs = _plugin(tmp_path)
    asyncio.run(_seed(tmp_path, plugin, [
        ("111", "小明", "标记之前的旧话", "m-old", 900),
        ("111", "小明", "他先骂我的", "m-anchor", 1000),
        ("222", "小红", "明明是你先", "m-1", 1001),
        ("333", "小刚", "都别吵了", "m-2", 1002),
    ]))
    asyncio.run(store.set_forward_mark(GROUP, message_id="m-anchor", timestamp=1000))

    asyncio.run(_runner(plugin)._handle_forward_marks(
        _request(), _outcome(forward_content="我赢了！"),
    ))

    assert len(client.forwards) == 1, f"没有发出合并转发: {client.forwards!r}"
    kind, target, nodes = client.forwards[0]
    assert (kind, target) == ("group", GROUP), "默认应转发到当前群"
    texts = [n["data"]["content"][0]["data"]["text"] for n in nodes]
    assert texts[0] == "我赢了！", "总结应作为第一条"
    assert "他先骂我的" not in texts, "标记那一条本身不该被转发"
    assert "标记之前的旧话" not in texts, "标记之前的消息不该被转发"
    assert "明明是你先" in texts and "都别吵了" in texts, "标记之后的多人多句都要在"
    assert texts.index("明明是你先") < texts.index("都别吵了"), "顺序必须是时间顺序"


def test_forward_clears_the_mark_so_it_cannot_be_replayed(tmp_path):
    plugin, client, store, _logs = _plugin(tmp_path)
    asyncio.run(_seed(tmp_path, plugin, [("111", "小明", "内容", "m-1", 1001)]))
    asyncio.run(store.set_forward_mark(GROUP, message_id="m-anchor", timestamp=1000))

    asyncio.run(_runner(plugin)._handle_forward_marks(
        _request(), _outcome(forward_content="总结"),
    ))
    assert asyncio.run(store.get_forward_mark(GROUP)) is None, (
        "标记没被清掉 —— 下一次转发会把早就发过的对话再抛一遍"
    )
    assert len(client.forwards) == 1


def test_forward_without_a_mark_is_skipped_loudly(tmp_path):
    """没有起点就没有"标记之后的对话"这个集合 —— 宁可不发，也不要把整个群抛出去。"""
    plugin, client, _store, logs = _plugin(tmp_path)
    asyncio.run(_seed(tmp_path, plugin, [("111", "小明", "一堆旧话", "m-1", 900)]))

    asyncio.run(_runner(plugin)._handle_forward_marks(
        _request(), _outcome(forward_content="总结"),
    ))

    assert not client.forwards, "没有 <mark/> 起点却发了转发"
    assert any(level == "WARNING" and "mark" in msg for level, msg in logs), (
        f"跳过时没有留下可查的日志: {logs!r}"
    )


def test_failed_forward_keeps_the_mark_for_retry(tmp_path):
    """发送未确认时**不能清标记** —— 清掉就永远补不上了。"""
    plugin, client, store, _logs = _plugin(tmp_path)
    asyncio.run(_seed(tmp_path, plugin, [("111", "小明", "内容", "m-1", 1001)]))
    asyncio.run(store.set_forward_mark(GROUP, message_id="m-anchor", timestamp=1000))
    client.fail_next = True

    asyncio.run(_runner(plugin)._handle_forward_marks(
        _request(), _outcome(forward_content="总结"),
    ))
    assert asyncio.run(store.get_forward_mark(GROUP)) is not None, (
        "发送失败却把标记清了 —— 起点丢失，再也补不上"
    )


def test_forward_to_a_known_group_and_to_a_private_user(tmp_path):
    """`to` 命中已知群号 → 发到那个群；否则当作 QQ 号私聊转发。"""
    plugin, client, store, _logs = _plugin(tmp_path, groups=(GROUP, OTHER_GROUP))
    asyncio.run(_seed(tmp_path, plugin, [("111", "小明", "内容", "m-1", 1001)]))

    asyncio.run(store.set_forward_mark(GROUP, message_id="m-anchor", timestamp=1000))
    asyncio.run(_runner(plugin)._handle_forward_marks(
        _request(), _outcome(forward_content="给隔壁群看", forward_target=OTHER_GROUP),
    ))
    assert client.forwards[-1][:2] == ("group", OTHER_GROUP)

    asyncio.run(store.set_forward_mark(GROUP, message_id="m-anchor", timestamp=1000))
    asyncio.run(_runner(plugin)._handle_forward_marks(
        _request(), _outcome(forward_content="向管理员炫耀", forward_target=ADMIN_QQ),
    ))
    assert client.forwards[-1][:2] == ("private", ADMIN_QQ), (
        "不是已知群号的 to 应走私聊转发（提示词的例子就是发给管理员 QQ）"
    )


def test_forward_is_capped_as_a_safety_valve(tmp_path):
    """上限只是防炸的安全阀：把几小时闲聊整段抛出去既刷屏也没人看。"""
    plugin, client, store, _logs = _plugin(tmp_path)
    many = [("111", f"人{i}", f"消息{i}", f"m-{i}", 1000 + i) for i in range(1, 121)]
    asyncio.run(_seed(tmp_path, plugin, many))
    asyncio.run(store.set_forward_mark(GROUP, message_id="m-anchor", timestamp=1000))

    asyncio.run(_runner(plugin)._handle_forward_marks(
        _request(), _outcome(forward_content="总结"),
    ))
    nodes = client.forwards[0][2]
    assert len(nodes) <= QQReplyPipelineRunner.FORWARD_MAX_NODES + 1, (
        f"转发没有上限保护，带了 {len(nodes)} 条"
    )
    # 截取的是**最近**的一段，不是最早的一段
    texts = [n["data"]["content"][0]["data"]["text"] for n in nodes]
    assert "消息120" in texts, "截取方向反了（应保留标记之后最近的一段）"


@pytest.mark.parametrize("bad_target", ["", "0"])
def test_unconfirmed_forward_is_not_reported_as_sent(tmp_path, bad_target):
    """开放平台那套空桩返回 `{}` —— 不能算成功（群/私聊两条路都要挡）。"""
    plugin, client, store, logs = _plugin(tmp_path)
    asyncio.run(_seed(tmp_path, plugin, [("111", "小明", "内容", "m-1", 1001)]))
    asyncio.run(store.set_forward_mark(GROUP, message_id="m-anchor", timestamp=1000))

    async def _stub(*a, **k):
        return {}

    client.send_group_forward_msg = _stub
    client.send_private_forward_msg = _stub
    asyncio.run(_runner(plugin)._handle_forward_marks(
        _request(), _outcome(forward_content="总结", forward_target=bad_target),
    ))
    assert asyncio.run(store.get_forward_mark(GROUP)) is not None, "空桩被当成了发送成功"


# ── 接线本身也要钉住 ─────────────────────────────────────────────────

def test_run_delivery_wires_the_forward_handler():
    """`_run_delivery` 必须真的调用 `_handle_forward_marks`。

    **这条是 fail-to-pass 验证查出来的缺口**：只测 `_handle_forward_marks` 本身的话，
    把 `_run_delivery` 里的调用点删掉，上面那些测试照样全绿 —— 也就是"实现正确但
    没人调用"，正是这个功能修复前的状态（解析出来没有消费方）。
    """
    import ast
    import pathlib

    import plugin.plugins.qq_auto_reply.reply_pipeline as pipeline_module

    tree = ast.parse(pathlib.Path(pipeline_module.__file__).read_text(encoding="utf-8"))
    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        calls = [
            node for node in ast.walk(func)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_handle_forward_marks"
        ]
        if calls:
            assert func.name == "_run_delivery", (
                f"转发处理器被调用的位置不对: {func.name}"
            )
            return
    raise AssertionError(
        "没有任何地方调用 _handle_forward_marks —— 转发接线断了，"
        "`<mark/>`/`<forward>` 又会变成解析出来没人消费"
    )


# ── 端到端：转发成功后必须留下记忆记录 ───────────────────────────────

def test_successful_forward_records_it_in_the_recipient_domain(tmp_path):
    """走完整链路（`_handle_forward_marks` → 真发 → 补记），断言记忆真的写了。

    **这条是冲着覆盖漏洞来的**：`test_qq_forward_memory.py` 只调
    `_record_forward_in_memory` 本身，把 `_handle_forward_marks` 里的调用点删掉
    它们照样全绿 —— 上一轮 fail-to-pass 就是这么抓到同类漏洞的。
    """
    plugin, _client, store, _logs = _plugin(tmp_path)
    asyncio.run(_seed(tmp_path, plugin, [
        ("111", "小明", "标记之前的旧话", "m-old", 999),
        ("111", "小明", "这条是标记本身，不该被转发", "m-anchor", 1000),
        ("222", "小红", "明明是你先", "m-1", 1001),
        ("333", "小刚", "都别吵了", "m-2", 1002),
    ]))
    asyncio.run(store.set_forward_mark(GROUP, message_id="m-anchor", timestamp=1000))

    asyncio.run(_runner(plugin)._handle_forward_marks(
        _request(), _outcome(forward_content="我赢了", forward_target=ADMIN_QQ),
    ))

    bridge = plugin.memory_bridge
    assert bridge.legacy, "转发成功了却没有写进管理员的私聊记忆域"
    text = "\n".join(
        part.get("text", "")
        for _endpoint, messages in bridge.legacy
        for m in messages
        for part in (m.get("content") or [])
        if isinstance(part, dict)
    )
    assert "【转发记录】" in text and "不是我说的" in text
    assert "小红(222): 明明是你先" in text, f"被转发的原文没记全（发言人名也要对）: {text!r}"
    assert "小刚(333): 都别吵了" in text
    assert "这条是标记本身" not in text, "标记那条被一起记了"
    assert "标记之前的旧话" not in text, "标记之前的消息被一起记了"
    # 源群那条动作记录也在
    assert any(s["subject_id"] == f"qq:{GROUP}" for s, _ in bridge.scoped)


def test_failed_forward_records_nothing(tmp_path):
    """发送未确认时不该留下「我转发了」的记录 —— 那是假记忆。"""
    plugin, client, store, _logs = _plugin(tmp_path)
    asyncio.run(_seed(tmp_path, plugin, [("111", "小明", "内容", "m-1", 1001)]))
    asyncio.run(store.set_forward_mark(GROUP, message_id="m-anchor", timestamp=1000))
    client.fail_next = True

    asyncio.run(_runner(plugin)._handle_forward_marks(
        _request(), _outcome(forward_content="总结", forward_target=ADMIN_QQ),
    ))
    assert not plugin.memory_bridge.legacy, "转发没发出去却记了记忆"
    assert not plugin.memory_bridge.scoped
