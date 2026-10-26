"""两处纵深防御/资源回收修复的回归测试。

1) **读取侧空 group_id 必须 fail-closed**：`resolve_group_recall_subjects` 此前不
   设防，空串会经 `group_subject` 拼出 `qq:` —— 那是**所有"没有群号"的群共用的
   一个桶**，任一畸形群轮都能读到（并可能写进）别的群的记忆。写侧一直有
   `while group_id:` 闸，读侧靠两个调用方各自早退兜住；现在函数自己也拒。

2) **memory 未开启的会话没有任何周期性回收**：唯一淘汰路径
   `_flush_idle_memory_sessions` 第一句就 `if not memory_enabled: continue`，
   而群记忆默认关闭 ⇒ 默认配置下 `_user_sessions` 随"见过的群 + 私聊对象"单调
   增长，每个都攥着活的对话客户端与无上限的历史。新增 `reap_stale_sessions`
   做「容量 + 空闲」双闸回收。
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
from plugin.plugins.qq_auto_reply.memory_bridge import QQMemoryBridge
from plugin.plugins.qq_auto_reply.memory_tool_service import (
    resolve_group_recall_subjects,
)
from plugin.plugins.qq_auto_reply.session_runtime_service import (
    SESSION_HARD_LIMIT,
    SESSION_REAP_MAX_PER_SWEEP,
    SESSION_REAP_MIN_IDLE_SECONDS,
    QQSessionRuntimeService,
)

GID = "1800000001"


# ── 1) 读取侧空 group_id ────────────────────────────────────────────

def _stub_memory_plugin(*, member_on: bool = True):
    return SimpleNamespace(
        memory_bridge=QQMemoryBridge(plugin=None),
        _qq_settings={"group_member_memory_enabled": member_on},
        backlog_store=None,
        logger=None,
    )


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_empty_group_id_yields_no_subjects(blank):
    """空/全空白/None 群号一律返回 ([], False) —— 绝不产出共享桶 `qq:`。"""
    subs, used = asyncio.run(resolve_group_recall_subjects(
        _stub_memory_plugin(), group_id=blank, memory_sender_id="100001",
    ))
    assert subs == [], f"空群号（{blank!r}）产出了 subject：{subs}"
    assert used is False


def test_real_group_id_is_stripped_and_still_works():
    """正常路径不受影响，且群号两侧空白会被规范化（与写侧同口径）。"""
    subs, used = asyncio.run(resolve_group_recall_subjects(
        _stub_memory_plugin(), group_id=f"  {GID}  ", memory_sender_id="100001",
    ))
    assert [s["subject_id"] for s in subs] == [f"qq:{GID}", f"qq:{GID}:100001"]
    assert used is True
    assert subs[0]["subject_kind"] == "group_chat"


def test_no_shared_bucket_can_be_produced_by_any_blank_form():
    """明确钉住：任何空值形态都不得出现 `qq:` 或以 `qq::` 开头的域。"""
    for blank in ("", " ", "\t", None):
        subs, _ = asyncio.run(resolve_group_recall_subjects(
            _stub_memory_plugin(), group_id=blank, memory_sender_id="100001",
        ))
        for s in subs:
            sid = s["subject_id"]
            assert sid != "qq:" and not sid.startswith("qq::"), (
                f"空群号 {blank!r} 产出了共享桶 {sid!r}"
            )


# ── 2) 会话容量回收 ────────────────────────────────────────────────

class _Recorder:
    """最小 logger：把 info/warning 的消息收进列表。

    ⚠️ 必须是**方法**而不是列表属性 —— 生产代码调用 `logger.warning(msg)`，
    属性会是 "'list' object is not callable"（踩过一次）。
    """

    def __init__(self) -> None:
        self.infos: list[str] = []
        self.warnings: list[str] = []

    def info(self, msg, *a, **k): self.infos.append(str(msg))
    def warning(self, msg, *a, **k): self.warnings.append(str(msg))
    def error(self, msg, *a, **k): pass
    def exception(self, msg, *a, **k): pass


def _session_data(*, memory_enabled: bool, idle_seconds: float, **extra):
    return {
        "memory_enabled": memory_enabled,
        "last_activity_at": time.time() - idle_seconds,
        **extra,
    }


def _reaper_plugin(sessions: dict, *, pending: set[str] | None = None):
    discarded: list[str] = []
    log = _Recorder()

    async def _discard(session_key, reason=""):
        discarded.append(session_key)
        sessions.pop(session_key, None)
        return True

    plugin = SimpleNamespace(
        _user_sessions=sessions,
        logger=log,
        _has_pending_session_settlement=lambda k: k in (pending or set()),
    )
    svc = QQSessionRuntimeService(plugin)
    # 只替换 discard（它自己已有完整测试）；本测试盯的是"选谁回收"的判据。
    svc.discard_session = _discard  # type: ignore[method-assign]
    return svc, discarded, log


def test_no_reap_when_under_capacity():
    """未超上限时一条都不回收 —— 容量闸不该变成"定期清空"。"""
    sessions = {f"group:{i}": _session_data(memory_enabled=False, idle_seconds=99999)
                for i in range(10)}
    svc, discarded, _ = _reaper_plugin(sessions)
    assert asyncio.run(svc.reap_stale_sessions()) == 0
    assert discarded == []


def test_reaps_idle_sessions_over_capacity():
    """超上限时回收"memory 未开 + 足够空闲"的会话。"""
    sessions = {}
    for i in range(SESSION_HARD_LIMIT + 5):
        sessions[f"group:{i}"] = _session_data(
            memory_enabled=False, idle_seconds=SESSION_REAP_MIN_IDLE_SECONDS + 60,
        )
    svc, discarded, log = _reaper_plugin(sessions)
    reaped = asyncio.run(svc.reap_stale_sessions())
    assert reaped == 5, f"应回收多出的 5 条，实际 {reaped}"
    assert len(discarded) == 5
    assert any("[Reap]" in m for m in log.infos), "回收必须有可观测日志"


def test_recently_active_sessions_are_never_reaped():
    """空闲时长不够的会话不回收 —— 刚热过的会话重建成本高（bootstrap + 历史）。"""
    sessions = {}
    for i in range(SESSION_HARD_LIMIT + 5):
        sessions[f"group:{i}"] = _session_data(
            memory_enabled=False, idle_seconds=SESSION_REAP_MIN_IDLE_SECONDS - 60,
        )
    svc, discarded, _ = _reaper_plugin(sessions)
    assert asyncio.run(svc.reap_stale_sessions()) == 0
    assert discarded == []


def test_memory_enabled_sessions_are_left_to_idle_settlement():
    """memory 开启的会话交给 `_flush_idle_memory_sessions`（先结算再淘汰），
    容量闸不得越权直接丢弃它们 —— 否则会丢掉未结算的记忆。"""
    sessions = {}
    for i in range(SESSION_HARD_LIMIT + 5):
        sessions[f"group:{i}"] = _session_data(
            memory_enabled=True, idle_seconds=SESSION_REAP_MIN_IDLE_SECONDS * 10,
        )
    svc, discarded, _ = _reaper_plugin(sessions)
    assert asyncio.run(svc.reap_stale_sessions()) == 0
    assert discarded == []


def test_sessions_owing_an_opt_out_settlement_are_kept():
    """欠着 opt-out 结算的会话不回收（无论哪种标记）。"""
    sessions = {}
    for i in range(SESSION_HARD_LIMIT + 5):
        sessions[f"group:{i}"] = _session_data(
            memory_enabled=False, idle_seconds=SESSION_REAP_MIN_IDLE_SECONDS * 10,
            pending_disable_settle=True,
        )
    svc, discarded, _ = _reaper_plugin(sessions)
    assert asyncio.run(svc.reap_stale_sessions()) == 0
    assert discarded == []

    sessions2 = {}
    for i in range(SESSION_HARD_LIMIT + 5):
        sessions2[f"group:{i}"] = _session_data(
            memory_enabled=False, idle_seconds=SESSION_REAP_MIN_IDLE_SECONDS * 10,
            pending_settle_buckets={"x": 1},
        )
    svc2, discarded2, _ = _reaper_plugin(sessions2)
    assert asyncio.run(svc2.reap_stale_sessions()) == 0
    assert discarded2 == []


def test_sessions_with_in_flight_delivery_are_kept():
    """在途投递未定局的会话不回收（真收到的回复会缺席 scoped 记忆）。"""
    sessions = {}
    for i in range(SESSION_HARD_LIMIT + 5):
        sessions[f"group:{i}"] = _session_data(
            memory_enabled=False, idle_seconds=SESSION_REAP_MIN_IDLE_SECONDS * 10,
        )
    pending = {f"group:{i}" for i in range(5)}   # 前 5 条在途
    svc, discarded, _ = _reaper_plugin(sessions, pending=pending)
    asyncio.run(svc.reap_stale_sessions())
    assert not (set(discarded) & pending), "回收了在途投递的会话"


def test_reap_is_bounded_per_sweep():
    """单轮回收条数有上限 —— 一次全清会把事件循环卡住。"""
    sessions = {}
    for i in range(SESSION_HARD_LIMIT * 2):
        sessions[f"group:{i}"] = _session_data(
            memory_enabled=False, idle_seconds=SESSION_REAP_MIN_IDLE_SECONDS * 10,
        )
    svc, discarded, _ = _reaper_plugin(sessions)
    reaped = asyncio.run(svc.reap_stale_sessions())
    assert reaped == SESSION_REAP_MAX_PER_SWEEP


def test_oldest_idle_sessions_are_reaped_first():
    """先回收最久没动的（按空闲时长降序），而不是任意顺序。"""
    sessions = {}
    # 构造"刚好超限 2 条"：2000 条刚热过的 + 2 条最老的。
    # ⚠️ 总数必须是 LIMIT + 2 —— 只放 LIMIT 条时根本不触发容量闸（踩过一次）。
    for i in range(SESSION_HARD_LIMIT):
        sessions[f"group:recent{i}"] = _session_data(
            memory_enabled=False, idle_seconds=SESSION_REAP_MIN_IDLE_SECONDS + 10,
        )
    sessions["group:oldest"] = _session_data(
        memory_enabled=False, idle_seconds=SESSION_REAP_MIN_IDLE_SECONDS + 9000,
    )
    sessions["group:older"] = _session_data(
        memory_enabled=False, idle_seconds=SESSION_REAP_MIN_IDLE_SECONDS + 5000,
    )
    assert len(sessions) == SESSION_HARD_LIMIT + 2, "前提：总数必须超过上限"
    svc, discarded, _ = _reaper_plugin(sessions)
    reaped = asyncio.run(svc.reap_stale_sessions())
    assert reaped == 2, f"应只回收超出的 2 条，实际 {reaped}"
    assert set(discarded) == {"group:oldest", "group:older"}, (
        f"应先回收最老的，实际 {sorted(discarded)}"
    )


def test_single_failure_does_not_stop_the_rest():
    """单条回收抛错不影响其余（清扫循环整体的健壮性要求）。"""
    sessions = {}
    for i in range(SESSION_HARD_LIMIT + 3):
        sessions[f"group:{i}"] = _session_data(
            memory_enabled=False, idle_seconds=SESSION_REAP_MIN_IDLE_SECONDS * 10,
        )
    svc, discarded, log = _reaper_plugin(sessions)

    async def _flaky(session_key, reason=""):
        if len(discarded) == 0:
            discarded.append(session_key)
            raise RuntimeError("模拟单条失败")
        discarded.append(session_key)
        sessions.pop(session_key, None)
        return True

    svc.discard_session = _flaky  # type: ignore[method-assign]
    reaped = asyncio.run(svc.reap_stale_sessions())
    assert reaped == 2, f"失败一条后应继续回收其余，实际 reaped={reaped}"
    assert any("[Reap]" in m and "失败" in m for m in log.warnings)
