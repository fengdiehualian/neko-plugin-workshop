from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_diary_module():
    root = Path(__file__).resolve().parents[1]
    path = root / "_diary.py"
    spec = importlib.util.spec_from_file_location("_diary", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_manifest_exists() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = root / "plugin.toml"
    assert manifest.is_file()
    text = manifest.read_text(encoding="utf-8")
    assert 'id = "keyboard_controller"' in text
    assert 'entry = "plugin.plugins.keyboard_controller:KeyboardControllerPlugin"' in text
    assert "diary_enabled = true" in text


def test_diary_records_and_renders() -> None:
    _diary = _load_diary_module()
    log = _diary.DiaryLog(enabled=True, max_events_per_day=10, locale="zh-CN")
    log.record("input", "按键 ctrl+c")
    log.record("capture", "截图 1920x1080")
    day = _diary._day_key()
    markdown = log.render_markdown(day)
    assert "按键 ctrl+c" in markdown
    assert "截图 1920x1080" in markdown
    assert log.counts(day) == {"input": 1, "capture": 1}


def test_diary_flush_and_readback(tmp_path: Path) -> None:
    _diary = _load_diary_module()
    log = _diary.DiaryLog(enabled=True, locale="zh-CN")
    log.record("note", "今天天气不错")
    day = _diary._day_key()
    path = log.flush_day(tmp_path, day)
    assert path is not None
    assert path.name == f"{day}.md"
    assert path.is_file()
    data = log.read_day(tmp_path, day)
    assert data["event_count"] == 1
    assert "今天天气不错" in data["markdown"]


def test_diary_disabled_does_not_record() -> None:
    _diary = _load_diary_module()
    log = _diary.DiaryLog(enabled=False, locale="zh-CN")
    log.record("note", "should be dropped")
    assert log.total_today() == 0


def test_diary_respects_max_events() -> None:
    _diary = _load_diary_module()
    log = _diary.DiaryLog(enabled=True, max_events_per_day=2, locale="zh-CN")
    log.record("note", "1")
    log.record("note", "2")
    log.record("note", "3")
    day = _diary._day_key()
    assert log.counts(day)["note"] == 2
    assert log.dropped(day) == 1
