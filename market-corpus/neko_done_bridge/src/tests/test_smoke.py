"""neko_done_bridge 冒烟测试。"""

from __future__ import annotations

import importlib


def test_module_imports():
    mod = importlib.import_module("plugin.plugins.neko_done_bridge")
    assert hasattr(mod, "NekoDoneBridgePlugin")
    assert hasattr(mod, "_diff_tasks")
    assert hasattr(mod, "_pick_text")


def test_diff_tasks_detects_changes():
    from plugin.plugins.neko_done_bridge import _diff_tasks

    old = {
        "1": {"id": "1", "text": "旧任务", "priority": 2, "done": False},
        "2": {"id": "2", "text": "要完成的", "priority": 0, "done": False},
        "3": {"id": "3", "text": "要删除的", "priority": 1, "done": False},
    }
    new = [
        {"id": "1", "text": "旧任务改名", "priority": 0, "done": False},
        {"id": "2", "text": "要完成的", "priority": 0, "done": True},
        {"id": "4", "text": "新任务", "priority": 1, "done": False},
    ]
    actions = {(e["action"], e["task_id"]) for e in _diff_tasks(old, new)}
    # 任务 1 同时改了优先级和文本：按设计只报最主要的变化（promote），不重复打扰
    assert ("promote", "1") in actions
    assert len([a for a in actions if a[1] == "1"]) == 1
    assert ("complete", "2") in actions
    assert ("delete", "3") in actions
    assert ("add", "4") in actions


def test_pick_text_styles():
    from plugin.plugins.neko_done_bridge import _pick_text

    a = _pick_text("neko_default", "urge", "写报告", "小明")
    b = _pick_text("calm", "urge", "写报告", "小明")
    c = _pick_text("idol", "urge", "写报告", "小明")
    assert "喵" in a
    assert "写报告" in b and "喵" not in b
    assert "写报告" in c
    # idol 风格不得引用真实歌姬角色名
    for name in ("miku", "teto", "neru", "初音", "重音", "亚北"):
        assert name not in c.lower()
