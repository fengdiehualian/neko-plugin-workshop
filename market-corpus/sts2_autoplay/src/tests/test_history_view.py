"""自动游玩历史页的数据层：把猫娘记忆投影成前端 JSON，并落成静态快照。

前端页面是插件服务器按静态文件发的（`GET /plugin/{id}/ui/<path>`），没有"直读插件内存"的通道，
所以每次局末把记录投影成 `static/autoplay_history.json`；权威数据仍在 `catgirl_memory.json`。
页面要展示的三样正是用户点名的：**做出的选择、损血情况、结束状态**。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from plugin.plugins.sts2_autoplay.history_view import (
    HISTORY_FILENAME,
    build_history_payload,
    write_history_view,
)

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _run(run_id: str, *, outcome: str = "defeat", floor: int = 7, reflection: str = "") -> dict:
    return {
        "run_id": run_id,
        "character": "绯刃战姬",
        "outcome": outcome,
        "floor": floor,
        "act": 1,
        "ended_at": 1700000000.0,
        "lessons": ["血量过低时别贪输出"],
        "preferences": ["偏好这类选择：play_card"],
        "reflection": reflection,
        "hp": {
            "total_lost": 96,
            "loss_events": 7,
            "min_hp": 0,
            "max_hp": 80,
            "final_hp": 0,
            "heaviest": [{"screen": "COMBAT", "floor": 6, "lost": 42, "hp_before": 80, "hp_after": 38}],
        },
        "decisions": [{"screen": "COMBAT", "action_type": "play_card", "reason": "贪输出", "step_count": 12}],
    }


@pytest.mark.unit
def test_payload_exposes_choices_losses_and_outcome() -> None:
    payload = build_history_payload([_run("r1", reflection="下次先垫格挡喵")])

    assert payload["summary"]["runs"] == 1
    assert payload["summary"]["defeats"] == 1
    assert payload["summary"]["total_hp_lost"] == 96

    run = payload["runs"][0]
    assert run["outcome"] == "defeat" and run["character"] == "绯刃战姬" and run["floor"] == 7
    assert run["hp"]["total_lost"] == 96 and run["hp"]["min_hp"] == 0
    assert run["heaviest"][0]["lost"] == 42 and run["heaviest"][0]["floor"] == 6
    assert run["decisions"][0]["action_type"] == "play_card"
    assert run["reflection"] == "下次先垫格挡喵"


@pytest.mark.unit
def test_newest_run_comes_first() -> None:
    payload = build_history_payload([_run("old"), _run("new")])

    assert [run["run_id"] for run in payload["runs"]] == ["new", "old"]


@pytest.mark.unit
def test_summary_counts_victories_and_best_floor() -> None:
    payload = build_history_payload([_run("a", outcome="defeat", floor=7), _run("b", outcome="victory", floor=17)])

    assert payload["summary"]["victories"] == 1 and payload["summary"]["defeats"] == 1
    assert payload["summary"]["best_floor"] == 17


@pytest.mark.unit
def test_payload_survives_garbage_records() -> None:
    payload = build_history_payload([{}, "nonsense", None])  # type: ignore[list-item]

    assert payload["runs"][0]["run_id"] == ""
    assert payload["summary"]["runs"] == 0 or payload["summary"]["runs"] == 1


@pytest.mark.unit
def test_write_history_view_writes_json_next_to_static(tmp_path) -> None:
    path = write_history_view([_run("r1")], directory=str(tmp_path))

    assert path and os.path.basename(path) == HISTORY_FILENAME
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)  # 必须是**合法 JSON**，页面直接 res.json()
    assert payload["runs"][0]["run_id"] == "r1"
    assert not os.path.exists(path + ".tmp"), "临时文件必须被 replace 掉"


@pytest.mark.unit
def test_write_history_view_never_raises_on_bad_directory(tmp_path) -> None:
    """写不进去也不能影响游玩（这是纯展示用的快照）。"""
    blocked = tmp_path / "file-not-dir"
    blocked.write_text("x", encoding="utf-8")

    assert write_history_view([_run("r1")], directory=str(blocked)) is None


# ---- 入口必须做在 index.html 里 ----------------------------------------------------------


@pytest.mark.unit
def test_index_html_links_to_the_autoplay_history_page() -> None:
    """**"index.html 没有历史入口"的回归测试。**

    宿主打开的就是 `/plugin/{id}/ui/`，也就是 `index.html`；而它**并不消费** plugin.toml 里额外的
    `[[plugin.ui.panel]]`（plugin server 里没有任何 panels 处理）—— 所以只在清单里加第二个面板，
    玩家在界面上是看不到的。入口必须做在 index.html 里。

    但 index.html 要**保持干净**（玩家要求）：只放一个跳转卡片，不内联取数/渲染逻辑
    ——页面一旦变重会出现刷新延迟，明细都留在 history.html 里。
    """
    html = (PLUGIN_ROOT / "static" / "index.html").read_text(encoding="utf-8")

    assert "history.html" in html, "启动页必须有历史页入口"
    assert "fetch(" not in html, "启动页不要内联取数（保持干净，避免刷新延迟）"
    assert len(html) < 3000, f"启动页应该保持小巧，现在 {len(html)} 字节"


@pytest.mark.unit
def test_history_page_uses_the_snapshot_next_to_it() -> None:
    """历史页取的是同目录下的快照（相对路径），换目录部署也不会断。"""
    html = (PLUGIN_ROOT / "static" / "history.html").read_text(encoding="utf-8")

    assert "autoplay_history.json" in html
    assert HISTORY_FILENAME in "autoplay_history.json"


@pytest.mark.unit
def test_plugin_toml_declares_the_history_panel() -> None:
    """清单里也留一份（有些 UI 消费者会用），但不能只靠它。"""
    text = (PLUGIN_ROOT / "plugin.toml").read_text(encoding="utf-8")

    assert 'id = "history"' in text and "static/history.html" in text
