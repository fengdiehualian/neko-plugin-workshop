from __future__ import annotations

import ast
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.support.entry_flow import make_install_plugin, make_phase2_plugin, shared_state
from tests.support.memory_flow import _session_state
from tests.support.plugin_runtime_stubs import install_plugin_runtime_stubs

install_plugin_runtime_stubs()

package = import_module("market_plugins.galgame_plugin")
plugin_core = import_module("market_plugins.galgame_plugin.plugin_core")
sdk = import_module("plugin.sdk.plugin")
Ok = sdk.Ok
package.build_summarize_context = plugin_core.build_summarize_context
package.install_textractor = plugin_core.install_textractor


def test_entry_declarations_preserve_public_ids_and_rapidocr_versions() -> None:
    root = Path(package.__file__).parent / "plugin_entries"
    ids: set[str] = set()
    rapidocr_versions: set[str] = set()
    for path in root.glob("galgame_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Name):
                    continue
                if decorator.func.id != "plugin_entry":
                    continue
                kwargs = {item.arg: item.value for item in decorator.keywords if item.arg}
                entry_id = ast.literal_eval(kwargs["id"])
                ids.add(entry_id)
                if entry_id == "galgame_set_rapidocr_lang":
                    schema = ast.literal_eval(kwargs["input_schema"])
                    rapidocr_versions.update(schema["properties"]["ocr_version"]["enum"])
    assert len(ids) == 39
    assert {"galgame_bind_game", "galgame_agent_command", "galgame_get_story_so_far"} <= ids
    assert {"4", "5"} <= rapidocr_versions


def test_public_surface_registers_list_action_and_static_ui() -> None:
    tree = ast.parse(Path(plugin_core.__file__).read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]
    static_call = next(node for node in calls if node.func.attr == "register_static_ui")
    list_call = next(node for node in calls if node.func.attr == "set_list_actions")
    assert ast.literal_eval(static_call.args[0]) == "static"
    action_node = list_call.args[0].elts[0]
    action = {
        ast.literal_eval(key): ast.literal_eval(value)
        for key, value in zip(action_node.keys, action_node.values, strict=True)
        if not isinstance(value, ast.JoinedStr)
    }
    assert action == {"id": "open_ui", "kind": "ui", "open_in": "new_tab"}


@pytest.mark.asyncio
async def test_install_textractor_returns_result_and_refreshed_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install_root = tmp_path / "Textractor Installed"
    plugin = make_install_plugin(tmp_path, install_root=install_root)
    seen: dict[str, object] = {}

    async def fake_install(**kwargs: object):
        seen.update(kwargs)
        install_root.mkdir(parents=True)
        executable = install_root / "TextractorCLI.exe"
        executable.write_text("", encoding="utf-8")
        return {"installed": True, "summary": "Textractor 安装完成", "detected_path": str(executable)}

    monkeypatch.setattr(package, "install_textractor", fake_install, raising=False)
    result = await plugin.galgame_install_textractor()
    assert isinstance(result, Ok)
    assert result.value["install_result"]["installed"] is True
    assert result.value["status"]["textractor"]["installed"] is True
    assert seen["install_target_dir_raw"] == str(install_root)


@pytest.mark.asyncio
async def test_install_textractor_uses_ctx_run_id_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = make_install_plugin(tmp_path, install_root=tmp_path / "Textractor")
    seen: dict[str, object] = {}

    async def fake_install(**kwargs: object):
        seen.update(kwargs)
        return {"installed": True, "summary": "ok"}

    monkeypatch.setattr(package, "install_textractor", fake_install, raising=False)
    result = await plugin.galgame_install_textractor(_ctx={"run_id": "run-123"})
    assert isinstance(result, Ok)
    assert seen["task_id"] == "run-123"


@pytest.mark.asyncio
async def test_phase2_entries_return_structured_degraded_results(tmp_path: Path) -> None:
    snapshot = _session_state(text="Current line", scene_id="scene-a", line_id="line-1", choices=[{"choice_id": "c1", "text": "Yes", "index": 0}], is_menu_open=True)
    plugin = make_phase2_plugin(tmp_path, shared=shared_state(snapshot=snapshot, history_lines=[{"text": "Current line", "line_id": "line-1", "scene_id": "scene-a"}], history_choices=list(snapshot["choices"])))
    explain = await plugin.galgame_explain_line()
    summarize = await plugin.galgame_summarize_scene()
    suggest = await plugin.galgame_suggest_choice()
    status = await plugin.galgame_agent_command(action="query_status")
    assert all(isinstance(item, Ok) for item in (explain, summarize, suggest, status))
    assert explain.value["degraded"] is True
    assert summarize.value["scene_id"] == "scene-a"
    assert suggest.value["choices"] == []
    assert status.value["action"] == "query_status"


@pytest.mark.asyncio
async def test_agent_list_messages_sanitizes_limit(tmp_path: Path) -> None:
    plugin = make_phase2_plugin(tmp_path)
    limits: list[int] = []

    class Agent:
        async def list_messages(self, _shared: object, *, direction: str, limit: int):
            limits.append(limit)
            return {"direction": direction, "limit": limit}

    plugin._game_agent = Agent()
    await plugin.galgame_agent_command(action="list_messages", limit="bad")
    await plugin.galgame_agent_command(action="list_messages", limit=-20)
    await plugin.galgame_agent_command(action="list_messages", limit=9999)
    assert limits == [50, 1, 500]


@pytest.mark.asyncio
async def test_get_history_sanitizes_limit_and_include_events(tmp_path: Path) -> None:
    plugin = make_phase2_plugin(tmp_path, shared=shared_state(history_events=[{"seq": 1}], history_lines=[{"line_id": "a"}], history_observed_lines=[{"line_id": "a"}]))
    result = await plugin.galgame_get_history(limit="bad", include_events="false")
    numeric_false = await plugin.galgame_get_history(limit=20, include_events=0)
    assert result.value["events"] == []
    assert "observed_lines" in result.value
    assert numeric_false.value["events"] == []


@pytest.mark.asyncio
async def test_story_so_far_uses_existing_scene_summaries(tmp_path: Path) -> None:
    plugin = make_phase2_plugin(tmp_path)
    plugin._game_agent = SimpleNamespace(_scene_tracker=SimpleNamespace(scene_memory=[{"scene_id": "a", "summary": "first summary", "push_seq": 7}, {"scene_id": "b", "summary": "second summary", "push_seq": 11}]))
    result = await plugin.galgame_get_story_so_far()
    assert result.value["available"] is True
    assert "first summary" in result.value["story_so_far"]
    assert result.value["last_updated_seq"] == 11


@pytest.mark.asyncio
async def test_story_so_far_keeps_newer_recorded_summary(tmp_path: Path) -> None:
    plugin = make_phase2_plugin(tmp_path)
    plugin._game_agent = SimpleNamespace(_scene_tracker=SimpleNamespace(scene_memory=[{"scene_id": "a", "summary": "old summary", "push_seq": 7}]))
    plugin._record_story_progress_from_scene_summary(scene_id="a", summary="new summary", push_seq=12)
    result = await plugin.galgame_get_story_so_far()
    assert "new summary" in result.value["story_so_far"]
    assert "old summary" not in result.value["story_so_far"]


@pytest.mark.asyncio
async def test_story_so_far_refreshes_zero_seq_summaries(tmp_path: Path) -> None:
    plugin = make_phase2_plugin(tmp_path)
    memory = [{"scene_id": "a", "summary": "first", "push_seq": 0}]
    plugin._game_agent = SimpleNamespace(_scene_tracker=SimpleNamespace(scene_memory=memory))
    first = await plugin.galgame_get_story_so_far()
    plugin._query_rate_limits["galgame_get_story_so_far"].clear()
    memory.append({"scene_id": "b", "summary": "second", "push_seq": 0})
    second = await plugin.galgame_get_story_so_far()
    assert "first" in first.value["story_so_far"]
    assert "second" in second.value["story_so_far"]
    assert second.value["last_updated_seq"] == 0


@pytest.mark.asyncio
async def test_continue_auto_advance_calls_mode_then_agent(tmp_path: Path) -> None:
    plugin = make_phase2_plugin(tmp_path)
    calls: list[tuple[str, object]] = []

    async def set_mode(**kwargs: object):
        calls.append(("mode", kwargs))
        return Ok({"mode": "choice_advisor", "skipped": True})

    async def command(**kwargs: object):
        calls.append(("agent", kwargs))
        return Ok({"action": "send_message", "result": "resumed", "status": {}})

    plugin.galgame_set_mode = set_mode
    plugin.galgame_agent_command = command
    result = await plugin.galgame_continue_auto_advance(message="继续")
    assert [name for name, _ in calls] == ["mode", "agent"]
    assert result.value["mode_result"]["success"] is True
    assert result.value["agent_result"]["action"] == "send_message"


@pytest.mark.asyncio
async def test_continue_auto_advance_propagates_agent_degraded(tmp_path: Path) -> None:
    plugin = make_phase2_plugin(tmp_path)
    plugin.galgame_set_mode = lambda **_k: _async_ok({"mode": "choice_advisor"})
    plugin.galgame_agent_command = lambda **_k: _async_ok({"action": "send_message", "degraded": True})
    result = await plugin.galgame_continue_auto_advance(message="继续")
    assert result.value["degraded"] is True


@pytest.mark.asyncio
async def test_continue_auto_advance_preserves_already_applied_mode_schema(tmp_path: Path) -> None:
    plugin = make_phase2_plugin(tmp_path)
    mode = {"mode": "choice_advisor", "push_notifications": True, "advance_speed": "medium", "skipped": True, "skip_reason": "already_applied"}
    plugin.galgame_set_mode = lambda **_k: _async_ok(mode)
    plugin.galgame_agent_command = lambda **_k: _async_ok({"action": "send_message", "status": {}})
    result = await plugin.galgame_continue_auto_advance()
    assert result.value["mode_result"]["result"] == mode


class SuccessfulGateway:
    async def explain_line(self, context: dict[str, object]):
        return {"explanation": "ok", "evidence": []}

    async def summarize_scene(self, context: dict[str, object]):
        return {"summary": "ok", "key_points": []}

    async def suggest_choice(self, context: dict[str, object]):
        choices = list(context.get("visible_choices") or [])
        return {"choices": [{**choices[0], "rank": 1, "reason": "ok"}] if choices else []}


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["memory_reader", "ocr_reader"])
async def test_phase2_entries_mark_weaker_input_degraded(tmp_path: Path, source: str) -> None:
    prefix = "ocr:" if source == "ocr_reader" else "mem:"
    snapshot = _session_state(speaker="Yukino", text="line", scene_id=f"{prefix}scene-a", line_id=f"{prefix}line-1", choices=[{"choice_id": f"{prefix}c1", "text": "yes", "index": 0}], is_menu_open=True)
    line = {"speaker": "Yukino", "text": "line", "line_id": f"{prefix}line-1", "scene_id": f"{prefix}scene-a"}
    plugin = make_phase2_plugin(tmp_path, shared=shared_state(snapshot=snapshot, history_lines=[line], history_observed_lines=[line], history_choices=list(snapshot["choices"]), active_data_source=source))
    plugin._llm_gateway = SuccessfulGateway()
    if source == "ocr_reader":
        plugin._state.ocr_reader_runtime = {"enabled": True, "status": "active"}
    results = [await plugin.galgame_explain_line(), await plugin.galgame_summarize_scene(), await plugin.galgame_suggest_choice()]
    for result in results:
        assert result.value["degraded"] is True
        assert "input_source" in result.value, result.value
        assert result.value["input_source"] == source
        assert result.value["semantic_degraded"] is True
        assert result.value["fallback_used"] is False


async def _async_ok(value: dict[str, object]):
    return Ok(value)
