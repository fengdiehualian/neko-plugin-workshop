from __future__ import annotations

import asyncio
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.support.agent_flow import (
    FakeHostAdapter,
    FakeLLMGateway,
    drain_summary_tasks,
    shared_state,
)
from tests.support.memory_flow import (
    _Ctx,
    _Logger,
    _make_effective_config,
    _make_plugin_dirs,
    _session_state,
)
from tests.support.plugin_runtime_stubs import install_plugin_runtime_stubs

install_plugin_runtime_stubs()

package = import_module("market_plugins.galgame_plugin")
plugin_core = import_module("market_plugins.galgame_plugin.plugin_core")
game_llm_agent_module = import_module("market_plugins.galgame_plugin.game_llm_agent")
sdk_module = import_module("plugin.sdk.plugin")
GalgamePlugin = plugin_core.GalgamePlugin
GameLLMAgent = game_llm_agent_module.GameLLMAgent
Ok = sdk_module.Ok


def _plugin(tmp_path: Path):
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    return GalgamePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root)))


def test_commit_state_preserves_private_context_snapshot_on_public_poll_snapshot(
    tmp_path: Path,
) -> None:
    plugin = _plugin(tmp_path)
    private_snapshot = {
        "scene_id": "scene-a",
        "game_id": "game-a",
        "route_id": "route-a",
        "summary_seed": "saved seed",
        "stable_line_ids": ["line-1", "line-2"],
        "saved_at": 123.0,
    }
    with plugin._state_lock:
        plugin._state.context_snapshot = dict(private_snapshot)
    payload = plugin._snapshot_state(fresh=True)
    assert "summary_seed" not in payload["context_snapshot"]
    assert "stable_line_ids" not in payload["context_snapshot"]
    plugin._commit_state(payload)
    with plugin._state_lock:
        assert plugin._state.context_snapshot["summary_seed"] == "saved seed"
        assert plugin._state.context_snapshot["stable_line_ids"] == ["line-1", "line-2"]


@pytest.mark.asyncio
async def test_summarize_scene_treats_context_snapshot_persist_as_best_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Gateway:
        async def summarize_scene(self, context: dict[str, object]):
            del context
            return {"summary": "summary ok"}

    context = {
        "scene_id": "scene-a",
        "recent_lines": [{"speaker": "A", "text": "line."}],
        "current_snapshot": {"text": "line."},
    }
    monkeypatch.setattr(
        package, "build_summarize_context", lambda *_a, **_k: context, raising=False
    )
    plugin = GalgamePlugin.__new__(GalgamePlugin)
    plugin._llm_gateway = Gateway()
    plugin._snapshot_state = lambda **_kwargs: {}
    plugin._cfg = SimpleNamespace()
    plugin._persist_context_snapshot_from_summary = lambda *_a: (_ for _ in ()).throw(
        RuntimeError("store unavailable")
    )
    plugin.logger = _Logger()
    result = await plugin.galgame_summarize_scene()
    assert isinstance(result, Ok)
    assert result.value["summary"] == "summary ok"
    assert result.value["scene_id"] == "scene-a"


def test_commit_state_skips_json_copy_when_payload_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = _plugin(tmp_path)
    plugin._commit_state(plugin._snapshot_state(fresh=True))
    cached_snapshot = plugin._snapshot_state()
    payload = plugin._snapshot_state(fresh=True)
    monkeypatch.setattr(
        package,
        "json_copy",
        lambda value: (_ for _ in ()).throw(AssertionError(f"unexpected copy: {value!r}")),
        raising=False,
    )
    plugin._commit_state(payload)
    assert plugin._state_dirty is False
    assert plugin._cached_snapshot is cached_snapshot


def test_commit_state_only_copies_changed_mutable_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = _plugin(tmp_path)
    plugin._commit_state(plugin._snapshot_state(fresh=True))
    plugin._snapshot_state()
    payload = plugin._snapshot_state(fresh=True)
    payload["last_error"] = {"kind": "warning", "message": "changed"}
    copied: list[object] = []

    def tracking(value: object):
        copied.append(value)
        return dict(value) if isinstance(value, dict) else list(value) if isinstance(value, list) else value

    monkeypatch.setattr(package, "json_copy", tracking, raising=False)
    plugin._commit_state(payload)
    assert copied == [{"kind": "warning", "message": "changed"}]
    assert plugin._state.last_error == {"kind": "warning", "message": "changed"}
    assert plugin._state_dirty is True
    assert plugin._cached_snapshot is None


def test_agent_reply_context_preserves_condensed_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = _plugin(tmp_path)
    agent = GameLLMAgent(plugin=plugin, logger=_Logger(), llm_gateway=FakeLLMGateway(), host_adapter=FakeHostAdapter())
    monkeypatch.setattr(game_llm_agent_module, "_compute_dynamic_line_limit", lambda *_a, **_k: 2)
    shared = shared_state(
        snapshot=_session_state(scene_id="scene-a", line_id="line-current"),
        history_lines=[{"speaker": "雪乃", "text": "第一句\n第二句\n第三句", "line_id": "s1", "scene_id": "scene-a", "_condensed_line_ids": ["s1", "s2", "s3"], "_condensed_count": 3}],
        history_observed_lines=[{"speaker": "雪乃", "text": "候选一句\n候选二句", "line_id": "o1", "scene_id": "scene-a", "_condensed_line_ids": ["o1", "o2"], "_condensed_count": 2}],
    )
    public = agent._build_agent_reply_context(shared, prompt="status")["public_context"]
    assert public["stable_lines"][0]["_condensed_count"] == 3
    assert public["observed_lines"][0]["_condensed_count"] == 2
    assert game_llm_agent_module._context_line_count(public["stable_lines"]) == 3
    assert all("_condensed_count" not in line for line in public["recent_lines"])


@pytest.mark.asyncio
async def test_agent_scene_summary_counts_condensed_stable_lines(tmp_path: Path) -> None:
    plugin = _plugin(tmp_path)
    agent = GameLLMAgent(plugin=plugin, logger=_Logger(), llm_gateway=FakeLLMGateway(), host_adapter=FakeHostAdapter())
    shared = shared_state(mode="companion", snapshot=_session_state(speaker="雪乃", text="第 8 句台词。", scene_id="scene-a", line_id="line-8", ts="2026-04-21T08:33:08Z"))
    agent._runtime_loop = asyncio.get_running_loop()
    agent._op_lock = asyncio.Lock()
    agent._observed_session_id = str(shared["active_session_id"])
    agent._observed_scene_id = "scene-a"
    agent._schedule_scene_summary_task(
        shared=shared, session_id="sess-a", scene_id="scene-a", route_id="",
        snapshot=dict(shared["latest_snapshot"]),
        context={"scene_id": "scene-a", "route_id": "", "stable_lines": [{"line_id": "line-1", "speaker": "雪乃", "text": "\n".join(f"第 {i} 句台词。" for i in range(1, 9)), "scene_id": "scene-a", "route_id": "", "ts": "2026-04-21T08:33:08Z", "_condensed_line_ids": [f"line-{i}" for i in range(1, 9)], "_condensed_count": 8}], "observed_lines": [], "recent_choices": []},
        trigger="line_count", metadata={"context_type": "galgame_scene_context", "trigger": "line_count", "scheduled_from_event_seq": 0, "last_line_seq": 0}, scheduled_line_count=8,
    )
    await drain_summary_tasks(agent)
    assert agent._last_delivered_summary_key == "scene-a:0:8"
    assert agent._summary_debug["last_task_finished"]["stable_line_count"] == 8
    assert any(item.get("scene_id") == "scene-a" and item.get("summary") for item in agent._scene_memory)
