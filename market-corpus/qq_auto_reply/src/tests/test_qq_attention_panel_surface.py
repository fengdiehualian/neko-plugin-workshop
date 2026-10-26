# -*- coding: utf-8 -*-
"""D 档契约：面板只留少数几个「用户能自己推理」的行为旋钮，被冻结的 15 个键仍在、仍可改配置文件。"""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import tempfile

import pytest

PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[1]

#: 面板保留的行为旋钮（前端控件 id）。
#: 加一个进来必须是有意的：这条测试就是那道"你确定要把它搬回界面吗"的关卡。
PANEL_CONTROLS = {
    "cfg-att-focus-threshold",         # 焦点线（跨群取舍删除后 = 分数档位线）
    "cfg-att-focus-send",              # 本群在聊的线（原「焦点保持线」）
    "cfg-att-lock-seconds",            # @ 锁时长
    "cfg-att-freq-target-gap",         # 目标消息间隔（频率自适应的参照）
    "cfg-att-freq-min-mult",           # 频率倍率下限
    "cfg-att-freq-max-mult",           # 频率倍率上限
    "cfg-att-emotion-multipliers",     # 情绪倍率表
    # 2026-09-29 加：按群维护循环的 tick 间隔。它不只是"多久算一次"——破冰是
    # **一轮最多一次**，所以这个值同时决定了她主动开口的最密节奏，用户能推理出来。
    "cfg-att-maintenance-interval",
}

#: 被冻结（撤下面板）的键：仍在 schema 与 defaults 里，仍从配置文件读取。
#: 2026-09-29 起少了三个 —— `attention_honeymoon_seconds` / `attention_fall_seconds` /
#: `attention_fall_boost_attenuation` 随 rise/fall 相位机一起**退役**
#: （进 `config_store._LEGACY_ZOMBIE_KEYS`）。相位机的前提是"同一时刻只有一个群能
#: 说话"，跨群取舍删除后热度只由「这个群最后一条消息离现在多远」决定。
FROZEN_KEYS = (
    "attention_max_score",
    "attention_min_threshold",
    "attention_batch_message_gain",
    "attention_base_rise_rate",
    "attention_message_boost",
    "attention_keyword_boost_ratio",
    "attention_fall_rate",
    "attention_consume_ratio",
    "attention_at_bot_boost",
    "attention_question_boost",
    "attention_wake_boost_ratio",
    "attention_decay_interval_seconds",
)


def _panel_ids() -> set[str]:
    html = (PLUGIN_DIR / "static" / "napcat.html").read_text(encoding="utf-8")
    return set(re.findall(r"""["'](cfg-att-[a-z0-9-]+)["']""", html))


def test_panel_exposes_exactly_the_agreed_behavior_knobs():
    """面板可调面 = `PANEL_CONTROLS` 那几个。多一个就说明有人把内部量又搬回界面了。"""
    assert _panel_ids() == PANEL_CONTROLS, (
        f"面板控件与契约不符：多了 {sorted(_panel_ids() - PANEL_CONTROLS)}，"
        f"少了 {sorted(PANEL_CONTROLS - _panel_ids())}"
    )


def test_frozen_keys_still_exist_in_schema_defaults():
    """「撤下面板」不等于删键：这些键仍在 defaults 里（否则老配置里的值会被丢弃）。"""
    from plugin.plugins.qq_auto_reply import settings_schema

    defaults = settings_schema.defaults()
    missing = [k for k in FROZEN_KEYS if k not in defaults]
    assert not missing, f"被冻结的键从 defaults 里消失了：{missing}"


def test_frozen_keys_have_no_panel_control_but_stay_readable():
    """冻结 = 没有 UIInput；键本身仍存在且可从配置文件读（可逆的简化）。"""
    from plugin.plugins.qq_auto_reply import settings_schema

    for key in FROZEN_KEYS:
        spec = settings_schema.BY_KEY[key]
        assert spec.ui is None, f"{key} 又被挂上面板控件了"
        assert spec.default is not None, f"{key} 的默认值丢了"
    # 保留的旋钮反过来必须有控件
    for key in ("attention_focus_threshold", "attention_lock_seconds", "attention_emotion_multipliers"):
        assert settings_schema.BY_KEY[key].ui is not None, f"{key} 的控件丢了"


def test_panel_javascript_parses():
    """面板里的 JS 必须能被 JS 引擎解析。

    为什么值得一条测试：`loadAttention()` 那种压缩成一行的渲染代码里，删掉一个条件渲染
    （比如这次删掉的「焦点」标识）极容易**把字符串字面量的引号配错** —— 引号一错，整段
    脚本静默失效：面板点开是空白、只有控制台报错，而服务端测试全绿。本仓库没有 JS 工具链，
    所以直接借 node 的 `--check`；环境里没有 node 就跳过（市场 CI 不一定装 node，
    跳过比误报好）。
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("环境里没有 node，跳过 JS 语法检查")

    html = (PLUGIN_DIR / "static" / "napcat.html").read_text(encoding="utf-8")
    blocks = re.findall(r"<script[^>]*>(.*?)</script>", html, re.S)
    assert blocks, "面板里一段 <script> 都没有？"

    with tempfile.TemporaryDirectory() as tmp:
        for index, body in enumerate(blocks):
            path = pathlib.Path(tmp) / f"block{index}.js"
            path.write_text(body, encoding="utf-8")
            proc = subprocess.run(
                [node, "--check", str(path)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            assert proc.returncode == 0, (
                f"面板第 {index} 段 <script> 语法错误：\n{proc.stderr.strip()[:800]}"
            )


def test_frozen_defaults_keep_their_values():
    """冻结只是不暴露，**值不能被顺手改掉** —— 这里钉住几个关键默认值。

    ⚠️ `attention_consume_ratio` 是**使用者点名改过**的那一个（2026-09-29：
    「满 10 的注意力发 6 条就踩线了」→ 0.10 降到 0.05）。改它要有口径，
    不是顺手；改完请同步 `tests/test_qq_attention_behavior.py`
    （`test_full_attention_now_survives_twelve_replies` 把 12 条 / 16 条钉死了）。
    """
    from plugin.plugins.qq_auto_reply import settings_schema

    expected = {
        "attention_max_score": 10.0,
        "attention_at_bot_boost": 3.0,
        "attention_question_boost": 1.5,
        "attention_consume_ratio": 0.05,
    }
    defaults = settings_schema.defaults()
    for key, value in expected.items():
        assert defaults[key] == value, f"{key} 的默认值被改动：{defaults[key]} != {value}"
