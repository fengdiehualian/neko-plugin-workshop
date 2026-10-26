import pytest
from plugin.plugins.neko_rimworld.agent.context.model_context import (
    RETIRED_ENVIRONMENT_KEYS,
    validate_deployment,
)
from plugin.plugins.neko_rimworld.agent.model.model_client import (
    AgentModelSettings,
    build_agent_model_settings,
    parse_agent_model_settings,
)
from plugin.plugins.neko_rimworld.agent.runtime.audit import AgentAuditLedger
from plugin.plugins.neko_rimworld.agent.runtime.session import AgentRunSession


@pytest.fixture(autouse=True)
def no_retired_environment(monkeypatch):
    for name in RETIRED_ENVIRONMENT_KEYS:
        monkeypatch.delenv(name, raising=False)


def test_default_session_has_json_projection_without_environment():
    session = AgentRunSession.create(ledger=AgentAuditLedger("question"), question="question")
    assert session.context_pruner is not None
    assert not hasattr(session, "policy") and not hasattr(session, "input_budget")


@pytest.mark.parametrize("name", RETIRED_ENVIRONMENT_KEYS)
@pytest.mark.parametrize("value", ["", " ", "0", "1", "false", "true", "None", "null", "off", "invalid"])
def test_every_retired_environment_key_is_rejected_by_presence(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name + " is retired"):
        validate_deployment()
    # A default session, with no model configuration at all, must also fail.
    with pytest.raises(ValueError, match=name + " is retired"):
        AgentRunSession.create(ledger=AgentAuditLedger("question"), question="question")


@pytest.mark.parametrize("name", ["review_before_delivery", "allow_text_completion", "prepared_calls", "preferred_cycles"])
@pytest.mark.parametrize("value", [None, False, True, "", " ", 0, 1, [], {}])
@pytest.mark.parametrize("configured", [False, True])
def test_retired_agent_key_cannot_hide_behind_an_empty_model(name, value, configured):
    agent = {"base_url": "http://127.0.0.1:11434/v1", "model": "local", "api_key": ""} if configured else {}
    agent[name] = value
    settings, errors = parse_agent_model_settings({"neko_rimworld": {"agent": agent}})
    assert settings == AgentModelSettings()
    assert errors == [f"{name} is retired; remove it from the agent configuration."]


def test_current_model_configuration_is_unchanged():
    agent = {
        "base_url": "http://127.0.0.1:11434/v1/",
        "model": "local",
        "api_key": "",
        "request_timeout_seconds": 50.0,
        "run_budget_seconds": 80.0,
        "max_output_tokens": 4096,
        "temperature": 0.5,
        "connect_timeout_seconds": 3.0,
        "first_response_timeout_seconds": 20.0,
        "stream_idle_timeout_seconds": 10.0,
        "finalization_reserve_seconds": 5.0,
        "stream_include_usage": False,
    }
    settings, errors = parse_agent_model_settings({"neko_rimworld": {"agent": agent}})
    assert settings == build_agent_model_settings(**agent)
    assert errors == []
    assert parse_agent_model_settings({}) == (AgentModelSettings(), [])
    assert parse_agent_model_settings({"neko_rimworld": {"agent": {"base_url": "", "model": "", "api_key": ""}}}) == (
        AgentModelSettings(), []
    )
