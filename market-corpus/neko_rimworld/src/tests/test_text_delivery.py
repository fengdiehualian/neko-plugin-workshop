"""Current text delivery through the real unchanged host transport."""
import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from plugin.plugins.neko_rimworld.agent.delivery.handoff import count_handoff_tokens, validate_submission
from utils.result_parser import parse_plugin_result


def test_text_budget_fits_the_real_host_parser_without_truncation():
    from config import TASK_DETAIL_MAX_TOKENS
    from plugin.plugins.neko_rimworld.agent.delivery.handoff import HANDOFF_TOKEN_BUDGET

    assert HANDOFF_TOKEN_BUDGET <= TASK_DETAIL_MAX_TOKENS
    exact = (" x" * HANDOFF_TOKEN_BUDGET).strip()
    assert count_handoff_tokens(exact)[0] == HANDOFF_TOKEN_BUDGET
    submission = validate_submission("完整正文与条件", exact)
    assert parse_plugin_result({"summary": submission["agent_text"]}, llm_result_fields=["summary"], lang="zh") == exact


@pytest.fixture
def host_transport(monkeypatch):
    """Execute unchanged host emitter/renderer; replace only network and clock.

    Isolated package loading avoids starting main_server or model services. The
    real tokenizer, budgets, result parser, emitter and callback renderer run.
    """
    root = Path(__file__).resolve().parents[4]
    namespace = "_rimworld_delivery_host"
    package = ModuleType(namespace)
    package.__path__ = []
    monkeypatch.setitem(sys.modules, namespace, package)
    events = []

    class Bridge:
        async def emit_to_main(self, event):
            events.append(event)
            return True

    shared = ModuleType(namespace + "._shared")
    shared.logger = logging.getLogger(namespace)
    shared.Modules = SimpleNamespace(agent_bridge=Bridge())
    monkeypatch.setitem(sys.modules, shared.__name__, shared)
    registry = ModuleType(namespace + ".registry")
    registry._now_iso = lambda: "2026-09-19T00:00:00Z"
    monkeypatch.setitem(sys.modules, registry.__name__, registry)

    def load(name, relative):
        spec = importlib.util.spec_from_file_location(namespace + "." + name, root / relative)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        return module

    emitter = load("results", "app/agent_server/results.py")
    renderer = load("callback_render", "main_logic/core/callback_render.py")
    return emitter, renderer, events


@pytest.mark.asyncio
async def test_text_delivery_preserves_full_report_and_host_callback(host_transport):
    submission = validate_submission("结论。\n\n完整报告与全部条件。", "结论。")
    result = {"summary": submission["agent_text"], "answer": submission["full_text"]}
    projected = parse_plugin_result(result, llm_result_fields=["summary"], lang="zh")
    assert projected == "结论。" and result["answer"].endswith("全部条件。")
    emitter, renderer, events = host_transport
    await emitter._emit_task_result("小天", channel="user_plugin", task_id="test", success=True,
        summary=projected, detail=projected, source_kind="plugin", source_name="RimWorld")
    (event,) = events
    assert event["summary"] == event["detail"] == projected
    from config import AGENT_CALLBACK_TEXT_MAX_TOKENS, AGENT_CALLBACK_TOTAL_MAX_TOKENS
    assert count_handoff_tokens(projected)[0] <= AGENT_CALLBACK_TEXT_MAX_TOKENS
    selected, deferred = renderer._select_callbacks_within_token_budget(
        [{**event, "origin": "task_result"}], AGENT_CALLBACK_TOTAL_MAX_TOKENS)
    assert not deferred
    prompt = renderer._build_callback_instruction(selected, lang="zh", lanlan_name="小天", master_name="测试者", passive=True)
    assert projected in prompt and "全部条件" not in prompt
