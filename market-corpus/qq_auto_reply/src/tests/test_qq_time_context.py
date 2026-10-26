# -*- coding: utf-8 -*-
"""时间上下文（从 fatigue_service 救出的那段）+ 「疲劳已删干净」看门狗。

为什么要单独钉住时间上下文：
``get_dynamic_time_context()`` 原先寄居在 ``fatigue_service.py`` 里，但它与疲劳无关，
是提示词的时间层——「当前时间 / 星期 / 时段」加一句「结合当前时间理解'刚刚''昨天''下周'」。
删疲劳时若整文件删掉，猫娘会**静默失去时间感**，而记忆召回正依赖它。
"""

from __future__ import annotations

import datetime as _dt
import pathlib
import re

import pytest
from plugin.plugins.qq_auto_reply.time_context import build_time_context

PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[1]

#: 允许出现 "fatigue" 字样的文件：
#: - ``config_store.py``：僵尸键名单 + 前缀兜底（必须认得旧键才能删掉它们）
#: - ``time_context.py``：文档，记录这段代码是从哪里救出来的
#: - ``test_qq_attention_key_migration.py``：迁移测试必须写出键名才能断言它们被丢掉
ALLOWED_FATIGUE_MENTIONS = {
    "config_store.py",
    "time_context.py",
    "test_qq_attention_key_migration.py",
}


@pytest.mark.parametrize(
    ("hour", "minute", "expected_phase"),
    [
        (0, 0, "现在是深夜凌晨。"),
        (5, 59, "现在是深夜凌晨。"),
        (6, 0, "现在是早晨。"),
        (8, 59, "现在是早晨。"),
        (9, 0, "现在是上午。"),
        (11, 59, "现在是上午。"),
        (12, 0, "现在是中午/午后。"),
        (13, 59, "现在是中午/午后。"),
        (14, 0, "现在是下午。"),
        (17, 59, "现在是下午。"),
        (18, 0, "现在是傍晚/晚间。"),
        (21, 59, "现在是傍晚/晚间。"),
        (22, 0, "现在是深夜。"),
        (23, 59, "现在是深夜。"),
    ],
)
def test_time_context_phase_boundaries(monkeypatch, hour, minute, expected_phase):
    """每个时段边界都必须落在原来那一档（删除疲劳不得改变提示词内容）。"""
    frozen = _dt.datetime(2026, 9, 27, hour, minute)
    weekday_char = "一二三四五六日"[frozen.weekday()]

    class _Frozen(_dt.datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: D102
            return frozen

    monkeypatch.setattr(_dt, "datetime", _Frozen)
    text = build_time_context()

    assert f"当前时间：2026年09月27日 {hour:02d}:{minute:02d}，星期{weekday_char}。" in text
    assert expected_phase in text
    assert '注意结合当前时间理解对话中的时间表达（如"刚刚""昨天""下周"等）。' in text
    assert text.count("\n") == 3


def test_time_context_has_exactly_three_lines():
    """整段格式固定为 3 行（第一行时间、第二行时段、第三行时间表达提示）。"""
    lines = build_time_context().rstrip("\n").split("\n")
    assert len(lines) == 3
    assert re.match(r"^当前时间：\d{4}年\d{2}月\d{2}日 \d{2}:\d{2}，星期[一二三四五六日]。$", lines[0])
    assert re.match(r"^现在是(深夜凌晨|早晨|上午|中午/午后|下午|傍晚/晚间|深夜)。$", lines[1])
    assert lines[2].startswith("注意结合当前时间理解")


def test_no_fatigue_left_anywhere_except_zombie_list_and_doc():
    """疲劳系统整体删除：源码/前端/i18n 不该再有 fatigue 字样。

    允许清单只有两处：``config_store`` 的僵尸键名单（必须认得旧键才能删掉它们）
    与 ``time_context`` 的文档（记录这段代码是从哪里救出来的）。
    """
    offenders: list[str] = []
    for path in sorted(PLUGIN_DIR.rglob("*")):
        if not path.is_file() or path.suffix not in {".py", ".html", ".json"}:
            continue
        if "_vendor" in path.parts or path.name in ALLOWED_FATIGUE_MENTIONS:
            continue
        if path.name == pathlib.Path(__file__).name:
            continue
        if "fatigue" in path.read_text(encoding="utf-8", errors="replace").lower():
            offenders.append(path.relative_to(PLUGIN_DIR).as_posix())
    assert not offenders, "疲劳残留：\n" + "\n".join(offenders)


def test_fatigue_config_keys_are_listed_as_zombies():
    """5 个疲劳配置键必须留在僵尸名单里，否则旧配置文件里它们会被原样带着走。"""
    from plugin.plugins.qq_auto_reply.config_store import QQAutoReplyConfigStore

    zombies = set(QQAutoReplyConfigStore._LEGACY_ZOMBIE_KEYS)
    for key in (
        "fatigue_enabled",
        "fatigue_circadian_peak_hour",
        "fatigue_circadian_low_hour",
        "fatigue_session_per_reply",
        "fatigue_awake_idle_timeout",
        "fatigue_tiers",
    ):
        assert key in zombies, f"{key} 应列入僵尸键名单"


def test_fatigue_keys_do_not_survive_a_load(tmp_path):
    """旧配置里的疲劳键加载后必须消失（schema 已经不认识它们）。"""
    import asyncio
    import json

    from plugin.plugins.qq_auto_reply.config_store import QQAutoReplyConfigStore

    (tmp_path / "business_config.json").write_text(
        json.dumps({"fatigue_enabled": True, "fatigue_session_per_reply": 9.0, "reply_mode": "text"}),
        encoding="utf-8",
    )
    loaded = asyncio.run(QQAutoReplyConfigStore(tmp_path).load())
    assert "fatigue_enabled" not in loaded
    assert "fatigue_session_per_reply" not in loaded
