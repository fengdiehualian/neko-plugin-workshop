from __future__ import annotations

import ast
import importlib
import importlib.util
import json
import shutil
import sys
import tomllib
from pathlib import Path
from types import ModuleType

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
EXTERNAL_PACKAGE = "market_plugins.galgame_plugin"


def test_python_sources_do_not_import_builtin_galgame_namespace() -> None:
    violations: list[str] = []
    for source_path in PLUGIN_ROOT.rglob("*.py"):
        if any(part in {".git", ".venv", ".tox", "__pycache__", "build", "dist"} for part in source_path.parts):
            continue
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "plugin.plugins.galgame_plugin"
            ):
                violations.append(f"{source_path}: import from {node.module}")
            if not isinstance(node, ast.Call) or not node.args:
                continue
            function = node.func
            if not (
                isinstance(function, ast.Attribute)
                and function.attr == "get"
                and isinstance(function.value, ast.Attribute)
                and function.value.attr == "modules"
            ):
                continue
            key = node.args[0]
            if isinstance(key, ast.Constant) and str(key.value).startswith(
                "plugin.plugins.galgame_plugin"
            ):
                violations.append(f"{source_path}: sys.modules.get({key.value!r})")
    assert violations == []


def test_dynamic_bridges_work_without_and_ignore_builtin_namespace(monkeypatch) -> None:
    package = importlib.import_module(EXTERNAL_PACKAGE)
    package.external_marker = object()

    monkeypatch.delitem(sys.modules, "plugin.plugins.galgame_plugin", raising=False)
    monkeypatch.delitem(
        sys.modules, "plugin.plugins.galgame_plugin.ocr_reader", raising=False
    )

    helpers = importlib.import_module(f"{EXTERNAL_PACKAGE}.plugin_util_helpers")
    assert helpers._package_public_attr("external_marker", None) is package.external_marker

    builtin_package = ModuleType("plugin.plugins.galgame_plugin")
    builtin_package.external_marker = object()
    monkeypatch.setitem(sys.modules, "plugin.plugins.galgame_plugin", builtin_package)
    assert helpers._package_public_attr("external_marker", None) is package.external_marker

    external_reader = ModuleType(f"{EXTERNAL_PACKAGE}.ocr_reader")
    external_reader._foreground_window_handle = lambda: 731
    external_reader._window_handle_from_point = lambda _x, _y: 913
    monkeypatch.setitem(sys.modules, external_reader.__name__, external_reader)

    hooks = importlib.import_module(f"{EXTERNAL_PACKAGE}.ocr_input_hooks")
    assert hooks._default_foreground_window_handle() == 731
    assert hooks._default_window_handle_from_point(1, 2) == 913

    builtin_reader = ModuleType("plugin.plugins.galgame_plugin.ocr_reader")
    builtin_reader._foreground_window_handle = lambda: -1
    builtin_reader._window_handle_from_point = lambda _x, _y: -1
    monkeypatch.setitem(sys.modules, builtin_reader.__name__, builtin_reader)
    assert hooks._default_foreground_window_handle() == 731
    assert hooks._default_window_handle_from_point(1, 2) == 913

    loaded_files = {
        name: Path(module.__file__).resolve()
        for name, module in sys.modules.items()
        if name.startswith(EXTERNAL_PACKAGE)
        and isinstance(getattr(module, "__file__", None), str)
    }
    assert loaded_files
    assert all(path.is_relative_to(PLUGIN_ROOT) for path in loaded_files.values())


def test_model_directory_is_resolved_from_plugin_root(tmp_path) -> None:
    constants = importlib.import_module(f"{EXTERNAL_PACKAGE}.models.constants")
    expected = PLUGIN_ROOT / "models" / "vision" / "screen_classifier"
    assert constants.DEFAULT_VISION_CLASSIFIER_MODEL_DIR == (
        "models/vision/screen_classifier"
    )
    assert constants.resolve_vision_classifier_model_dir() == expected.resolve()
    assert constants.resolve_vision_classifier_model_dir("models/custom") == (
        PLUGIN_ROOT / "models" / "custom"
    ).resolve()
    assert constants.resolve_vision_classifier_model_dir(str(tmp_path)) == tmp_path.resolve()

    with (PLUGIN_ROOT / "plugin.toml").open("rb") as handle:
        manifest = tomllib.load(handle)
    with (PLUGIN_ROOT / "config.example.toml").open("rb") as handle:
        example = tomllib.load(handle)
    assert manifest["vision"]["model_dir"] == constants.DEFAULT_VISION_CLASSIFIER_MODEL_DIR
    assert example["vision"]["model_dir"] == constants.DEFAULT_VISION_CLASSIFIER_MODEL_DIR


def test_market_entry_has_no_install_or_tutorial_registration_side_effects(
    tmp_path, monkeypatch
) -> None:
    stage = tmp_path / "带空格 市场目录" / "plugins" / "galgame_plugin"
    stage.mkdir(parents=True)
    shutil.copy2(PLUGIN_ROOT / "__init__.py", stage / "__init__.py")
    (stage / "i18n" / "ui").mkdir(parents=True)

    monkeypatch.delitem(sys.modules, "plugin.server.install_registry", raising=False)

    parent = ModuleType("plugins")
    parent.__path__ = [str(stage.parent)]
    monkeypatch.setitem(sys.modules, "plugins", parent)

    package_name = "plugins.galgame_plugin"
    plugin_core = ModuleType(f"{package_name}.plugin_core")
    plugin_core.__getattr__ = lambda _name: object()
    config_service = ModuleType(f"{package_name}.plugin_config_service")
    config_service.GalgamePluginConfigService = type(
        "GalgamePluginConfigService", (), {}
    )
    monkeypatch.setitem(sys.modules, plugin_core.__name__, plugin_core)
    monkeypatch.setitem(sys.modules, config_service.__name__, config_service)

    spec = importlib.util.spec_from_file_location(
        package_name,
        stage / "__init__.py",
        submodule_search_locations=[str(stage)],
    )
    assert spec is not None and spec.loader is not None
    package = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, package_name, package)
    spec.loader.exec_module(package)

    assert "plugin.server.install_registry" not in sys.modules
    assert f"{package_name}._tutorial_migration" not in sys.modules
    loaded_plugin_files = [
        Path(module.__file__).resolve()
        for name, module in sys.modules.items()
        if name.startswith(package_name)
        and isinstance(getattr(module, "__file__", None), str)
    ]
    assert loaded_plugin_files == [(stage / "__init__.py").resolve()]


def test_tutorial_migration_copies_legacy_progress_without_overwriting(
    tmp_path, monkeypatch
) -> None:
    class Logger:
        def info(self, *_args: object, **_kwargs: object) -> None:
            return None

        def warning(self, *_args: object, **_kwargs: object) -> None:
            return None

    logging_config = ModuleType("plugin.logging_config")
    logging_config.get_logger = lambda _name: Logger()
    base_runtime = ModuleType("plugin.sdk.shared.core.base_runtime")
    base_runtime.resolve_runtime_data_root = lambda: tmp_path / "runtime"

    for package_name in ("plugin", "plugin.sdk", "plugin.sdk.shared", "plugin.sdk.shared.core"):
        package = ModuleType(package_name)
        package.__path__ = []
        monkeypatch.setitem(sys.modules, package_name, package)
    monkeypatch.setitem(sys.modules, logging_config.__name__, logging_config)
    monkeypatch.setitem(sys.modules, base_runtime.__name__, base_runtime)

    store_module = ModuleType(f"{EXTERNAL_PACKAGE}.store")

    class GalgameStore:
        def __init__(self, store_path: Path, _logger: object) -> None:
            self._store_path = store_path

        def load_tutorial_progress(self) -> dict[str, object] | None:
            raw = json.loads(self._store_path.read_text(encoding="utf-8"))
            progress = raw.get("tutorial_progress")
            return progress if isinstance(progress, dict) else None

    store_module.GalgameStore = GalgameStore
    monkeypatch.setitem(sys.modules, store_module.__name__, store_module)
    monkeypatch.delitem(sys.modules, f"{EXTERNAL_PACKAGE}._tutorial_migration", raising=False)
    migration = importlib.import_module(f"{EXTERNAL_PACKAGE}._tutorial_migration")

    legacy_store = tmp_path / "legacy" / "galgame_store.json"
    legacy_store.parent.mkdir(parents=True)
    legacy_store.write_text(
        json.dumps({"tutorial_progress": {"day": 3, "completed": ["intro"]}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(migration, "_legacy_store_paths", lambda: (legacy_store,))

    target = tmp_path / "market" / "tutorial.json"
    migration.copy_legacy_tutorial_progress_if_missing(target)
    assert json.loads(target.read_text(encoding="utf-8")) == {
        "completed": ["intro"],
        "day": 3,
    }

    target.write_text('{"preserved": true}', encoding="utf-8")
    migration.copy_legacy_tutorial_progress_if_missing(target)
    assert json.loads(target.read_text(encoding="utf-8")) == {"preserved": True}
