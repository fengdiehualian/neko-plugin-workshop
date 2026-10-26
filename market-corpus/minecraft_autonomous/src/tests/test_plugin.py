from plugin.sdk.plugin import Err, Ok


def assert_unavailable(result):
    assert isinstance(result, Ok)
    assert result.value["backend_ready"] is False
    assert result.value["game_connected"] is False
    assert result.value["autonomy_running"] is False
    assert result.value["reason"] == "backend_missing"


def test_startup_and_status_do_not_start_gameplay(plugin_instance, run_async):
    assert_unavailable(run_async(plugin_instance.on_startup()))
    assert_unavailable(run_async(plugin_instance.status()))


def test_start_reports_missing_backend_without_claiming_success(plugin_instance, run_async):
    result = run_async(plugin_instance.start(goal="survive"))
    assert isinstance(result, Err)
    assert result.error["code"] == "autonomy_disabled"
    assert_unavailable(run_async(plugin_instance.status()))


def test_stop_is_safe_before_start_and_when_repeated(plugin_instance, run_async):
    first = run_async(plugin_instance.stop())
    second = run_async(plugin_instance.stop())
    assert_unavailable(first)
    assert first == second


def test_shutdown_remains_stopped_after_failed_start(plugin_instance, run_async):
    assert isinstance(run_async(plugin_instance.start(goal="collect wood")), Err)
    assert_unavailable(run_async(plugin_instance.on_shutdown()))
    assert_unavailable(run_async(plugin_instance.status()))


def test_stop_persists_and_wins_over_inflight_start(plugin_instance, run_async, monkeypatch):
    import asyncio
    import json

    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed(**_):
            entered.set()
            await release.wait()
            return {"autonomy": {"enabled": True}, "server": {"host": "example.test", "username": "Alice"},
                    "backend": {"directory": "explicit-test-backend"}}

        monkeypatch.setattr(plugin_instance.config, "dump", delayed)
        module = __import__(plugin_instance.__class__.__module__)
        monkeypatch.setattr(module, "verify_runtime", lambda _: plugin_instance.data_path("unused.mjs"))
        task = asyncio.create_task(plugin_instance.start())
        await entered.wait()
        stopping = asyncio.create_task(plugin_instance.stop())
        await asyncio.sleep(0)
        release.set()
        result = await task
        await stopping
        assert isinstance(result, Err)
        assert result.error["code"] == "start_cancelled"
        assert plugin_instance._worker is None
        assert json.loads(plugin_instance.data_path("operator.json").read_text())["explicit_stop"]

    run_async(exercise())


def test_external_shutdown_is_read_only_and_keeps_framework_ownership(plugin_instance, run_async, monkeypatch, tmp_path):
    async def config(**_):
        return {"runtime": {"mode": "external", "data_directory": str(tmp_path / "separate-runtime")},
                "autonomy": {"enabled": True}, "server": {"host": "example.test", "username": "Alice"}}
    monkeypatch.setattr(plugin_instance.config, "dump", config)
    result = run_async(plugin_instance.on_shutdown())
    assert isinstance(result, Ok) and result.value["mode"] == "external"
    assert result.value["observation"]["phase"] == "not_observed"
    assert not (tmp_path / "separate-runtime").exists()
    assert plugin_instance._worker is None


def test_natural_feedback_entry_does_not_call_model_game_or_voice(plugin_instance, run_async, monkeypatch, tmp_path):
    import uuid

    async def config(**_):
        return {"runtime": {"mode": "external", "data_directory": str(tmp_path / "separate-runtime")},
                "autonomy": {"enabled": True}, "server": {"host": "example.test", "username": "Alice"}}
    monkeypatch.setattr(plugin_instance.config, "dump", config)
    request_id = str(uuid.uuid4())
    result = run_async(plugin_instance.feedback(goal="别挖了，回家", mode="replace", request_id=request_id))
    assert isinstance(result, Ok) and result.value["delivery"] == "queued"
    assert run_async(plugin_instance.feedback(goal="别挖了，回家", mode="replace", request_id=request_id)) == result
    receipt = run_async(plugin_instance.task_receipt(request_id=request_id))
    assert receipt.value["command"]["delivery"] == "queued"


def test_main_brain_intent_uses_same_mailbox_and_public_sdk(plugin_instance, run_async, monkeypatch, tmp_path):
    import uuid

    from plugin.sdk.plugin.llm_tool import collect_llm_tool_methods

    async def config(**_):
        return {"runtime": {"mode": "external", "data_directory": str(tmp_path / "runtime")},
                "autonomy": {"enabled": True}, "server": {"host": "example.test", "username": "Alice"}}

    monkeypatch.setattr(plugin_instance.config, "dump", config)
    names = {meta.name for meta, _ in collect_llm_tool_methods(plugin_instance)}
    assert names == {"minecraft_autonomy_status", "minecraft_autonomy_intent", "minecraft_autonomy_stop", "minecraft_autonomy_review"}
    request = str(uuid.uuid4())
    response = run_async(plugin_instance.minecraft_autonomy_intent("collect wood", "insert", request))
    assert response["delivery"] == "queued"
    assert run_async(plugin_instance.minecraft_autonomy_intent("collect wood", "insert", request)) == response
    snapshot = run_async(plugin_instance.minecraft_autonomy_status(request_id=request))
    assert snapshot["phase"] == "not_observed"
    assert snapshot["command"]["delivery"] == "queued"
    assert snapshot["command"]["planner_report"]["verified"] is False
    assert plugin_instance._worker is None


def test_main_brain_status_pages_full_fact_without_repeating_it(plugin_instance, run_async, monkeypatch):
    async def status(**_):
        return Ok({"observation": {"phase": "running", "source": "last_observed_snapshot",
                   "facts": {"parts": [{"key": "gear", "text": "木板" * 1400, "original": "log:gear"}]}}})

    monkeypatch.setattr(plugin_instance, "status", status)
    summary = run_async(plugin_instance.minecraft_autonomy_status())
    assert len(summary["facts"][0]["text"]) == 240 and summary["facts"][0]["has_more"]
    page = run_async(plugin_instance.minecraft_autonomy_status(fact_key="gear", offset=1200))
    assert page["text"] == ("木板" * 1400)[1200:2400]
    assert page["has_more"] and page["next_offset"] == 2400
    assert page["source"] == "last_observed_snapshot"


def test_panel_user_origin_does_not_relabel_legacy_or_model_commands(plugin_instance, run_async, monkeypatch, tmp_path):
    import uuid

    from agent_mc_autonomy.mailbox import Mailbox

    raw = {"runtime": {"mode": "external", "data_directory": str(tmp_path / "runtime")},
           "autonomy": {"enabled": True}, "server": {"host": "example.test", "username": "Alice"}}
    async def config(**_):
        return raw
    monkeypatch.setattr(plugin_instance.config, "dump", config)
    settings = plugin_instance.__class__.__module__
    module = __import__(settings)
    parsed = module.Settings.parse(raw)
    box = Mailbox(parsed.data_root(plugin_instance.data_path()) / "sessions" / parsed.identity / "host")
    user, legacy, model = (str(uuid.uuid4()) for _ in range(3))
    assert run_async(plugin_instance.panel_intent("food", "insert", user)).value["delivery"] == "queued"
    assert box.goal_lineage(user)[0]["origin"] == "user"
    assert run_async(plugin_instance.feedback("old goal", "insert", legacy)).value["delivery"] == "queued"
    assert run_async(plugin_instance.panel_intent("old goal", "insert", legacy)).value["delivery"] == "queued"
    assert box.goal_lineage(legacy)[0]["origin"] == "unknown"
    assert run_async(plugin_instance.minecraft_autonomy_intent("shelter", "insert", model))["delivery"] == "queued"
    assert box.goal_lineage(model)[0]["origin"] == "unknown"
