"""P1/P2 缺陷的回归测试：驱动循环停摆、注意力落盘竞态、回溯丢老消息、失败不兜底。

每个测试对应一处已修复的真实缺陷，注释里写明"修之前会怎样"——这样将来谁改了
实现、把行为退回去，红灯能直接告诉他人是怎么坏的，而不只是"断言失败了"。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from plugin.plugins.qq_auto_reply.attention_service import QQAttentionService
from plugin.plugins.qq_auto_reply.backlog_models import QQBacklogMessage
from plugin.plugins.qq_auto_reply.backlog_store import QQBacklogStore
from plugin.plugins.qq_auto_reply.pipeline_models import QQReplyContext
from plugin.plugins.qq_auto_reply.reply_generation_service import QQReplyGenerationService
from plugin.plugins.qq_auto_reply.session_runtime_service import QQSessionRuntimeService

# ── 回溯补回：超窗的老消息不得被静默标掉 ──────────────────────────────

def _append(store: QQBacklogStore, *, mid: str, ts: int, gid: str = "100") -> None:
    return store.append_message(
        QQBacklogMessage(
            conversation_key=f"group:{gid}:user:u1",
            conversation_type="group",
            source_id=gid,
            sender_id="u1",
            sender_name="某人",
            text=f"消息{mid}",
            message_id=mid,
            timestamp=ts,
            group_id=gid,
        ),
        conversation_display_name="某人",
        group_display_name="测试群",
    )


def _unreviewed_ids(state: dict, gid: str = "100") -> set[str]:
    group = state["groups"][gid]
    out: set[str] = set()
    for key in group.get("conversation_keys") or []:
        for item in (state["conversations"].get(key) or {}).get("messages") or []:
            if item.get("review_status") == "unreviewed":
                out.add(str(item.get("message_id")))
    return out


def test_marking_only_the_consumed_window_keeps_older_messages_pending(tmp_path: Path):
    """回溯只把最新 N 条喂给模型，那 N 条之外的老消息必须留着，不能一起标已审。

    修之前 ``mark_group_reviewed`` 无条件把该群所有会话的所有消息标成 reviewed，
    于是超出 ``retroactive_review_max_messages`` 窗口的老消息永远不会被补回——
    用户既没见过，也再没有机会见到。
    """
    store = QQBacklogStore(tmp_path)

    async def _run() -> None:
        for i in range(1, 8):
            await _append(store, mid=f"m{i}", ts=1000 + i)
        window = await store.get_unreviewed_messages_since("100", since_timestamp=0, limit=3)
        assert [m["message_id"] for m in window] == ["m5", "m6", "m7"]

        await store.mark_group_reviewed(
            "100", message_ids={str(m["message_id"]) for m in window},
        )
        state = await store.load()
        # 窗口内的三条已审，窗口外的四条仍是未审（修之前这里是空集）
        assert _unreviewed_ids(state) == {"m1", "m2", "m3", "m4"}

    asyncio.run(_run())


def test_mark_group_reviewed_without_ids_keeps_mark_all_semantics(tmp_path: Path):
    """手动入口/relay 不传 message_ids 时必须仍然整群全标（向后兼容）。"""
    store = QQBacklogStore(tmp_path)

    async def _run() -> None:
        for i in range(1, 4):
            await _append(store, mid=f"m{i}", ts=1000 + i)
        await store.mark_group_reviewed("100")
        assert _unreviewed_ids(await store.load()) == set()

    asyncio.run(_run())


def test_message_ids_since_returns_the_full_set_not_the_window(tmp_path: Path):
    """``get_unreviewed_message_ids_since`` 是"全集"，用于定标记边界。"""
    store = QQBacklogStore(tmp_path)

    async def _run() -> None:
        for i in range(1, 6):
            await _append(store, mid=f"m{i}", ts=1000 + i)
        assert await store.get_unreviewed_message_ids_since("100") == {"m1", "m2", "m3", "m4", "m5"}
        # 窗口版只给最新两条，两者语义必须不同
        window = await store.get_unreviewed_messages_since("100", limit=2)
        assert [m["message_id"] for m in window] == ["m4", "m5"]

    asyncio.run(_run())


# ── 注意力落盘：锁内读改写，不得吃掉并发 append 的消息 ────────────────

def test_update_group_attention_state_preserves_conversations(tmp_path: Path):
    """注意力落盘只该换 ``group_attention_state``，别的字段原样保留。

    修之前 ``attention_service._persist`` 自己 ``load→save``：与
    ``append_message`` 交错时整份文档互相覆盖，要么丢刚 append 的群消息、
    要么丢刚推进的注意力分数。
    """
    store = QQBacklogStore(tmp_path)

    async def _run() -> None:
        await _append(store, mid="m1", ts=1001)
        await store.update_group_attention_state({"100": {"attention_score": 3.5}})
        state = await store.load()
        assert state["group_attention_state"] == {"100": {"attention_score": 3.5}}
        # 并发 append 出来的会话结构没有被注意力落盘抹掉
        assert _unreviewed_ids(state) == {"m1"}

        # 反向：先写注意力再 append，注意力不得被 append 抹掉
        await _append(store, mid="m2", ts=1002)
        state = await store.load()
        assert state["group_attention_state"] == {"100": {"attention_score": 3.5}}
        assert _unreviewed_ids(state) == {"m1", "m2"}

    asyncio.run(_run())


def test_persist_still_writes_attention_state(tmp_path: Path):
    """健全性检查（**不是**竞态回归测试）：``_persist`` 仍能把状态写下去。

    本测试在修改前后都通过——旧的 ``load→save`` 实现同样会落盘，只是路径不对
    （锁外读改写，会与 ``append_message`` 互相覆盖）。真正的竞态回归测试是上面
    的 ``test_update_group_attention_state_preserves_conversations``（它在原始代码
    上失败）。保留本测试只为在将来有人重构 ``_persist`` 时，有一根钉子钉住
    "它还在落盘"。
    """
    store = QQBacklogStore(tmp_path)
    # cleanup_stale_cache 会删掉"不在信任列表里"的群，所以这个桩必须把群报成
    # 受信任的，否则测的是清理而不是落盘。
    plugin = SimpleNamespace(
        backlog_store=store,
        group_permission_mgr=SimpleNamespace(list_groups=lambda: [{"group_id": "100"}]),
        logger=_RecordingLogger(),
    )
    service = QQAttentionService(plugin)
    service._write_state(service._load_state("100"))

    async def _run() -> None:
        await service._persist()
        state = await store.load()
        assert "100" in state["group_attention_state"]

    asyncio.run(_run())


# ── 驱动循环：单轮异常不得杀掉循环 ────────────────────────────────────

class _RecordingLogger:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, msg: str, *a, **k) -> None:
        self.warnings.append(str(msg))

    def info(self, msg: str, *a, **k) -> None:
        pass


def test_decay_loop_survives_a_failing_round():
    """``decay_all`` 抛一次之后，衰减循环必须继续跑下一轮。

    修之前 ``decay_all()`` 在 try 之外、只捕 CancelledError 的循环里执行，一次
    坏 JSON/磁盘错误就永久终止注意力推进与焦点释放，且没有任何日志。
    """
    logger = _RecordingLogger()
    calls = {"n": 0}

    class _Attention(QQAttentionService):
        async def decay_all(self) -> None:
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("磁盘临时故障")

    plugin = SimpleNamespace(logger=logger, group_permission_mgr=None)
    service = _Attention(plugin)

    async def _run() -> None:
        task = asyncio.create_task(service._decay_loop(0.01))
        for _ in range(200):
            await asyncio.sleep(0.01)
            if calls["n"] >= 3:
                break
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        # 循环活过了第一轮的异常
        assert calls["n"] >= 3, f"衰减循环在异常后停摆，只跑了 {calls['n']} 轮"
        assert any("衰减轮次异常" in w for w in logger.warnings)

    asyncio.run(_run())


def test_housekeeping_loop_survives_a_failing_round():
    """idle 结算抛一次之后，清扫循环必须继续跑下一轮。"""
    logger = _RecordingLogger()
    calls = {"n": 0}

    class _Plugin:
        SESSION_SWEEP_INTERVAL_SECONDS = 0.01
        display_name_service = None
        attention_service = None

        def __init__(self) -> None:
            self.logger = logger

        async def _flush_idle_memory_sessions(self) -> None:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("记忆服务暂时不可用")

    service = QQSessionRuntimeService(_Plugin())

    async def _run() -> None:
        task = asyncio.create_task(service.session_housekeeping_loop())
        for _ in range(200):
            await asyncio.sleep(0.01)
            if calls["n"] >= 3:
                break
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert calls["n"] >= 3, f"清扫循环在异常后停摆，只跑了 {calls['n']} 轮"
        assert any("清扫轮次异常" in w for w in logger.warnings)

    asyncio.run(_run())


# ── LLM 失败路径：超时与异常都必须允许直连兜底 ────────────────────────

def _generation_service(*, raise_exc: BaseException) -> QQReplyGenerationService:
    logger = _RecordingLogger()
    logger.exception = lambda msg, *a, **k: None  # type: ignore[attr-defined]

    async def _boom(**kwargs):
        raise raise_exc

    async def _noop_discard(session_key, reason=""):
        return True

    plugin = SimpleNamespace(
        logger=logger,
        session_runtime_service=SimpleNamespace(
            build_generation_session_key=lambda ctx: "group:100",
            discard_session=_noop_discard,
        ),
        session_bootstrap_service=SimpleNamespace(
            ensure_generation_session=None,  # 下面按需覆盖
        ),
        session_memory_service=SimpleNamespace(
            record_synthetic_prompt_rows=lambda *a, **k: None,
        ),
        _user_sessions={},
    )
    service = QQReplyGenerationService(plugin)
    service._run_session_generation = _boom  # type: ignore[method-assign]
    return service


async def _ensure_session(context, session_key):
    return {"memory_enabled": False}


def _make_context() -> QQReplyContext:
    """最小可用的群聊 context。

    ``QQReplyContext`` 的必填字段很多，而本测试只关心生成失败路径的返回值，
    其余一律给常量——不构造真实 prompt，避免把测试绑到提示词实现上。
    """
    return QQReplyContext(
        message="在吗",
        attachments=None,
        permission_level="admin",
        sender_id="u1",
        is_group=True,
        group_id="100",
        user_nickname="某人",
        use_memory_context=True,
        persist_memory=True,
        ephemeral_session=False,
        group_facing=True,
        group_scene_mode="shared_context",
        scene_mode="group",
        master_name="主人",
        her_name="猫娘",
        user_title="",
        character_prompt="",
        character_card_fields={},
        prompt_message="在吗",
        system_prompt="你是猫娘",
        memory_context_used=False,
        core_memory_text="",
        recalled_memory_text="",
        recalled_memory_used=False,
        login_status="online",
        login_self_id="1",
        login_nickname="猫娘",
    )


@pytest.mark.parametrize(
    "exc",
    [asyncio.TimeoutError(), RuntimeError("供应商 500")],
    ids=["timeout", "error"],
)
def test_failed_generation_allows_direct_llm_fallback(exc: BaseException):
    """主会话超时/报错返回的结果必须 ``allow_fallback=True``。

    修之前这两条路径返回的 ``QQModelResult`` 沿用默认 ``allow_fallback=False``，
    而 ``reply_model_node.generate`` 在它为假时直接返回——于是"供应商超时/报错"
    等于静默不回：用户看到猫娘彻底没反应，日志里只有一条 warning。
    """
    service = _generation_service(raise_exc=exc)
    service.plugin.session_bootstrap_service.ensure_generation_session = _ensure_session
    context = _make_context()

    result = asyncio.run(service.run_primary_session_call(context))

    assert result.reply_text is None
    assert result.allow_fallback is True, "失败路径必须允许兜底，否则整轮静默"
    assert result.fallback_reason in {"session_timeout", "session_error"}
