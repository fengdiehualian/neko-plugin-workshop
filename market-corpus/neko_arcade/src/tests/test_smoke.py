"""Repository smoke tests for the standalone plugin package."""

from __future__ import annotations

import pathlib
import tomllib

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _manifest() -> dict:
    return tomllib.loads((_ROOT / "plugin.toml").read_text(encoding="utf-8"))


def test_plugin_manifest_declares_expected_entrypoint_and_runtime():
    manifest = _manifest()

    assert manifest["plugin"]["id"] == "neko_arcade"
    # 清单规范写法是 plugin.plugins.<id>:Class；宿主对外部安装会规范化为 plugins.<id>:Class，
    # 挂载进宿主树后 plugin.toml 可能被 sync 改写，两种形态都接受。
    assert manifest["plugin"]["entry"] in {
        "plugin.plugins.neko_arcade:NekoArcadePlugin",
        "plugins.neko_arcade:NekoArcadePlugin",
    }
    assert manifest["plugin_runtime"]["enabled"] is True


def test_plugin_manifest_declares_hosted_ui_surface_and_files_exist():
    manifest = _manifest()

    assert manifest["plugin"]["ui"]["enabled"] is True
    for panel in manifest["plugin"]["ui"]["panel"]:
        entry = _ROOT / panel["entry"]
        assert entry.exists(), f"UI entry missing: {panel['entry']}"
    for guide in manifest["plugin"]["ui"]["guide"]:
        entry = _ROOT / guide["entry"]
        assert entry.exists(), f"Guide entry missing: {guide['entry']}"


def test_repository_support_files_meet_market_requirements():
    """市场 ``check --release`` 严格模式要求的标准仓库文件。

    这些文件缺一个，CI 就会在最后一步 ``[FAIL] ... blocked by validation errors``，
    而本地跑 pytest/ruff 全绿——所以把校验规则镜像成测试，在本地就拦住。
    """
    import json

    for rel in (
        ".vscode/settings.json",
        ".vscode/tasks.json",
        ".github/workflows/verify.yml",
        ".github/workflows/release.yml",
        "README.md",
        ".gitignore",
    ):
        assert (_ROOT / rel).is_file(), f"缺市场发布要求的仓库文件: {rel}"

    for rel in (".vscode/settings.json", ".vscode/tasks.json"):
        json.loads((_ROOT / rel).read_text(encoding="utf-8"))

    gitignore = (_ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in ("__pycache__/", ".pytest_cache/", "store.db"):
        assert pattern in gitignore, f".gitignore 应包含 {pattern}"


def test_entry_module_declares_neko_plugin_class():
    """CI 的静态入口检查：入口模块里必须存在被 @neko_plugin 装饰的入口类。"""
    manifest = _manifest()
    module_path, _, class_name = manifest["plugin"]["entry"].partition(":")
    assert module_path.endswith("neko_arcade")
    assert class_name == "NekoArcadePlugin"

    source = (_ROOT / "__init__.py").read_text(encoding="utf-8")
    assert f"class {class_name}" in source
    assert "@neko_plugin" in source


def test_plugin_source_modules_compile():
    import ast

    modules = (
        sorted((_ROOT / "core").glob("*.py"))
        + sorted((_ROOT / "adapters").glob("*.py"))
        + sorted((_ROOT / "games").rglob("*.py"))
        + [_ROOT / "__init__.py"]
    )
    for py in modules:
        ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
