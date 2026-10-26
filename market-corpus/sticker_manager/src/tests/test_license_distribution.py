import importlib.util
from pathlib import Path
from tomllib import loads as toml_loads

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("sticker_license_builder", ROOT / "tools/build_package.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def test_source_license_and_media_scope_are_shipped():
    selected = {path.relative_to(ROOT).as_posix() for path in builder.collect_plugin_files()}
    assert {"LICENSE", "NOTICE", "MEDIA_NOTICE.md"} <= selected
    project = toml_loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["license"] == "Apache-2.0"
    assert "Apache License" in (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "Copyright 2026 qaqms" in (ROOT / "NOTICE").read_text(encoding="utf-8")
    assert "official/official_pack.zip" in (ROOT / "MEDIA_NOTICE.md").read_text(encoding="utf-8")


def test_market_build_excludes_empty_development_cache_directories():
    rules = toml_loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["neko"]["build"]
    assert {".benchmarks", ".mypy_cache", "node_modules", "dist", "build"} <= set(rules["exclude_dirs"])
