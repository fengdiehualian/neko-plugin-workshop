import json
import logging
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from plugin.sdk.plugin import Err


@pytest.mark.parametrize("patch,field", [
    ({"server": {"host": "secret.invalid", "connection_type": "local"}}, "server.connection_type"),
    ({"server": {"username": "SECRET!"}}, "server.username"),
    ({"server": {"port": 0}}, "server.port"),
    ({"operator": {"port": 80}}, "operator.port"),
    ({"viewer": {"renderer_directory": "SECRET"}}, "viewer.renderer_directory"),
])
def test_rejected_save_preserves_state(plugin_instance, run_async, monkeypatch, patch, field):
    original = {"server": {"username": "MyAccount"}}
    dump = AsyncMock(return_value=original)
    update = AsyncMock()
    monkeypatch.setattr(plugin_instance.config, "dump", dump)
    monkeypatch.setattr(plugin_instance.config, "update", update)
    generation = plugin_instance._generation
    result = run_async(plugin_instance.save_settings(patch))
    assert isinstance(result, Err)
    assert result.error["field"] == field
    assert "SECRET" not in json.dumps(result.error)
    assert update.await_count == 0 and plugin_instance._generation == generation
    assert plugin_instance._configuration_writes == 0 and plugin_instance._worker is None
    assert original == {"server": {"username": "MyAccount"}}


def test_dashboard_metadata_does_not_hash_backend(plugin_instance, plugin_module, run_async, monkeypatch):
    def forbid(*args, **kwargs):
        raise AssertionError("dashboard must not hash full runtime")
    monkeypatch.setattr(plugin_module, "verify_runtime", forbid)
    result = run_async(plugin_instance.dashboard())
    assert result["installation"]["scope"] == "metadata_only_not_runtime_or_render_verification"
    assert result["installation"]["viewer"] == "text_only"
    assert plugin_instance._worker is None


@pytest.mark.parametrize("patch,field", [
    ({"operator": {"port": 80}}, "operator.port"),
    ({"viewer": {"renderer_directory": "SECRET"}}, "viewer.renderer_directory"),
])
def test_hosted_rejection_survives_real_host_serialization(plugin_instance, run_async, monkeypatch, patch, field, sdk_root):
    update = AsyncMock()
    monkeypatch.setattr(plugin_instance.config, "update", update)
    generation = plugin_instance._generation
    if (sdk_root / "plugin/core/entry_executor.py").is_file():
        # Newer hosts route all entry results through the shared executor.
        # Exercise that real codec without starting the host or a game client.
        from plugin.core.entry_executor import EntryExecutor, execute_entry_command

        context = SimpleNamespace(
            _handler_scope=lambda _: nullcontext(),
            _run_scope=lambda _: nullcontext(),
            _lanlan_scope=lambda _: nullcontext(),
        )
        executor = EntryExecutor(
            plugin_id="minecraft_autonomous", context=context, instance=plugin_instance,
            entries={"save_settings_hosted": plugin_instance.save_settings_hosted},
            metadata={}, events={}, default_timeout=3,
            refresh=lambda: None, should_persist=lambda _: False, persist=None,
            logger=logging.getLogger(__name__),
        )
        transport = run_async(execute_entry_command(executor, {
            "req_id": "settings-rejection", "entry_id": "save_settings_hosted",
            "args": {"settings": patch},
        }))
    else:
        import ast

        # Older hosts normalize Result in host.py itself.
        host_source = sdk_root / "plugin/core/host.py"
        tree = ast.parse(host_source.read_text(encoding="utf-8"))
        branches = [node for node in ast.walk(tree) if isinstance(node, ast.If)
                    and ast.unparse(node.test) == "hasattr(result, 'is_ok') and callable(result.is_ok)"
                    and "ret['data'] = result.value" in ast.unparse(node)]
        assert branches
        result = run_async(plugin_instance.save_settings_hosted(patch))
        scope = {"result": result, "ret": {}}
        for branch in branches:
            exec(compile(ast.Module(body=[branch], type_ignores=[]), str(host_source), "exec"), scope)
        transport = scope["ret"]
    transport = json.loads(json.dumps(transport))
    assert transport["success"] is True
    hosted_response = {"result": transport["data"]}
    assert hosted_response["result"]["success"] is False
    feedback = hosted_response["result"]["error"]
    assert feedback["field"] == field and feedback["message"]
    assert "SECRET" not in json.dumps(hosted_response)
    assert update.await_count == 0 and plugin_instance._generation == generation
    assert plugin_instance._worker is None and plugin_instance._configuration_writes == 0


@pytest.mark.parametrize("preference", ["balanced", "local-first", "remote-first"])
def test_dashboard_full_draft_roundtrip_preserves_memory_and_typed_rejection(
    plugin_instance, run_async, monkeypatch, preference
):
    from copy import deepcopy

    from agent_mc_autonomy.configuration import Settings
    from plugin.sdk.plugin import Ok

    original = {} if preference == "balanced" else {"memory": {"read_preference": preference}}
    dump = AsyncMock(return_value=original)
    update = AsyncMock()
    monkeypatch.setattr(plugin_instance.config, "dump", dump)
    monkeypatch.setattr(plugin_instance.config, "update", update)
    state = run_async(plugin_instance.dashboard())
    draft = state["settings"]
    assert draft["memory"]["read_preference"] == preference
    generation = plugin_instance._generation
    invalid = deepcopy(draft)
    invalid["operator"]["port"] = 80
    rejected = run_async(plugin_instance.save_settings_hosted(invalid))
    assert isinstance(rejected, Ok)
    assert rejected.value["success"] is False
    assert rejected.value["error"]["code"] == "operator_port_unprivileged"
    assert rejected.value["error"]["field"] == "operator.port"
    assert update.await_count == 0 and plugin_instance._generation == generation
    assert plugin_instance._worker is None and plugin_instance._configuration_writes == 0
    saved = run_async(plugin_instance.save_settings_hosted(deepcopy(draft)))
    assert isinstance(saved, Ok) and saved.value["saved"] is True
    update.assert_awaited_once_with(draft)
    assert Settings.parse(update.await_args.args[0]).memory.read_preference == preference
    assert original == ({} if preference == "balanced" else {"memory": {"read_preference": preference}})
    assert plugin_instance._worker is None and plugin_instance._configuration_writes == 0
