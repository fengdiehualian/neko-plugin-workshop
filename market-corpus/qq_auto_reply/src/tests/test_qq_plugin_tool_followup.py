"""异步插件任务的**结果回投**：她说"结果出来我告诉你"时，这条链路要真的兑现。

真机教训（§4.0ag-1）：`writer_power_analysis:analyze_text` 当场只回
`{"task_id": …, "status": "queued"}`，而插件会话一轮只走一次工具轮 —— 她那句
"等结果出来我第一时间告诉你"**没有任何东西会兑现**。使用者 16:3x 拍板要把它做成真的。

这个功能的风险全在"她**主动**说话"上，所以每一道闸、每一条"不许撒谎"的路径都要钉住：

* 四道闸：开关 / 值班 / 通道 / 上限（外加"同一会话只等一件"）；
* 只投一次（键 = 插件 + task_id），投递成功才出队；
* 值班停了、通道换了 → **留着不说**，等条件恢复再发（而不是丢掉或硬发）；
* 三种终态三套说法：**成功有正文** / **失败** / **跟丢了（查不到、超时、说完成却没正文）**
  —— 任何一条都不许拿"成功"去糊。
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import time
from types import SimpleNamespace

import pytest
from plugin.plugins.qq_auto_reply import pipeline_models
from plugin.plugins.qq_auto_reply.plugin_tool_followup_service import (
    ERRORS_BEFORE_LOST,
    FIRST_DELAY_SECONDS,
    MAX_PENDING,
    MAX_WAIT_SECONDS,
    PROMPT_RESULT_MAX_CHARS,
    QQPluginToolFollowupService,
    extract_result,
    normalize_status,
    status_token,
)

_PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[1]

_CONVO = {
    "sender_id": "u_openid_1",
    "is_group": False,
    "group_id": "",
    "user_nickname": "测试者",
    "permission_level": "admin",
    "use_memory_context": True,
    "persist_memory": True,
    "ephemeral_session": False,
    "group_scene_mode": "",
}

_GROUP_CONVO = {
    "sender_id": "u_openid_1",
    "is_group": True,
    "group_id": "g_openid_9",
    "user_nickname": "测试者",
    "permission_level": "normal",
    "use_memory_context": False,
    "persist_memory": False,
    "ephemeral_session": False,
    "group_scene_mode": "group_collective",
}


# ── 夹具 ────────────────────────────────────────────────────────────

def _err(message: str) -> SimpleNamespace:
    """一次"查询失败"（宿主那边返回 Err）。"""
    return SimpleNamespace(value=None, error=message, is_err=lambda: True)


def _ok(payload) -> SimpleNamespace:
    return SimpleNamespace(value=payload, is_err=lambda: False)


def _plugin(
    tmp_path,
    *,
    poll_results=(),
    probe_ok=True,
    running=True,
    enabled=True,
    mode="open_platform",
    admin_qq="10001",
    reply_text="（她说的话）",
    extra_settings=None,
):
    """最小插件桩：只带服务真正用到的那几样。

    ``poll_results`` 是**登记之后**轮询依次要拿到的东西：dict = 结果，
    ``_err("…")`` = 查询失败；用完了就一直返回 ``running``（免得测试因为多跑一拍
    就"完成"）。登记入口自己会先问一次"这个 id 查得到进度吗"（`probe_ok=False`
    模拟查不到进度的那种插件，如 `music_pusher:create_schedule_task` 那类假阳性）。
    """
    script = list(poll_results)
    calls: list[tuple] = []
    requests: list = []
    probed: set[tuple[str, str]] = set()

    class _Plugins:
        @staticmethod
        async def call_entry(entry_ref, params=None, *, timeout=10.0):
            calls.append((entry_ref, params, timeout))
            task_id = str((params or {}).get("task_id") or "")
            marker = (entry_ref, task_id)
            if marker not in probed:
                probed.add(marker)
                if not probe_ok:
                    # 像"排程任务"那样：查得到，但返回的东西里没有进度词
                    return _ok({"settings": {"enabled": True}, "message": "获取成功"})
                return _ok({"task_id": task_id, "status": "running"})
            step = script.pop(0) if script else {"status": "running"}
            if isinstance(step, BaseException):
                raise step
            return step

    class _Pipeline:
        async def run(self, request):
            requests.append(request)
            return SimpleNamespace(reply_text=reply_text, traces=[])

    client = SimpleNamespace()
    if mode == "open_platform":
        client.CHANNEL = "open"
        client.mode = "open_platform"
    else:
        client.CHANNEL = "onebot"
        client.mode = "napcat"

    emitted: list[tuple[str, str]] = []
    logged: list[str] = []
    outcomes: list = []
    settings = {"qq_open_plugin_followup_enabled": enabled}
    settings.update(extra_settings or {})
    plugin = SimpleNamespace(
        plugins=_Plugins(),
        qq_client=client,
        reply_pipeline=_Pipeline(),
        runtime_service=SimpleNamespace(record_pipeline_outcome=lambda **kw: outcomes.append(kw)),
        _qq_settings=settings,
        _running=running,
        _admin_qq=admin_qq,
        logger=SimpleNamespace(
            info=lambda msg, *a, **k: logged.append(str(msg)),
            warning=lambda msg, *a, **k: logged.append(str(msg)),
        ),
        _emit_log=lambda level, msg: emitted.append((level, msg)),
        data_path=lambda name: tmp_path / name,
        calls=calls,
        requests=requests,
        emitted=emitted,
        logged=logged,
        outcomes=outcomes,
    )
    plugin.plugin_tool_followup_service = QQPluginToolFollowupService(plugin)
    return plugin


def _service(plugin) -> QQPluginToolFollowupService:
    return plugin.plugin_tool_followup_service


def _remember(plugin, *, task_id="t1", poller="get_analysis_status", convo=None, plugin_id="writer"):
    """走**登记入口**（`register`）：它会先实测"这个 id 查得到进度吗"。

    所以夹具的 ``poll_results`` 里第一条会被这次确认吃掉 —— 与生产一致。
    """
    return asyncio.run(_service(plugin).register(
        plugin_id=plugin_id,
        entry_id="analyze_text",
        field="task_id",
        task_id=task_id,
        poller=poller,
        conversation=dict(convo if convo is not None else _CONVO),
    ))


async def _tick(plugin, times=1, *, age_off=0.0):
    """走 ``times`` 轮 tick。默认把记录的时间往前拨过 FIRST_DELAY，免得等 4 秒。"""
    service = _service(plugin)
    for record in service._pending.values():
        record["created_at"] -= FIRST_DELAY_SECONDS + 1.0 + age_off
    for _ in range(times):
        await service.tick()


def _tick_sync(plugin, times=1, **kw):
    return asyncio.run(_tick(plugin, times, **kw))


# ── 状态归一化这类纯函数 ────────────────────────────────────────────

@pytest.mark.parametrize("token,expected", [
    ("done", "done"), ("SUCCESS", "done"), ("succeeded", "done"), ("finished", "done"),
    ("error", "failed"), ("failed", "failed"), ("cancelled", "failed"), ("timed-out", "failed"),
    ("queued", "running"), ("running", "running"), ("in_progress", "running"), ("pending", "running"),
    ("", "unknown"), ("wibble", "unknown"), (None, "unknown"),
])
def test_status_tokens_are_normalised_by_meaning(token, expected):
    """别的插件用什么词我们管不了，只能按**语义**归一化（多认几个词不会说谎）。"""
    assert normalize_status(token) == expected


def test_status_token_reads_the_first_known_key():
    assert status_token({"status": "queued"}) == "queued"
    assert status_token({"state": "done"}) == "done"
    assert status_token({"task_status": "running"}) == "running"
    assert status_token({"whatever": "done"}) == ""
    assert status_token("done") == ""


def test_extract_result_prefers_the_body_and_drops_bookkeeping():
    assert extract_result({"status": "done", "result": {"summary": "他写得很啰嗦"}}) == \
        json.dumps({"summary": "他写得很啰嗦"}, ensure_ascii=False)
    assert extract_result({"status": "done", "task_id": "t1", "model": "m"}) == ""
    assert extract_result({"status": "done", "custom": "只有这个"}) == json.dumps(
        {"custom": "只有这个"}, ensure_ascii=False)
    assert extract_result("就是一段话") == "就是一段话"


def test_extract_result_is_capped():
    text = extract_result({"result": "字" * (PROMPT_RESULT_MAX_CHARS + 500)})
    assert len(text) < PROMPT_RESULT_MAX_CHARS + 60
    assert "已截断" in text


def test_the_followup_result_is_masked_before_it_reaches_her_prompt(tmp_path):
    """回投的结果会进她的 prompt，她随后当着群里的人说 —— 密钥形状先掩掉。"""
    plugin = _plugin(tmp_path, poll_results=[_ok({
        "status": "done",
        "result": {"api_key": "sk-abcdef123456", "summary": "节奏太慢"},
    })])
    _remember(plugin)

    _tick_sync(plugin, 2)

    text = plugin.requests[0].message_text
    assert "abcdef123456" not in text, "密钥被塞进她的 prompt 了"
    assert "节奏太慢" in text, "正文还在"


def test_extract_result_masks_secret_shapes_in_text():
    assert "149a" not in extract_result({"result": "your api key: ****149a is invalid"})
    assert "invalid" in extract_result({"result": "your api key: ****149a is invalid"})


# ── 闸 1：开关 ──────────────────────────────────────────────────────

def test_the_switch_defaults_to_on(tmp_path):
    plugin = _plugin(tmp_path)
    del plugin._qq_settings["qq_open_plugin_followup_enabled"]
    assert _service(plugin).enabled() is True, "使用者要的功能默认开"


def test_a_disabled_switch_blocks_registration(tmp_path):
    plugin = _plugin(tmp_path, enabled=False)
    assert _remember(plugin) is False
    assert _service(plugin).pending_count() == 0


def test_a_disabled_switch_blocks_delivery_of_something_already_pending(tmp_path):
    """已经登记了（比如登记之后才关的开关）：不许再发，但仍然留着、不撒谎。"""
    plugin = _plugin(tmp_path, poll_results=[_ok({"status": "done", "result": "结论"})])
    assert _remember(plugin) is True
    plugin._qq_settings["qq_open_plugin_followup_enabled"] = False
    _tick_sync(plugin, 2)

    assert plugin.requests == []
    assert _service(plugin).pending_count() == 1


# ── 登记：能登记才允许承诺 ──────────────────────────────────────────

def test_remember_needs_a_poller_entry(tmp_path):
    """没有"查状态"的 entry 就永远不知道结果什么时候到 —— 不许登记。

    （判据只在 `register` 里判一次：两处各判一次的话，拆掉任何一处都测不出来。）
    """
    plugin = _plugin(tmp_path)
    assert _remember(plugin, poller="") is False
    assert _service(plugin).pending_count() == 0


def test_remember_needs_a_deliverable_conversation(tmp_path):
    plugin = _plugin(tmp_path)
    assert _remember(plugin, convo={**_CONVO, "sender_id": ""}) is False
    assert _remember(plugin, convo={**_GROUP_CONVO, "group_id": ""}) is False


def test_a_task_we_cannot_watch_is_not_registered(tmp_path):
    """**假阳性防线**：`music_pusher:create_schedule_task` 那种"排程任务"也回
    `{task_id, status}`，但它永远没有"完成"那一刻。查不到进度就不登记 ——
    否则过一会儿她会跟使用者说一句"那个任务跟丢了"，假警报比不说更糟。
    """
    plugin = _plugin(tmp_path, probe_ok=False)

    assert _remember(plugin) is False
    assert _service(plugin).pending_count() == 0


def test_a_probe_echoing_a_different_task_is_refused(tmp_path):
    """查到的是**别的**任务（很多插件对未知 id 会回默认对象）：也当查不到。"""
    plugin = _plugin(tmp_path)

    async def _other_task(entry_ref, params=None, *, timeout=10.0):
        return _ok({"task_id": "somebody-else", "status": "running"})

    plugin.plugins.call_entry = _other_task

    assert _remember(plugin) is False
    assert _service(plugin).pending_count() == 0


def test_remember_is_idempotent_per_task(tmp_path):
    plugin = _plugin(tmp_path)
    assert _remember(plugin, task_id="t1") is True
    assert _remember(plugin, task_id="t1") is False
    assert _service(plugin).pending_count() == 1


def test_a_second_task_in_the_same_conversation_is_refused(tmp_path):
    """一个会话同时只等一件事 —— 否则结果一到就会连发好几条。"""
    plugin = _plugin(tmp_path)
    assert _remember(plugin, task_id="t1") is True
    assert _remember(plugin, task_id="t2") is False
    assert _remember(plugin, task_id="t3", convo={**_CONVO, "sender_id": "u2"}) is True


def test_pending_cap_is_enforced(tmp_path):
    plugin = _plugin(tmp_path)
    for index in range(MAX_PENDING):
        assert _remember(plugin, task_id=f"t{index}", convo={**_CONVO, "sender_id": f"u{index}"}) is True
    assert _remember(plugin, task_id="overflow", convo={**_CONVO, "sender_id": "u99"}) is False
    assert _service(plugin).pending_count() == MAX_PENDING


def test_registering_persists_to_disk(tmp_path):
    plugin = _plugin(tmp_path)
    _remember(plugin)
    saved = json.loads((tmp_path / "plugin_tool_followups.json").read_text(encoding="utf-8"))
    assert [row["key"] for row in saved["pending"]] == ["writer:t1"]


def test_the_key_decisions_go_into_the_file_log_too(tmp_path):
    """登记与回投都要**双写文件日志** —— ring 在插件重载时清空，事后就没法复盘了。"""
    plugin = _plugin(tmp_path, poll_results=[_ok({"status": "done", "result": "结论"})])
    _remember(plugin)
    _tick_sync(plugin, 2)

    assert any("已登记异步任务" in line for line in plugin.logged), "登记没进文件日志"
    assert any("已回投到" in line for line in plugin.logged), "回投没进文件日志"


def test_state_survives_a_restart_and_stale_rows_are_dropped(tmp_path):
    """宿主重启后还要接着等 —— 否则那句承诺连日志都没有就没了。"""
    plugin = _plugin(tmp_path)
    _remember(plugin, task_id="fresh")
    _remember(plugin, task_id="stale", convo={**_CONVO, "sender_id": "u2"})
    state_path = tmp_path / "plugin_tool_followups.json"
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    for row in raw["pending"]:
        if row["task_id"] == "stale":
            row["created_at"] = time.time() - MAX_WAIT_SECONDS - 10
    state_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    fresh_plugin = _plugin(tmp_path)

    assert [row["task_id"] for row in _service(fresh_plugin).snapshot()] == ["fresh"]


# ── 轮询 → 回投 ─────────────────────────────────────────────────────

def test_a_running_task_is_waited_on(tmp_path):
    plugin = _plugin(tmp_path, poll_results=[_ok({"status": "queued"})])
    _remember(plugin)

    _tick_sync(plugin)

    snapshot = _service(plugin).snapshot()
    assert plugin.requests == [], "还没出结果就不该说话"
    assert snapshot[0]["status"] == "queued"
    assert snapshot[0]["attempts"] == 1


def test_a_done_task_is_delivered_once_with_the_result(tmp_path):
    plugin = _plugin(tmp_path, poll_results=[
        _ok({"status": "running"}),
        _ok({"status": "done", "result": "他这篇的问题是节奏太慢"}),
    ])
    _remember(plugin)

    _tick_sync(plugin, 3)

    assert len(plugin.requests) == 1, "结果到了就要说，而且只说一次"
    request = plugin.requests[0]
    assert request.source_kind == pipeline_models.KIND_PLUGIN_TOOL_RESULT
    assert request.force_reply is True
    assert request.is_group is False
    assert request.sender_id == "u_openid_1"
    assert "他这篇的问题是节奏太慢" in request.message_text
    assert _service(plugin).pending_count() == 0
    assert plugin.outcomes, "回投也要记进运行时统计（与主动发言同口径）"


def test_the_prompt_forbids_talking_about_plugin_internals(tmp_path):
    """她得用自己的话说结论，不能把 JSON / 系统词念出来。"""
    plugin = _plugin(tmp_path, poll_results=[_ok({"status": "done", "result": "结论原文"})])
    _remember(plugin)
    _tick_sync(plugin, 2)

    text = plugin.requests[0].message_text
    assert "结论原文" in text
    for word in ("插件", "系统", "后台", "task_id"):
        assert word in text, f"约束里该点名 {word}（要告诉她别说这个）"
    assert "告诉他" in text


def test_a_group_followup_goes_back_to_the_same_group(tmp_path):
    plugin = _plugin(tmp_path, poll_results=[_ok({"status": "done", "result": "结论"})])
    _remember(plugin, convo=_GROUP_CONVO)

    _tick_sync(plugin, 2)

    request = plugin.requests[0]
    assert request.is_group is True
    assert request.group_id == "g_openid_9"
    assert request.sender_id == "10001", "群聊回投借管理员的名义（与主动群发言同口径）"
    assert request.permission_level_override == "trusted"
    assert request.group_scene_mode == "group_collective"


def test_the_followup_turn_keeps_the_original_memory_policy(tmp_path):
    """回投是同一场对话的下一轮，不是另起一个临时会话。"""
    plugin = _plugin(tmp_path, poll_results=[_ok({"status": "done", "result": "结论"})])
    _remember(plugin)
    _tick_sync(plugin, 2)

    request = plugin.requests[0]
    assert request.use_memory_context is True
    assert request.persist_memory is True
    assert request.ephemeral_session is False


def test_a_failed_task_says_it_failed(tmp_path):
    plugin = _plugin(tmp_path, poll_results=[
        _ok({"status": "error", "error": "缺少 API key"}),
    ])
    _remember(plugin)

    _tick_sync(plugin, 2)

    assert len(plugin.requests) == 1
    text = plugin.requests[0].message_text
    assert "失败了" in text
    assert "缺少 API key" in text
    assert "现在结果回来了" not in text, "失败不许被说成成功"


def test_a_completed_task_without_a_body_is_not_reported_as_success(tmp_path):
    """说是完成了却没正文：不能编一段交差 —— 按"跟丢了"处理。"""
    plugin = _plugin(tmp_path, poll_results=[_ok({"status": "done", "task_id": "t1"})])
    _remember(plugin)

    _tick_sync(plugin, 2)

    text = plugin.requests[0].message_text
    assert "没能跟到最后" in text
    assert "现在结果回来了" not in text, "没有正文就不许说结果回来了"


@pytest.mark.parametrize("failure", [
    _err("未找到任务: t1"),
    RuntimeError("插件重启中"),
])
def test_repeated_query_failures_end_as_lost_not_as_success(tmp_path, failure):
    plugin = _plugin(tmp_path, poll_results=[failure] * ERRORS_BEFORE_LOST)
    _remember(plugin)

    _tick_sync(plugin, ERRORS_BEFORE_LOST)

    assert len(plugin.requests) == 1, f"{ERRORS_BEFORE_LOST} 次查不到就该说一声"
    text = plugin.requests[0].message_text
    assert "没能跟到最后" in text
    assert "现在结果回来了" not in text, "查不到不许说成结果到了"


def test_a_single_query_failure_is_not_fatal(tmp_path):
    """单次失败可能只是插件正在重启 —— 别急着把承诺判死。"""
    plugin = _plugin(tmp_path, poll_results=[
        _err("暂时问不到"),
        _ok({"status": "done", "result": "结论"}),
    ])
    _remember(plugin)

    _tick_sync(plugin, 1)
    assert plugin.requests == []
    assert _service(plugin).pending_count() == 1

    _tick_sync(plugin, 1)
    assert len(plugin.requests) == 1
    assert "结论" in plugin.requests[0].message_text


def test_waiting_too_long_ends_as_timeout(tmp_path):
    plugin = _plugin(tmp_path, poll_results=[_ok({"status": "running"})] * 4)
    _remember(plugin)

    _tick_sync(plugin, 2, age_off=MAX_WAIT_SECONDS + 10)

    assert len(plugin.requests) == 1
    text = plugin.requests[0].message_text
    assert "没能跟到最后" in text, "等超时也要按「没跟上」说，不是成功"
    assert "现在结果回来了" not in text


# ── 闸 2/3：值班与通道 ──────────────────────────────────────────────

def test_a_stopped_duty_holds_the_result_back(tmp_path):
    """值班停了就**不说话**（停着还主动发消息是越权），但结果留着，等恢复再发。"""
    plugin = _plugin(tmp_path, running=False, poll_results=[_ok({"status": "done", "result": "结论"})])
    _remember(plugin)

    _tick_sync(plugin, 2)

    assert plugin.requests == []
    assert _service(plugin).pending_count() == 1, "值班停了不等于把承诺丢了"

    plugin._running = True
    _tick_sync(plugin, 1)

    assert len(plugin.requests) == 1


def test_the_onebot_channel_also_delivers_the_result(tmp_path):
    """OneBot 通道也回投（与工具桥同一条闸；使用者 2026-10-02 把闸放开到 OneBot）。

    OneBot 侧还比开放平台简单：`_conversation_target` 拿的 `sender_id` / `group_id`
    就是 QQ 号与群号，不需要 openid 映射。
    """
    plugin = _plugin(tmp_path, mode="napcat", poll_results=[_ok({"status": "done", "result": "结论"})])
    _remember(plugin)

    _tick_sync(plugin, 2)

    assert len(plugin.requests) == 1
    assert _service(plugin).pending_count() == 0


def test_no_channel_object_holds_the_result_back(tmp_path):
    """闸不是恒真：没有连接对象时不主动说话，结果留着（下次再试）。"""
    plugin = _plugin(tmp_path, poll_results=[_ok({"status": "done", "result": "结论"})])
    plugin.qq_client = None
    _remember(plugin)

    _tick_sync(plugin, 2)

    assert plugin.requests == []
    assert _service(plugin).pending_count() == 1


def test_a_speaker_who_left_the_roster_is_not_written_to(tmp_path):
    """说话人已经不在名册里了（权限 none）：私聊不投。"""
    plugin = _plugin(tmp_path, poll_results=[_ok({"status": "done", "result": "结论"})] * 3)
    _remember(plugin, convo={**_CONVO, "permission_level": "none"})

    _tick_sync(plugin, 3)

    assert plugin.requests == []
    assert _service(plugin).pending_count() == 1


def test_an_empty_generation_does_not_consume_the_result(tmp_path):
    """她没生成出内容：不算发过，留着下次再试。"""
    plugin = _plugin(
        tmp_path, reply_text="", poll_results=[_ok({"status": "done", "result": "结论"})] * 3,
    )
    _remember(plugin)

    _tick_sync(plugin, 3)

    assert len(plugin.requests) >= 2, "每次都该再试一次"
    assert _service(plugin).pending_count() == 1


def test_the_delivery_result_decides_not_the_reply_text(tmp_path):
    """"生成了"不等于"送出去了"：投递管线说 delivered=False 就还没兑现。"""
    plugin = _plugin(tmp_path, poll_results=[_ok({"status": "done", "result": "结论"})] * 3)
    plugin.reply_pipeline.run = _outcome_pipeline(
        plugin, reply_text="（说了话）", delivered=False,
    )
    _remember(plugin)

    _tick_sync(plugin, 3)

    assert _service(plugin).pending_count() == 1, "没送到就不许出队"


def test_a_delivered_result_is_popped(tmp_path):
    """对照：投递管线说送到了 → 出队（证明上面那条不是因为别的原因此刻不出队）。"""
    plugin = _plugin(tmp_path, poll_results=[_ok({"status": "done", "result": "结论"})])
    plugin.reply_pipeline.run = _outcome_pipeline(
        plugin, reply_text="（说了话）", delivered=True,
    )
    _remember(plugin)

    _tick_sync(plugin, 2)

    assert _service(plugin).pending_count() == 0


def test_the_delivering_marker_is_persisted_before_the_send(tmp_path):
    """「正要发」的记号必须在**发之前**落盘 —— 否则"发出去了但没记账"就会重发。"""
    plugin = _plugin(tmp_path, poll_results=[_ok({"status": "done", "result": "结论"})])
    state_path = tmp_path / "plugin_tool_followups.json"
    seen: list[bool] = []

    async def _run(request):
        saved = json.loads(state_path.read_text(encoding="utf-8"))
        seen.append(bool(saved["pending"][0].get("delivering_at")))
        return SimpleNamespace(reply_text="（说了话）", history_ai_row=None)

    plugin.reply_pipeline.run = _run
    _remember(plugin)
    _tick_sync(plugin, 2)

    assert seen == [True], "发之前状态文件里就该有记号"


def test_a_restart_does_not_re_send_something_that_was_already_going_out(tmp_path):
    """在那一步挂掉 → 重启后放弃它（可能已经发出去了），而不是再发一遍。"""
    plugin = _plugin(tmp_path)
    _remember(plugin, task_id="inflight")
    state_path = tmp_path / "plugin_tool_followups.json"
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    raw["pending"][0]["delivering_at"] = time.time()
    raw["pending"][0]["terminal"] = "done"
    raw["pending"][0]["output"] = "结论"
    state_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    restarted = _plugin(tmp_path)

    assert _service(restarted).pending_count() == 0
    assert restarted.requests == []


def test_a_failed_send_clears_the_marker_so_a_restart_retries(tmp_path):
    """没送出去那一趟要把记号撤掉 —— 否则重启会把它当成"已发"，结果永远送不到。"""
    plugin = _plugin(tmp_path, reply_text="", poll_results=[_ok({"status": "done", "result": "结论"})])
    _remember(plugin)

    _tick_sync(plugin, 2)

    saved = json.loads((tmp_path / "plugin_tool_followups.json").read_text(encoding="utf-8"))
    assert "delivering_at" not in saved["pending"][0]


def _outcome_pipeline(plugin, *, reply_text, delivered):
    """一个返回**带 delivery_result** 的 outcome 的管线桩。"""

    async def _run(request):
        plugin.requests.append(request)
        return SimpleNamespace(
            reply_text=reply_text,
            delivery_result=SimpleNamespace(delivered=delivered, target_type="private", target_id="u"),
        )

    return _run


# ── 与工具桥的接线 ──────────────────────────────────────────────────

def test_a_registered_task_lets_her_promise_a_followup(tmp_path):
    """登记上了才允许她说"出来了我告诉你" —— 否则不许承诺。"""
    plugin = _plugin(tmp_path)
    service = _bridge_service(plugin)

    note = service._async_followup_note(
        {"task_id": "t9", "status": "queued"},
        plugin_id="writer", poller="get_analysis_status", registered=True,
    )

    assert "系统会再喊你一次" in note
    assert "不要承诺「结果出来我主动告诉你」" not in note
    assert "不要承诺具体时间" in note


def test_registration_failure_keeps_the_old_no_promise_wording(tmp_path):
    """登记不上（没配查状态的 entry）：沿用上一轮的口径，**不要承诺**。"""
    plugin = _plugin(tmp_path)
    service = _bridge_service(plugin)

    note = service._async_followup_note(
        {"task_id": "t9", "status": "queued"},
        plugin_id="writer", poller="", registered=False,
    )

    assert "不要承诺「结果出来我主动告诉你」" in note
    assert "t9" in note, "要给出让对方再问一次时怎么查"


def test_the_note_only_allows_promising_when_registration_really_succeeded(tmp_path):
    """`registered` 必须是**实测**出来的那一个，不是照着 task_id 的形状猜的。

    这条钉住接线本身：没登记上却给了"允许承诺"的措辞 = 又回到真机上那个空话。
    """
    plugin = _plugin(tmp_path, probe_ok=False)
    service = _bridge_service(plugin)
    payload = {"task_id": "t9", "status": "pending"}

    registered = asyncio.run(service._register_followup(
        payload, plugin_id="writer", entry_id="create_schedule_task",
        poller="get_push_settings", conversation=dict(_CONVO),
    ))
    note = service._async_followup_note(
        payload, plugin_id="writer", poller="get_push_settings", registered=registered,
    )

    assert registered is False
    assert "系统会再喊你一次" not in note
    assert "不要承诺「结果出来我主动告诉你」" in note


def test_a_sync_result_gets_no_note_and_no_registration(tmp_path):
    plugin = _plugin(tmp_path)
    service = _bridge_service(plugin)

    note = service._async_followup_note(
        {"answer": "42"}, plugin_id="web_search", poller="get_status", registered=False,
    )

    assert note == ""
    assert _service(plugin).pending_count() == 0


def _bridge_service(plugin):
    """工具桥服务（只为了调那两个与回投接线的方法）。"""
    from plugin.plugins.qq_auto_reply.plugin_tool_service import QQPluginToolService

    return QQPluginToolService(plugin)


def test_the_followup_turn_does_not_mount_plugin_tools(tmp_path):
    """结果回投那一轮**不再挂插件工具**：否则一个异步结果能生出下一个异步任务，跑成环。"""
    from plugin.plugins.qq_auto_reply.reply_generation_service import QQReplyGenerationService

    plugin = _plugin(tmp_path)
    plugin._qq_settings["qq_open_plugin_tools"] = {"web_search": "all"}
    plugin.plugin_tool_service = _bridge_service(plugin)
    plugin.plugin_tool_service._candidates_cache = (time.monotonic(), [
        {"plugin_id": "web_search", "name": "搜索", "entries": ["search"], "running": True,
         "entry_hints": {}, "required_params": [], "optional_params": []},
    ])
    generation = QQReplyGenerationService(plugin)

    mounted, tools = asyncio.run(generation._build_bridge_tools(SimpleNamespace(
        permission_level="admin", source_kind=pipeline_models.KIND_PLUGIN_TOOL_RESULT,
    )))
    assert (mounted, tools) == ([], [])

    # 对照：普通一轮照样挂（证明上面那条不是"因为别的原因恰好没挂"）
    mounted, tools = asyncio.run(generation._build_bridge_tools(SimpleNamespace(
        permission_level="admin", source_kind=pipeline_models.KIND_INCOMING_GROUP,
    )))
    assert [tool.name for tool in tools] == ["plugin_web_search"]


def test_the_conversation_handed_to_the_bridge_carries_what_delivery_needs():
    """回投要用的会话身份必须从那一轮**真的带出来**（漏一个字段就等于投不出去）。"""
    from plugin.plugins.qq_auto_reply.reply_generation_service import QQReplyGenerationService

    context = SimpleNamespace(
        sender_id="u1", is_group=True, group_id="g1", user_nickname="甲",
        permission_level="normal", use_memory_context=False, persist_memory=False,
        ephemeral_session=False, group_scene_mode="group_collective",
    )
    convo = QQReplyGenerationService._bridge_conversation(context)

    assert convo == {
        "sender_id": "u1", "is_group": True, "group_id": "g1", "user_nickname": "甲",
        "permission_level": "normal", "use_memory_context": False, "persist_memory": False,
        "ephemeral_session": False, "group_scene_mode": "group_collective",
    }


def test_the_handler_registers_the_task_and_wires_it_to_the_bridge(tmp_path):
    """端到端（桥这一侧）：handler 拿到 task_id 就登记，并把"会不会回投"告诉提示词。"""
    from plugin.plugins.qq_auto_reply.plugin_tool_service import QQPluginToolService

    plugin = _plugin(tmp_path)
    bridge = QQPluginToolService(plugin)
    plugin.plugin_tool_service = bridge
    calls = []

    async def _call_entry(entry_ref, params=None, *, timeout=10.0):
        calls.append((entry_ref, params))
        if entry_ref.endswith("analyze_text"):
            return _ok({"task_id": "t42", "status": "queued"})
        return _ok({"status": "running"})

    plugin.plugins.call_entry = _call_entry
    mounted = [(
        {"plugin_id": "writer", "name": "writer", "entries": ["analyze_text", "get_analysis_status"]},
        "all",
    )]
    handler = bridge.build_handler(mounted, session_key="private:u1", conversation=dict(_CONVO))

    result = asyncio.run(handler(SimpleNamespace(
        call_id="c1", name="plugin_writer",
        arguments={"entry_id": "analyze_text", "params": {"text": "..."}},
    )))

    assert [row["plugin_id"] for row in _service(plugin).snapshot()] == ["writer"]
    assert "系统会再喊你一次" in result.output
    assert calls and calls[0][0] == "writer:analyze_text"


# ── 给提示词用的「你手上还没交的活」─────────────────────────────────────

def test_pending_items_are_reported_for_the_own_conversation_only(tmp_path):
    """只报**这条会话**在等的事：群里在等的活不该出现在私聊提示词里（反之亦然）。"""
    plugin = _plugin(tmp_path)
    _remember(plugin, task_id="t1")
    _remember(plugin, task_id="t2", convo={**_GROUP_CONVO})
    _remember(plugin, task_id="t3", convo={**_GROUP_CONVO, "group_id": "g_other"})

    private = _service(plugin).pending_items_for(is_group=False, sender_id="u_openid_1")
    group = _service(plugin).pending_items_for(is_group=True, group_id="g_openid_9")
    other = _service(plugin).pending_items_for(is_group=True, group_id="g_other")

    assert len(private) == 1 and "已等" in private[0]
    assert len(group) == 1
    assert len(other) == 1
    # 隔离性：**没在这条会话里等过**的，一个都不该报（去掉会话过滤这条会红）
    assert _service(plugin).pending_items_for(is_group=False, sender_id="someone_else") == []
    assert _service(plugin).pending_items_for(is_group=True, group_id="g_none") == []


def test_pending_items_are_capped_and_ordered_by_age(tmp_path):
    """进了提示词的行**必须封顶**。

    注意：`MAX_PENDING_PER_CONVERSATION=1` 时，每个会话本来就只等一件，这个封顶平时够不着
    —— 所以这里**直接往 `_pending` 里塞三条**（模拟"将来把每会话上限放开"或状态文件被手工改过），
    否则这条闸是死代码、拆掉它没有任何测试会红（第一版就是这么写的，变异证据直接把它抓出来了）。
    """
    plugin = _plugin(tmp_path)
    service = _service(plugin)
    now = time.time()
    for index in range(3):
        key = f"writer:t{index}"
        service._pending[key] = {
            "key": key,
            "plugin_id": "writer",
            "entry_id": "analyze_text",
            "field": "task_id",
            "task_id": f"t{index}",
            "poller": "get_analysis_status",
            "conversation": dict(_CONVO),
            "created_at": now - (10 - index),  # t0 最老
            "attempts": 0,
            "errors": 0,
            "status": "queued",
            "terminal": "",
            "output": "",
            "last_block_reason": "",
            "last_block_at": 0.0,
        }

    rows = service.pending_items_for(is_group=False, sender_id="u_openid_1")

    from plugin.plugins.qq_auto_reply.plugin_tool_followup_service import PENDING_PROMPT_MAX_ITEMS

    assert PENDING_PROMPT_MAX_ITEMS == 2
    assert len(rows) == PENDING_PROMPT_MAX_ITEMS, f"没封顶：{rows}"
    assert "已等 10s" in rows[0], f"没按最老的排：{rows}"


def test_pending_items_are_empty_without_a_conversation(tmp_path):
    plugin = _plugin(tmp_path)
    _remember(plugin)
    assert _service(plugin).pending_items_for(is_group=False, sender_id="") == []
    assert _service(plugin).pending_items_for(is_group=True, group_id="") == []


# ── 界面/查询要看得见 ───────────────────────────────────────────────

def test_the_snapshot_reports_what_she_is_waiting_on(tmp_path):
    plugin = _plugin(tmp_path)
    _remember(plugin, task_id="t1")
    _remember(plugin, task_id="t2", convo={**_GROUP_CONVO})

    rows = _service(plugin).snapshot()

    assert {row["task_id"] for row in rows} == {"t1", "t2"}
    group_row = next(row for row in rows if row["task_id"] == "t2")
    assert group_row["is_group"] is True
    assert group_row["conversation"] == "群 g_openid_9"
    assert group_row["status"] == "queued"
    assert group_row["poller"] == "get_analysis_status"
    assert "age_seconds" in group_row


def test_the_plugin_tools_query_exposes_the_pending_followups():
    """界面那条 query 要带上"还在等什么"，否则使用者只会以为"又没下文了"。"""
    source = (_PLUGIN_DIR / "__init__.py").read_text(encoding="utf-8")
    for token in ('"pending_followups"', '"followups_enabled"', '"max_pending_followups"'):
        assert token in source, f"plugin_tools 的返回里少了 {token}"
    assert "plugin_tool_followup_service" in source, "服务没被挂到插件上"
    assert "followups.ensure_loop()" in source, "常驻 loop 里没把它拉起来"


def test_the_new_source_kind_is_synthetic_and_skips_the_buffer():
    """它必须算"名义发言人"（否则会拿别人的成员记忆去公开发言），且不再进缓冲。"""
    kind = pipeline_models.KIND_PLUGIN_TOOL_RESULT
    assert pipeline_models.is_synthetic_source(kind) is True
    assert kind in pipeline_models.BUFFER_INTERNAL_SOURCE_KINDS


# ── 界面契约 ────────────────────────────────────────────────────────

def _module_text() -> str:
    """「插件」页的界面本体在两个面板共用的 `static/plugin-tools.js` 里。

    2026-10-02 之前它只存在于 `open_platform.html`；放开 OneBot 通道之后两页共用一份。
    """
    return (_PLUGIN_DIR / "static" / "plugin-tools.js").read_text(encoding="utf-8")


def test_the_page_has_a_switch_that_saves_the_real_key():
    """她主动说话这条路必须能一键关掉 —— 而且关的得是后端真正读的那个键。"""
    text = _module_text()
    assert 'id="pt-followup-enabled"' in text, "页面上没有这个开关"
    assert "qq_open_plugin_followup_enabled" in text, "开关没有保存到后端读的那个键上"
    assert "ptSetFollowup(" in text, "开关没有接上保存函数"


def test_the_page_shows_what_she_is_still_waiting_on():
    """否则使用者只会以为"又没下文了" —— 得能看见还在等什么。"""
    text = _module_text()
    assert 'id="pt-followups"' in text, "页面上没有正在等结果那一行"
    assert "pending_followups" in text, "没有用后端给的待办清单"
    assert "followups_enabled" in text, "开关没有回显后端的当前值"


def test_the_followup_labels_exist_in_both_bundles():
    import json

    base = _PLUGIN_DIR / "i18n"
    zh = json.loads((base / "zh-CN.json").read_text(encoding="utf-8"))
    en = json.loads((base / "en.json").read_text(encoding="utf-8"))
    for required in ("followup_enabled", "followup_hint", "followup_pending", "followup_waited"):
        key = f"ui.openplat.plugintools.{required}"
        assert key in zh, f"中文 bundle 缺 {key}"
        assert key in en, f"英文 bundle 缺 {key}"
