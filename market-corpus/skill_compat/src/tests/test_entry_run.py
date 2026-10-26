"""skill_compat v0.1.26 通用入口测试：entry_run 分派（纯逻辑路径）。"""

import asyncio
import threading


def _plugin():
    from plugin.plugins.skill_compat import SkillCompatPlugin

    p = SkillCompatPlugin.__new__(SkillCompatPlugin)
    p._idx = {}
    p._lock = threading.Lock()
    p._skill_dirs = []
    p._follow_symlinks = True
    p._scan_status = "未扫描"
    p._consent_given = True
    return p


def _run(coro):
    return asyncio.run(coro)


def test_run_entry_registered():
    """plugin_entry run 入口可被宿主收集（v0.1.26 新增）。"""
    from plugin.plugins.skill_compat import SkillCompatPlugin

    metas = []
    for name in dir(SkillCompatPlugin):
        if name.startswith("_"):
            continue
        meta = getattr(getattr(SkillCompatPlugin, name, None), "__neko_event_meta__", None)
        if meta is not None and getattr(meta, "event_type", None) == "plugin_entry":
            metas.append(meta)
    ids = [m.id for m in metas]
    assert "run" in ids, f"缺少入口 run，现有：{ids}"


def test_run_unknown_action():
    p = _plugin()
    out = _run(p.entry_run(action="fly", parameters={}))
    assert out["is_error"] is True
    assert out["error"] == "NO_ACTION"


def test_run_list_empty():
    """list：无扫描目录时安全返回空列表（不触发真实目录扫描）。"""
    from plugin.sdk.plugin import Ok

    p = _plugin()
    out = _run(p.entry_run(action="list", parameters={}))
    assert isinstance(out, Ok), out
    assert out.value.get("count") == 0


def test_run_status_shape():
    """status：返回加载器状态结构。"""
    from plugin.sdk.plugin import Ok

    p = _plugin()
    out = _run(p.entry_run(action="status", parameters={}))
    assert isinstance(out, Ok), out
    assert "total" in out.value


def test_run_load_missing_skill():
    """load：未找到 skill 时给出可理解错误（不崩溃）。"""
    from plugin.sdk.plugin import Err

    p = _plugin()
    out = _run(p.entry_run(action="load", name="no_such_skill_xyz", parameters={}))
    assert isinstance(out, Err), out
    assert "找不到" in str(out.error) or "no_such_skill_xyz" in str(out.error)
