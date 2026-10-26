# -*- coding: utf-8 -*-
"""A 档看门狗：两代注意力配置命名合并 + 僵尸键清理 + 前端/schema 键位一致性。

背景（为什么需要这些测试）：
1. 插件的注意力配置存在**两代命名**（``attention_*`` 与 ``group_attention_*``）。
   旧一代里有 5 个仍在生效、7 个已无人读；后者只活在 ``business_config.json``
   里（schema/前端/任何 .py 都是 0 命中），因为 ``load()``/``save()`` 的
   ``update`` 语义会把不认识的键一直带着走。
2. 改名这类机械改动最容易漏掉**前端**：``static/napcat.html`` 里同时有
   「填表读键」「存表写键」「显示取值」三处硬编码键名。本文件把它钉住。
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[1]
CONFIG_FILE = "business_config.json"

#: 旧名 → 新名。与 ``config_store._LEGACY_ATTENTION_KEY_MAP`` 必须一致。
LEGACY_MAP = {
    "group_attention_max_score": "attention_max_score",
    "group_attention_min_threshold": "attention_min_threshold",
    "group_attention_focus_threshold": "attention_focus_threshold",
    "group_attention_focus_send_threshold": "attention_focus_hold_threshold",
    "group_attention_message_gain": "attention_batch_message_gain",
}

#: 只在配置文件里留尸的键：任何代码/schema/前端都不该再出现。
ZOMBIES = (
    "group_attention_decay_per_second",
    "group_attention_focus_cooldown_seconds",
    "group_attention_focus_lock_seconds",
    "group_attention_focus_rise_seconds",
    "group_attention_keyword_boost_scale",
    "group_attention_message_recovery",
    "group_attention_reply_penalty",
)


def _write_config(tmp_path, payload: dict) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8",
    )


def _store(tmp_path):
    from plugin.plugins.qq_auto_reply.config_store import QQAutoReplyConfigStore

    return QQAutoReplyConfigStore(tmp_path)


@pytest.mark.asyncio
async def test_legacy_attention_keys_migrate_and_keep_tuned_values(tmp_path):
    """旧名有值、新名缺失 ⇒ 搬过去；用户调过的值不能被默认值盖掉。"""
    _write_config(tmp_path, {
        "group_attention_focus_threshold": 6.5,
        "group_attention_focus_send_threshold": 3.25,
        "group_attention_max_score": 8.0,
        "group_attention_min_threshold": 0.5,
        "group_attention_message_gain": 0.9,
    })
    loaded = await _store(tmp_path).load()

    assert loaded["attention_focus_threshold"] == 6.5
    assert loaded["attention_focus_hold_threshold"] == 3.25
    assert loaded["attention_max_score"] == 8.0
    assert loaded["attention_min_threshold"] == 0.5
    assert loaded["attention_batch_message_gain"] == 0.9
    for old in LEGACY_MAP:
        assert old not in loaded, f"旧名 {old} 迁移后必须消失"


@pytest.mark.asyncio
async def test_new_name_wins_when_both_generations_present(tmp_path):
    """两代同名键并存 ⇒ 以新名为准（新名才是当前保存链路写的那个）。"""
    _write_config(tmp_path, {
        "attention_focus_threshold": 5.0,
        "group_attention_focus_threshold": 9.9,
    })
    loaded = await _store(tmp_path).load()

    assert loaded["attention_focus_threshold"] == 5.0
    assert "group_attention_focus_threshold" not in loaded


@pytest.mark.asyncio
async def test_zombie_keys_are_dropped_on_load_and_never_written_back(tmp_path):
    """7 个僵尸键：加载后不出现，保存后也不落盘。"""
    _write_config(tmp_path, {**{z: 1.23 for z in ZOMBIES}, "reply_mode": "text"})
    store = _store(tmp_path)
    loaded = await store.load()

    for zombie in ZOMBIES:
        assert zombie not in loaded, f"僵尸键 {zombie} 不该被加载进来"

    await store.save(loaded)
    on_disk = json.loads((tmp_path / CONFIG_FILE).read_text(encoding="utf-8"))
    for zombie in ZOMBIES:
        assert zombie not in on_disk, f"僵尸键 {zombie} 不该被写回磁盘"


@pytest.mark.asyncio
async def test_migration_is_idempotent(tmp_path):
    """迁移可反复调用：第二次加载结果与第一次逐键相同。"""
    _write_config(tmp_path, {
        "group_attention_focus_threshold": 6.5,
        **{z: 0.5 for z in ZOMBIES},
    })
    store = _store(tmp_path)
    first = await store.load()
    await store.save(first)
    second = await store.load()

    assert first == second


def test_no_legacy_attention_names_remain_in_sources():
    """全仓（.py/.html/.json）除迁移表本身外，不该再出现旧名或僵尸键。"""
    allowed = {"config_store.py", pathlib.Path(__file__).name}
    offenders: list[str] = []
    for path in sorted(PLUGIN_DIR.rglob("*")):
        if not path.is_file() or path.suffix not in {".py", ".html", ".json"}:
            continue
        if "_vendor" in path.parts or path.name in allowed:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for name in (*LEGACY_MAP, *ZOMBIES):
            if name in text:
                offenders.append(f"{path.relative_to(PLUGIN_DIR).as_posix()}: {name}")
    assert not offenders, "旧命名残留：\n" + "\n".join(offenders)


def test_frontend_attention_control_ids_all_registered_in_schema():
    """前端每个 ``cfg-att-*`` 控件都必须在 schema 里有对应 SettingSpec。

    这条是踩过的坑：改名时前端有「填表/存表/显示」三处硬编码键名，
    只改后端就会让面板静默失效（存进去的键没人读）。
    """
    html = (PLUGIN_DIR / "static" / "napcat.html").read_text(encoding="utf-8")
    schema = (PLUGIN_DIR / "settings_schema.py").read_text(encoding="utf-8")

    html_ids = set(re.findall(r"""["'](cfg-att-[a-z0-9-]+)["']""", html))
    schema_ids = set(re.findall(r"""UIInput\(\s*["'](cfg-att-[a-z0-9-]+)["']""", schema))

    assert html_ids, "没在前端解析到任何注意力控件，测试本身失效了"
    missing = sorted(html_ids - schema_ids)
    assert not missing, f"前端控件在 schema 里没有注册：{missing}"


@pytest.mark.asyncio
async def test_unknown_legacy_prefixed_keys_are_dropped(tmp_path):
    """前缀兜底：没列进名单、但同前缀的残留键也必须被丢掉。

    这条是被真机打脸后加的：``fatigue_tiers`` 只存在于**真机配置文件**里，
    repo 内 0 命中，靠逐个列名字根本对不出来。改成前缀规则后，同类漏网不再可能。
    """
    _write_config(tmp_path, {
        "fatigue_some_future_key": 1,
        "group_attention_whatever": 2,
        "reply_mode": "text",
    })
    loaded = await _store(tmp_path).load()

    assert "fatigue_some_future_key" not in loaded, "fatigue* 前缀残留未被清掉"
    assert "group_attention_whatever" not in loaded, "group_attention_* 前缀残留未被清掉"


@pytest.mark.asyncio
async def test_load_reports_whether_it_migrated(tmp_path):
    """``load()`` 必须报告「这次是否真的迁移过」——调用方据此决定要不要落盘。

    真机踩过：迁移只改内存视图，磁盘里长期留着旧键（插件已按新键跑，
    配置文件却还是两代键并存）。settings_service 会在迁移过时立刻写盘一次。
    """
    _write_config(tmp_path, {"group_attention_focus_threshold": 6.5, "fatigue_enabled": True})
    store = _store(tmp_path)
    await store.load()
    assert store.migration_applied is True

    # 干净的配置：不该报告迁移（否则每次启动都白写一次盘）
    clean = tmp_path / "clean"
    clean.mkdir()
    clean_store = _store(clean)
    await clean_store.load()                      # 文件不存在 → 默认值
    assert clean_store.migration_applied is False

    await clean_store.save(clean_store.default_config())
    await clean_store.load()
    assert clean_store.migration_applied is False


@pytest.mark.asyncio
async def test_migrated_file_is_rewritten_after_save(tmp_path):
    """迁移后保存一次，磁盘上就不该再有旧键/僵尸键。"""
    _write_config(tmp_path, {"fatigue_tiers": [1, 2], "group_attention_max_score": 8.0, "reply_mode": "text"})
    store = _store(tmp_path)
    loaded = await store.load()
    assert store.migration_applied is True
    await store.save(loaded)
    on_disk = json.loads((tmp_path / CONFIG_FILE).read_text(encoding="utf-8"))
    assert "fatigue_tiers" not in on_disk
    assert "group_attention_max_score" not in on_disk
    assert on_disk["attention_max_score"] == 8.0


@pytest.mark.asyncio
async def test_migration_persist_keeps_permission_lists(tmp_path):
    """加载期迁移落盘时**不能**把权限名单冲成空。

    真机事故（本测试就是它的回归）：``load_business_config`` 跑在
    ``rebuild_permission_managers`` **之前**，此时权限管理器还是空的；
    普通 persist 会用 ``list_users()/list_groups()``（空）覆盖磁盘上的信任名单
    —— 一次插件重载把管理员与两个群全清掉了。

    修复方式：走 ``_persist_business_config_locked(preserve_published_permissions=True)``。
    """
    from types import SimpleNamespace

    from plugin.plugins.qq_auto_reply.config_store import QQAutoReplyConfigStore
    from plugin.plugins.qq_auto_reply.settings_service import QQSettingsService

    users = [{"qq": "820040531", "level": "admin"}]
    groups = [{"group_id": "985066274", "level": "trusted"}, {"group_id": "1048307485", "level": "open"}]
    _write_config(tmp_path, {
        "trusted_users": users,
        "trusted_groups": groups,
        "fatigue_enabled": True,               # 僵尸键：迁移会清掉
        "group_attention_max_score": 8.0,      # 旧名：迁移会改名
        "reply_mode": "text",
    })

    plugin = SimpleNamespace(
        config_store=QQAutoReplyConfigStore(tmp_path),
        _qq_settings={},
        permission_mgr=None,          # 启动早期就是 None —— 事故的成因
        group_permission_mgr=None,
        backlog_store=None,
        logger=SimpleNamespace(error=lambda *a, **k: None, info=lambda *a, **k: None),
        _emit_log=lambda *a, **k: None,
        _create_backlog_store_from_settings=lambda settings: None,
    )
    assert plugin.config_store.migration_applied is False

    await QQSettingsService(plugin).load_business_config()
    assert plugin.config_store.migration_applied is True

    on_disk = json.loads((tmp_path / CONFIG_FILE).read_text(encoding="utf-8"))
    assert on_disk["trusted_users"] == users, "迁移落盘把信任用户名单冲掉了"
    assert on_disk["trusted_groups"] == groups, "迁移落盘把信任群名单冲掉了"
    assert plugin._qq_settings["trusted_groups"] == groups
    # 迁移本身仍然生效
    assert "fatigue_enabled" not in on_disk
    assert on_disk["attention_max_score"] == 8.0
