"""Keep HSR direct tools/events, exclude duplicate after-turn task dispatch."""

from __future__ import annotations

import asyncio
import tomllib
from pathlib import Path

from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture


def test_passive_manifest_does_not_disable_startup_or_direct_tools():
    from plugin.plugins.hsr_companion import HsrCompanionPlugin
    from plugin.sdk.plugin.llm_tool import collect_llm_tool_methods

    manifest = tomllib.loads((Path(__file__).resolve().parents[1] / "plugin.toml").read_text(encoding="utf-8"))
    assert manifest["plugin"]["passive"] is True
    assert manifest["plugin_runtime"]["enabled"] and manifest["plugin_runtime"]["auto_start"]
    tools = collect_llm_tool_methods(object.__new__(HsrCompanionPlugin))
    assert {"hsr_companion_prepare", "hsr_lookup", "hsr_analyze_team"} <= {meta.name for meta, _ in tools}


def test_host_background_assessment_excludes_this_manifest_without_llm():
    from brain.task_executor import DirectTaskExecutor

    manifest = tomllib.loads((Path(__file__).resolve().parents[1] / "plugin.toml").read_text(encoding="utf-8"))
    owner = object.__new__(DirectTaskExecutor)
    # If dispatch is not structurally filtered, this object lacks any LLM,
    # credentials or automation client and the assessment cannot continue.
    result = asyncio.run(owner._assess_user_plugin("当前任务要做什么", [manifest["plugin"]], lang="zh"))
    assert not result.has_task and not result.can_execute


def test_no_knowledge_match_is_not_game_task_completion(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    value = owner._lookup_payload("未收录的任务目标")
    assert value["count"] == 0 and value["results"] == []
    assert "未找到匹配资料" in value["notice"] and "游戏任务完成状态未知" in value["notice"]
