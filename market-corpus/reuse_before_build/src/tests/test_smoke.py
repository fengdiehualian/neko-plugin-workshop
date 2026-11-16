"""市场模板要求的 smoke test（文件名固定为 tests/test_smoke.py）。

它不是重复劳动：N.E.K.O 的 `check -r` 在严格模式下要求仓库里存在这个文件，
并期望它至少能确认清单与入口没写错。真实行为测试在 tests/test_offline.py
与 tests/test_plugin_runtime.py。
"""

from pathlib import Path


def test_plugin_manifest_exists() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = root / "plugin.toml"
    assert manifest.is_file()
    text = manifest.read_text(encoding="utf-8")
    assert 'id = "reuse_before_build"' in text
    assert 'entry = "plugin.plugins.reuse_before_build:ReuseBeforeBuildPlugin"' in text


def test_entry_module_exists() -> None:
    root = Path(__file__).resolve().parents[1]
    assert (root / "__init__.py").is_file()
    assert (root / "rbb_core.py").is_file()


def test_bundled_skill_is_present() -> None:
    root = Path(__file__).resolve().parents[1]
    skill = root / "skills" / "reuse-before-build"
    for rel in ("SKILL.md", "localization.contract.json", "scripts/verify_localized.py"):
        assert (skill / rel).is_file(), rel
