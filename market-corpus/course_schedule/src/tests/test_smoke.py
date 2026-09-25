"""插件冒烟测试：校验清单文件与核心纯逻辑，不依赖 N.E.K.O 运行时。"""

from __future__ import annotations

import importlib.util
import json
import tomllib
from datetime import date
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str):
    """按文件路径加载插件根目录下的纯标准库模块（避免依赖包导入环境）。"""
    spec = importlib.util.spec_from_file_location(name, PLUGIN_ROOT / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plugin_manifest_exists() -> None:
    manifest = PLUGIN_ROOT / "plugin.toml"
    assert manifest.is_file()
    text = manifest.read_text(encoding="utf-8")
    assert 'id = "course_schedule"' in text
    assert 'entry = "plugin.plugins.course_schedule:CourseSchedulePlugin"' in text


def test_manifest_assets_exist() -> None:
    data = tomllib.loads((PLUGIN_ROOT / "plugin.toml").read_text(encoding="utf-8"))
    plugin = data["plugin"]
    assert plugin["id"] == "course_schedule"
    assert (PLUGIN_ROOT / "__init__.py").is_file()
    assert (PLUGIN_ROOT / "config.example.toml").is_file()
    ui = plugin.get("ui", {})
    for panel in ui.get("panel", []):
        assert (PLUGIN_ROOT / panel["entry"]).is_file()
    for guide in ui.get("guide", []):
        assert (PLUGIN_ROOT / guide["entry"]).is_file()
    if "i18n" in plugin:
        assert (PLUGIN_ROOT / plugin["i18n"].get("locales_dir", "i18n")).is_dir()


def test_week_number_logic() -> None:
    time_mod = _load_module("_time")
    semester = {"start_date": "2026-03-02", "end_date": "2026-07-05"}
    assert time_mod.week_number(semester, date(2026, 3, 2)) == 1
    assert time_mod.week_number(semester, date(2026, 3, 8)) == 1
    assert time_mod.week_number(semester, date(2026, 3, 9)) == 2
    assert time_mod.week_number(semester, date(2026, 7, 5)) == 18
    assert time_mod.week_number(semester, date(2026, 1, 1)) is None
    assert time_mod.active_in_week([], 3) is True
    assert time_mod.active_in_week([1, 2], 3) is False
    assert time_mod.active_in_week([1, 3], 3) is True


def test_weeks_json_roundtrip() -> None:
    schema_mod = _load_module("_schema")
    stored = schema_mod.weeks_to_json([1, 2, 3])
    assert isinstance(stored, str)
    assert json.loads(stored) == [1, 2, 3]
    assert schema_mod.parse_weeks(stored) == [1, 2, 3]
    assert schema_mod.weeks_to_json(None) is None
    assert schema_mod.weeks_to_json([]) is None
    assert schema_mod.parse_weeks(None) == []
