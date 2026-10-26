"""验证 plugin_entry 自动路由入口注册（v0.6.22 核心修复）。

背景：bili_viewer 此前只有 llm_tool、无任何 plugin_entry，导致 TaskExecutor
的 UserPlugin 评估看到 entries 为空，判定"无支持发送弹幕的插件"，YUI 永远
无法路由发弹幕。本测试锁定 run 与 bili_danmaku 两个入口注册可见。

与 test_bili.py 一致：插件模块在函数内导入（conftest.py 注入 sys.path）。
"""

from __future__ import annotations


def _plugin_cls():
    from plugin.plugins.bili_viewer import BiliViewerPlugin

    return BiliViewerPlugin


def _entry_metas():
    from plugin.sdk.shared.constants import EVENT_META_ATTR

    cls = _plugin_cls()
    return {name: getattr(getattr(cls, name), EVENT_META_ATTR) for name in ("entry_run", "entry_danmaku")}


def test_plugin_entry_registered() -> None:
    """类上必须注册 run 与 bili_danmaku 两个 plugin_entry（事件类型 plugin_entry）。"""
    metas = _entry_metas()
    assert all(getattr(m, "event_type", None) == "plugin_entry" for m in metas.values())
    assert [getattr(m, "id", None) for m in metas.values()] == ["run", "bili_danmaku"]


def test_plugin_entry_input_schema() -> None:
    """入口必须暴露弹幕相关参数（room/bvid/text），供评估 LLM 生成参数。"""
    meta = _entry_metas()["entry_danmaku"]
    schema = dict(getattr(meta, "input_schema", None) or {})
    props = schema.get("properties") or {}
    assert "room" in props and "bvid" in props and "text" in props
    assert "required" in schema


def test_plugin_entry_kind_action() -> None:
    """入口 kind 必须为 action，才会被 TaskExecutor 视为 Agent 可见。"""
    for meta in _entry_metas().values():
        assert getattr(meta, "kind", None) == "action"


def test_entry_danmaku_routes_to_video() -> None:
    """bili_danmaku 入口：给 bvid 时路由到视频弹幕，返回标准 output 结构。"""
    import asyncio

    plugin = object.__new__(_plugin_cls())
    calls = []

    async def fake_video(bvid="", text="", progress=0, **_):
        calls.append(("video", bvid, text, progress))
        return {"output": f"已向视频 {bvid} 发送弹幕：{text}", "is_error": False}

    async def fake_live(room="", text="", **_):
        calls.append(("live", room, text))
        return {"output": f"已向直播间 {room} 发送弹幕：{text}", "is_error": False}

    plugin.bili_video_send_danmaku = fake_video
    plugin.bili_live_send_danmaku = fake_live

    result = asyncio.run(plugin.entry_danmaku(bvid="BV1xx", text="喵", progress=5))
    assert calls == [("video", "BV1xx", "喵", 5)]
    assert result["is_error"] is False


def test_entry_danmaku_routes_to_live() -> None:
    """bili_danmaku 入口：给 room 时路由到直播弹幕。"""
    import asyncio

    plugin = object.__new__(_plugin_cls())
    calls = []

    async def fake_video(bvid="", text="", progress=0, **_):
        calls.append(("video", bvid, text, progress))
        return {"output": "", "is_error": False}

    async def fake_live(room="", text="", **_):
        calls.append(("live", room, text))
        return {"output": f"已向直播间 {room} 发送弹幕：{text}", "is_error": False}

    plugin.bili_video_send_danmaku = fake_video
    plugin.bili_live_send_danmaku = fake_live

    result = asyncio.run(plugin.entry_danmaku(room="123456", text="冲鸭"))
    assert calls == [("live", "123456", "冲鸭")]
    assert result["is_error"] is False


def test_entry_danmaku_no_target() -> None:
    """无 room/bvid 时返回可读错误，不抛异常。"""
    import asyncio

    plugin = object.__new__(_plugin_cls())
    plugin.bili_video_send_danmaku = lambda **_: None
    plugin.bili_live_send_danmaku = lambda **_: None

    result = asyncio.run(plugin.entry_danmaku(text="喵"))
    assert result["is_error"] is True
    assert result["error"] == "NO_TARGET"


def test_entry_run_dispatches_danmaku() -> None:
    """run 入口：action=send_video_danmaku 路由到视频弹幕（含 parameters 兼容）。"""
    import asyncio

    plugin = object.__new__(_plugin_cls())
    calls = []

    async def fake_video(bvid="", text="", progress=0, **_):
        calls.append(("video", bvid, text, progress))
        return {"output": f"ok {bvid}", "is_error": False}

    plugin.bili_video_send_danmaku = fake_video

    result = asyncio.run(
        plugin.entry_run(
            action="send_video_danmaku",
            parameters={"bvid": "BV1ab", "text": "来了", "progress": 3},
        )
    )
    assert calls == [("video", "BV1ab", "来了", 3)]
    assert result["is_error"] is False


def test_collect_entries_contains_plugin_entries() -> None:
    """宿主执行侧必须能在 collect_entries() 中看到 run/bili_danmaku。

    回归：v0.6.22 把入口方法命名为 _entry_run/_entry_danmaku（下划线前缀），
    宿主 collect_entries()（plugin/sdk/shared/core/base.py）会跳过所有
    `_` 前缀实例方法，导致 TaskExecutor 评估路由成功但执行侧报
    "Entry 'run' not found"（发弹幕链路断裂）。本测试锁定修复：
    方法名必须避开下划线前缀，且 id 以 plugin_entry(id=...) 为准。
    """
    plugin = object.__new__(_plugin_cls())
    plugin._dynamic_entries = {}
    plugin._routers = []
    collected = plugin.collect_entries(wrap_with_hooks=True)
    assert "run" in collected
    assert "bili_danmaku" in collected
    assert collected["run"].meta.id == "run"
    assert collected["bili_danmaku"].meta.id == "bili_danmaku"
    assert collected["bili_danmaku"].meta.event_type == "plugin_entry"


def test_entry_run_fallback_no_action_with_bvid() -> None:
    """兜底①：LLM 未给 action 但显式给了 bvid+text → 自动发视频弹幕。"""
    import asyncio

    plugin = object.__new__(_plugin_cls())
    calls = []

    async def fake_video(bvid="", text="", progress=0, **_):
        calls.append(("video", bvid, text, progress))
        return {"output": f"ok {bvid}", "is_error": False}

    plugin.bili_video_send_danmaku = fake_video
    plugin._ext_tab = lambda **kw: None

    result = asyncio.run(plugin.entry_run(bvid="BV1yy", text="mft"))
    assert calls == [("video", "BV1yy", "mft", 0)]
    assert result["is_error"] is False


def test_entry_run_fallback_senses_current_video() -> None:
    """兜底②：无 action 且无显式目标，感知当前正在看的视频并自动发送。"""
    import asyncio

    plugin = object.__new__(_plugin_cls())
    calls = []

    async def fake_video(bvid="", text="", progress=0, **_):
        calls.append(("video", bvid, text, progress))
        return {"output": f"ok {bvid}", "is_error": False}

    plugin.bili_video_send_danmaku = fake_video
    plugin._ext_tab = lambda **kw: {
        "url": "https://www.bilibili.com/video/BV1cur",
        "title": "当前视频标题",
        "at": 9999999999.0,
    }

    result = asyncio.run(plugin.entry_run(text="mft"))
    assert calls == [("video", "BV1cur", "mft", 0)]
    assert result["is_error"] is False


def test_entry_run_fallback_senses_current_live() -> None:
    """兜底②：感知当前正在看的直播并自动发送直播弹幕。"""
    import asyncio

    plugin = object.__new__(_plugin_cls())
    calls = []

    async def fake_live(room="", text="", **_):
        calls.append(("live", room, text))
        return {"output": f"ok {room}", "is_error": False}

    plugin.bili_live_send_danmaku = fake_live
    plugin._ext_tab = lambda **kw: {
        "url": "https://live.bilibili.com/88888",
        "title": "直播间",
        "at": 9999999999.0,
    }

    result = asyncio.run(plugin.entry_run(text="mft"))
    assert calls == [("live", "88888", "mft")]
    assert result["is_error"] is False


def test_entry_run_fallback_no_target_no_crash() -> None:
    """无 action、无目标、无可感知内容 → 返回 NO_ACTION 而非 KeyError（v0.6.23 回归）。"""
    import asyncio

    plugin = object.__new__(_plugin_cls())
    plugin._ext_tab = lambda **kw: None

    result = asyncio.run(plugin.entry_run(text="mft"))
    assert result["is_error"] is True
    assert result["error"] == "NO_ACTION"


def test_llm_tool_video_send_senses_current_video() -> None:
    """llm_tool 版发视频弹幕：LLM 未给 bvid 时感知当前视频自动补齐（v0.6.25）。"""
    import asyncio

    from plugin.plugins.bili_viewer import bili

    plugin = object.__new__(_plugin_cls())
    calls = []

    def fake_send(bvid="", text="", progress_ms=0, account="main"):
        calls.append(("video", bvid, text, progress_ms))
        return {"ok": True, "bvid": bvid, "cid": 1, "text": text, "progress_ms": progress_ms, "server": ""}

    orig = bili.send_video_danmaku
    bili.send_video_danmaku = fake_send
    plugin._ext_tab = lambda **kw: {
        "url": "https://www.bilibili.com/video/BV1llm",
        "title": "Kards 视频",
        "at": 9999999999.0,
    }
    try:
        result = asyncio.run(plugin.bili_video_send_danmaku(text="mft"))
    finally:
        bili.send_video_danmaku = orig
    assert calls == [("video", "BV1llm", "mft", 0)]
    assert result["is_error"] is False


def test_llm_tool_video_send_no_sense_keeps_error() -> None:
    """llm_tool 版发视频弹幕：无可感知视频时保持 NO_BVID 错误。"""
    import asyncio

    plugin = object.__new__(_plugin_cls())
    plugin._ext_tab = lambda **kw: None

    result = asyncio.run(plugin.bili_video_send_danmaku(text="mft"))
    assert result["is_error"] is True
    assert result["error"] == "NO_BVID"


def test_llm_tool_live_send_senses_current_live() -> None:
    """llm_tool 版发直播弹幕：LLM 未给 room 时感知当前直播自动补齐（v0.6.25）。"""
    import asyncio

    from plugin.plugins.bili_viewer import bili

    plugin = object.__new__(_plugin_cls())
    calls = []

    def fake_send(room="", text="", account="main"):
        calls.append(("live", room, text))
        return {"ok": True, "room_id": int(room), "text": text, "server": ""}

    orig = bili.live_send_danmaku
    bili.live_send_danmaku = fake_send
    plugin._ext_tab = lambda **kw: {
        "url": "https://live.bilibili.com/66666",
        "title": "直播间",
        "at": 9999999999.0,
    }
    try:
        result = asyncio.run(plugin.bili_live_send_danmaku(text="mft"))
    finally:
        bili.live_send_danmaku = orig
    assert calls == [("live", "66666", "mft")]
    assert result["is_error"] is False


def test_llm_tool_live_send_no_sense_keeps_error() -> None:
    """llm_tool 版发直播弹幕：无可感知直播时保持 NO_ROOM 错误。"""
    import asyncio

    plugin = object.__new__(_plugin_cls())
    plugin._ext_tab = lambda **kw: None

    result = asyncio.run(plugin.bili_live_send_danmaku(text="mft"))
    assert result["is_error"] is True
    assert result["error"] == "NO_ROOM"


def test_send_result_recorded_on_success() -> None:
    """发送成功后 last_send 诊断字段被记录（面板只读可查，不含 cookie）。"""
    import asyncio

    from plugin.plugins.bili_viewer import bili

    plugin = object.__new__(_plugin_cls())
    orig = bili.send_video_danmaku

    def fake_send(bvid="", text="", progress_ms=0, account="main"):
        return {"ok": True, "bvid": bvid, "cid": 1, "text": text, "progress_ms": progress_ms, "server": ""}

    bili.send_video_danmaku = fake_send
    plugin._ext_tab = lambda **kw: None
    try:
        result = asyncio.run(plugin.bili_video_send_danmaku(bvid="BV1ok", text="hi"))
    finally:
        bili.send_video_danmaku = orig
    assert result["is_error"] is False
    rec = plugin._last_send_result
    assert rec["ok"] is True
    assert rec["kind"] == "video"
    assert rec["target"] == "BV1ok"


def test_send_result_recorded_on_no_bvid() -> None:
    """感知不到视频导致 NO_BVID 时，last_send 记录 error=NO_BVID（区分参数失败与 API 拒绝）。"""
    import asyncio

    plugin = object.__new__(_plugin_cls())
    plugin._ext_tab = lambda **kw: None

    result = asyncio.run(plugin.bili_video_send_danmaku(text="mft"))
    assert result["error"] == "NO_BVID"
    rec = plugin._last_send_result
    assert rec["ok"] is False
    assert rec["error"] == "NO_BVID"
