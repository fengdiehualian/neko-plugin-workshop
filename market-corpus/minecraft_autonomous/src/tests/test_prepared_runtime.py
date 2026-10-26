"""Thin host wiring, with canonical selection and no game/process/network access."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from plugin.sdk.plugin import Err, Ok


@pytest.fixture
def prepared(plugin_module, monkeypatch, tmp_path):
    import agent_mc_autonomy.runtime_bundle as neutral
    host = __import__(plugin_module.__package__ + ".prepared_runtime", fromlist=["unused"])
    bundle = neutral.PreparedBundle(tmp_path, tmp_path / "node.exe", tmp_path / "backend", tmp_path / "renderer",
                                    tmp_path / "catalog.json", tmp_path / "appearances.json", "a" * 64)
    calls = []
    monkeypatch.setattr(neutral, "resolve_prepared_bundle", lambda **_: bundle)
    def metadata(root, **options):
        assert root == tmp_path and options["full"] is False
        calls.append(options)
        return {}
    monkeypatch.setattr(host, "verify_bundle", metadata)
    return host, bundle, calls


def test_default_paths_precede_appearance_and_keep_binding(prepared):
    host, bundle, calls = prepared
    raw = {"backend": {"node": ""}, "viewer": {"bundled_appearance_catalog_file": "", "default_appearance": "server"}}
    original = copy.deepcopy(raw)
    effective, state = host.resolve_host_runtime(raw)
    assert state == "prepared" and raw == original
    assert effective["backend"] == {"node": str(bundle.node), "directory": str(bundle.backend)}
    assert effective["viewer"]["bundled_appearance_catalog_file"] == str(bundle.appearances)
    assert effective["viewer"]["default_appearance"] == "server"
    assert effective["runtime"]["bundle_manifest_sha256"] == bundle.manifest_sha256
    assert host.resolve_host_runtime(json.loads(json.dumps(effective))) == (effective, "prepared")
    assert len(calls) == 2


@pytest.mark.parametrize("raw", [
    {"backend": {"node": "node"}}, {"backend": {"directory": "custom"}},
    {"viewer": {"renderer_directory": "custom", "catalog_file": "custom.json"}},
    {"viewer": {"bundled_appearance_catalog_file": "custom.json"}},
    {"viewer": {"url": "https://example.invalid/observer"}},
    {"runtime": {"mode": "external", "data_directory": "private"}},
])
def test_explicit_components_and_external_never_resolve_bundle(plugin_module, monkeypatch, raw):
    import agent_mc_autonomy.runtime_bundle as neutral
    host = __import__(plugin_module.__package__ + ".prepared_runtime", fromlist=["unused"])
    monkeypatch.setattr(neutral, "resolve_prepared_bundle", lambda **_: pytest.fail("implicit mixed runtime"))
    original = copy.deepcopy(raw)
    effective, state = host.resolve_host_runtime(raw)
    assert raw == original and "bundle_root" not in effective.get("runtime", {})
    assert state == ("external" if raw.get("runtime", {}).get("mode") == "external" else "custom")
    for section, values in original.items():
        assert effective[section].items() >= values.items()


@pytest.mark.parametrize("code,state", [("metadata_unavailable", "missing"), ("platform_mismatch", "incompatible")])
def test_unprepared_draft_stays_editable_and_default_eligible(plugin_instance, plugin_module, monkeypatch, run_async, code, state):
    import agent_mc_autonomy.runtime_bundle as neutral
    def unavailable(**_):
        raise neutral.BundleError("runtime_bundle_" + code)
    monkeypatch.setattr(neutral, "resolve_prepared_bundle", unavailable)
    monkeypatch.setattr(plugin_module, "verify_runtime", lambda _: pytest.fail("metadata must not scan runtime"))
    dashboard = run_async(plugin_instance.dashboard())
    assert dashboard["installation"]["prepared_runtime"] == state
    assert dashboard["settings"]["backend"]["node"] == ""
    assert dashboard["settings"]["viewer"]["bundled_appearance_catalog_file"] == ""
    update = AsyncMock()
    monkeypatch.setattr(plugin_instance.config, "update", update)
    assert isinstance(run_async(plugin_instance.save_settings(dashboard["settings"])), Ok)
    assert plugin_instance._worker is None


@pytest.mark.parametrize("legacy_sdk", [False, True])
def test_dashboard_roundtrip_and_start_share_exact_runtime(prepared, plugin_instance, plugin_module, monkeypatch, run_async, legacy_sdk):
    if legacy_sdk:
        monkeypatch.delattr(type(plugin_instance.ctx), "models")
    host, bundle, calls = prepared
    raw = {"autonomy": {"enabled": True}, "server": {"host": "localhost", "username": "Fixture"}}
    if legacy_sdk:
        raw["planner"] = {"route": "direct", "provider": "openai-compatible", "base_url": "http://fixture.invalid/v1",
                          "model": "fixture", "auth": {"mode": "none"}}
    monkeypatch.setattr(plugin_instance.config, "dump", AsyncMock(side_effect=lambda: copy.deepcopy(raw)))
    monkeypatch.setattr(plugin_instance.config, "update", AsyncMock(side_effect=lambda settings: raw.update(copy.deepcopy(settings))))
    monkeypatch.setattr(plugin_module, "verify_runtime", lambda _: pytest.fail("status must not hash runtime"))
    state = run_async(plugin_instance.dashboard())
    draft = json.loads(json.dumps(state["settings"]))
    assert draft["runtime"]["bundle_root"] == str(bundle.root)
    assert draft["runtime"]["bundle_manifest_sha256"] == bundle.manifest_sha256
    assert isinstance(run_async(plugin_instance.save_settings(draft)), Ok)
    assert plugin_instance._worker is None
    assert host.resolve_host_runtime(raw)[1] == "prepared"
    checked = []
    monkeypatch.setattr(plugin_module, "verify_runtime", lambda path: checked.append(path) or path / "entry.mjs")
    class Worker:
        def __init__(self, play, log):
            self.play = play
        def start(self, goal):
            pass
        def snapshot(self):
            return {"worker_alive": True}
    monkeypatch.setattr(plugin_module, "Worker", Worker)
    captured = []
    async def session(settings, entry, data_root, planner, *_):
        captured.append(settings)
        if legacy_sdk:
            assert planner.config.model == "fixture"
            await planner.aclose()
    monkeypatch.setattr(plugin_module, "run_session", session)
    if not legacy_sdk:
        monkeypatch.setattr(plugin_module, "create_planner", lambda *_: None)
    assert isinstance(run_async(plugin_instance.start()), Ok)
    run_async(plugin_instance._worker.play("survive", None, None))
    assert checked == [bundle.backend]
    assert captured[0].bundle_root == str(bundle.root)
    assert captured[0].bundle_manifest_sha256 == bundle.manifest_sha256
    assert captured[0].node == str(bundle.node) and captured[0].viewer_catalog_file == str(bundle.catalog)
    assert calls and all(call["full"] is False for call in calls)


def test_incompatible_bound_runtime_does_not_start(prepared, plugin_instance, plugin_module, monkeypatch, run_async):
    host, _, _ = prepared
    raw, _ = host.resolve_host_runtime({"autonomy": {"enabled": True}, "server": {"host": "localhost", "username": "Fixture"}})
    monkeypatch.setattr(plugin_instance.config, "dump", AsyncMock(return_value=raw))
    def mismatch(*_, **__):
        from agent_mc_autonomy.runtime_bundle import BundleError
        raise BundleError("runtime_bundle_source_mismatch")
    monkeypatch.setattr(host, "verify_bundle", mismatch)
    monkeypatch.setattr(plugin_module, "verify_runtime", lambda _: pytest.fail("incompatible runtime must not start"))
    result = run_async(plugin_instance.start())
    assert isinstance(result, Err) and "prepare.cmd" in result.error["message"]
    assert plugin_instance._worker is None


@pytest.mark.parametrize("worker_alive", [False, True])
def test_explicit_prepared_upgrade_preserves_user_configuration(prepared, plugin_instance, monkeypatch, run_async, worker_alive):
    host, bundle, _ = prepared
    old = {
        "server": {"host": "localhost", "username": "MyRole"},
        "planner": {"route": "host"},
        "memory": {"mode": "local"},
        "runtime": {"mode": "embedded", "data_directory": "my-data", "bundle_root": "old-runtime", "bundle_manifest_sha256": "b" * 64},
        "backend": {"node": "old-node", "directory": "old-backend"},
        "viewer": {"renderer_directory": "old-renderer", "catalog_file": "old-catalog", "bundled_appearance_catalog_file": "old-appearances", "auto_open": False},
    }
    original = copy.deepcopy(old)
    from agent_mc_autonomy.runtime_bundle import BundleError
    def check(root, **options):
        if root != bundle.root:
            raise BundleError("runtime_bundle_source_mismatch")
        assert options["full"] is False
        return {}
    monkeypatch.setattr(host, "verify_bundle", check)
    assert host.resolve_host_runtime(old)[1] == "incompatible"
    monkeypatch.setattr(plugin_instance.config, "dump", AsyncMock(side_effect=lambda: copy.deepcopy(old)))
    async def update(patch):
        for section, values in patch.items():
            old.setdefault(section, {}).update(copy.deepcopy(values))
    writer = AsyncMock(side_effect=update)
    monkeypatch.setattr(plugin_instance.config, "update", writer)
    if worker_alive:
        plugin_instance._worker = SimpleNamespace(snapshot=lambda: {"worker_alive": True})
    patch = {"backend": {"node": "", "directory": ""},
             "viewer": {"renderer_directory": "", "catalog_file": "", "bundled_appearance_catalog_file": ""},
             "runtime": {"bundle_root": "", "bundle_manifest_sha256": ""}}
    result = run_async(plugin_instance.save_settings(patch))
    if worker_alive:
        assert isinstance(result, Err) and result.error["code"] == "settings_runtime_active"
        assert old == original and writer.await_count == 0
        return
    assert isinstance(result, Ok) and plugin_instance._worker is None
    for section in ["server", "planner", "memory"]:
        assert old[section] == original[section]
    assert old["runtime"]["data_directory"] == "my-data"
    assert old["runtime"]["mode"] == "embedded"
    assert old["viewer"]["auto_open"] is False
    selected, state = host.resolve_host_runtime(old)
    assert state == "prepared" and selected["runtime"]["bundle_root"] == str(bundle.root)
    assert selected["backend"]["directory"] == str(bundle.backend)
