"""scene_store 单元测试：scenes.toml 解析 / 空文件 / 覆盖层合并。"""
from __future__ import annotations

from pathlib import Path

from plugin.plugins.genshin_game_buddy.scene_store import load_scenes


def test_load_scenes_normal(scratch: Path) -> None:
    (scratch / "terms" / "demo").mkdir(parents=True)
    (scratch / "terms" / "demo" / "scenes.toml").write_text(
        '[scenes."深境螺旋"]\nsignals = ["深境螺旋", "第12层"]\ncontext_terms = ["元素反应"]\n',
        encoding="utf-8",
    )
    scenes = load_scenes(scratch, None, "demo", "terms/demo")
    assert "深境螺旋" in scenes
    assert scenes["深境螺旋"].signals == ("深境螺旋", "第12层")
    assert scenes["深境螺旋"].context_terms == ("元素反应",)


def test_load_scenes_missing_file(scratch: Path) -> None:
    scenes = load_scenes(scratch, None, "demo", "terms/demo")
    assert scenes == {}


def test_load_scenes_empty_file(scratch: Path) -> None:
    (scratch / "terms" / "demo").mkdir(parents=True)
    (scratch / "terms" / "demo" / "scenes.toml").write_text("", encoding="utf-8")
    scenes = load_scenes(scratch, None, "demo", "terms/demo")
    assert scenes == {}


def test_load_scenes_missing_fields(scratch: Path) -> None:
    (scratch / "terms" / "demo").mkdir(parents=True)
    (scratch / "terms" / "demo" / "scenes.toml").write_text(
        '[scenes."场景1"]\nsignals = []\ncontext_terms = []\n',
        encoding="utf-8",
    )
    scenes = load_scenes(scratch, None, "demo", "terms/demo")
    assert scenes["场景1"].signals == ()
    assert scenes["场景1"].context_terms == ()


def test_load_scenes_override_merge(scratch: Path) -> None:
    (scratch / "default" / "terms" / "demo").mkdir(parents=True)
    (scratch / "override" / "terms" / "demo").mkdir(parents=True)
    (scratch / "default" / "terms" / "demo" / "scenes.toml").write_text(
        '[scenes."A"]\nsignals = ["a"]\ncontext_terms = ["x"]\n',
        encoding="utf-8",
    )
    (scratch / "override" / "terms" / "demo" / "scenes.toml").write_text(
        '[scenes."A"]\nsignals = ["b"]\ncontext_terms = ["y"]\n',
        encoding="utf-8",
    )
    scenes = load_scenes(scratch / "default", scratch / "override", "demo", "terms/demo")
    assert scenes["A"].signals == ("b",)
    assert scenes["A"].context_terms == ("y",)
