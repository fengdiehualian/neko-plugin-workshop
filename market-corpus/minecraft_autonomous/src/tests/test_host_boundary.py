"""Keep the adapter deployable without patching or importing Neko internals."""

import ast
import importlib.util
from pathlib import Path


def test_discovered_sdk_root_matches_the_real_loaded_public_api(sdk_root):
    import plugin.sdk.plugin

    assert Path(plugin.sdk.plugin.__file__).resolve().parents[3] == sdk_root


def test_framework_is_loaded_from_this_plugins_synced_vendor():
    import agent_mc_autonomy

    root = Path(__file__).resolve().parents[1]
    assert Path(agent_mc_autonomy.__file__).resolve().is_relative_to(root / "vendor")


def test_production_adapter_only_imports_the_public_host_sdk():
    root = Path(__file__).resolve().parents[1]
    paths = [*root.glob("*.py"), *root.glob("autonomy/*.py"), *root.glob("backend/*.py")]
    checked = 0
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level:
                modules = [node.module or ""]
            for module in modules:
                if module == "plugin" or module.startswith("plugin."):
                    assert module == "plugin.sdk.plugin" or module.startswith("plugin.sdk.plugin."), (path.name, module)
                assert module.split(".")[0] not in {
                    "main_server", "config_manager", "plugin_manager", "session_manager", "llm_client", "lanlan_config"
                }, (path.name, module)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"eval", "exec"}, path.name
        checked += 1
    assert checked >= 15


def test_compatibility_namespaces_are_exports_from_the_independent_framework():
    root = Path(__file__).resolve().parents[1]
    for relative in ("worker.py", "standalone.py", "autonomy/__init__.py", "backend/install.py"):
        tree = ast.parse((root / relative).read_text(encoding="utf-8"))
        assert any((isinstance(node, ast.ImportFrom) and (node.module or "").startswith("agent_mc_autonomy"))
                   or (isinstance(node, ast.Import) and any(alias.name == "agent_mc_autonomy" for alias in node.names))
                   for node in ast.walk(tree)), relative
        assert not any(isinstance(node, ast.ImportFrom) and (node.module or "").startswith("plugin")
                       for node in ast.walk(tree)), relative


def test_standalone_delegates_arguments_and_the_canonical_runtime_verifier(monkeypatch):
    from agent_mc_autonomy.cli import main
    from agent_mc_autonomy.core_distribution.install import verify_runtime

    path = Path(__file__).resolve().parents[1] / "standalone.py"
    spec = importlib.util.spec_from_file_location("standalone_ci_wrapper", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.framework_main is main
    calls = []
    monkeypatch.setattr(module, "framework_main", lambda argv, **options: calls.append((argv, options)) or 73)
    argv = ["check", "--config", "fixture.toml"]
    assert module.main(argv) == 73
    assert calls == [(argv, {"entry_resolver": verify_runtime})]
