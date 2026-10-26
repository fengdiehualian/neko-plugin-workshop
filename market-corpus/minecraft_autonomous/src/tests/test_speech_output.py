import asyncio
import importlib
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
import pytest
from test_heartbeat import setup

PLACEHOLDERS = [
    "[No commentary needed this round.]",
    "[neko_emotion:neutral] [No commentary needed this round.]",
    "[[ (No commentary needed this round.) ]]",
    "【「No commentary needed this round.」】",
    '{"No commentary needed this round."}',
    "<‘No commentary needed this round.’>",
    "`[No commentary needed this round.]`",
    "（“No commentary needed this round.”）",
    "[No comment this round.]", "(no-comment)",
    "[No new comment this round.]", "[[no-new-comment]]",
    "【No new commentary needed for this round.】",
    "[tts_style:neutral] [No new comments this round.]",
    "[[本轮无需口播。]]", "（无需回复）",
    "\u3000No commentary needed this round.\u3000",
    "\u00a0No commentary needed this round.\u00a0",
    "\u3000[\u00a0No commentary needed this round.\u3000]\u00a0",
    "[neko_emotion:neutral]\u00a0【\u3000无需回复\u00a0】\u3000",
    "No commentary needed\nthis round.",
    "No\ncommentary needed this round.",
    "\u3000[[No\ncommentary needed\nthis round.]]\u00a0",
    "[tts_style:neutral] (No new\r\ncomment this round.)",
]


@pytest.mark.parametrize("text", PLACEHOLDERS)
def test_wrapped_placeholder_is_empty(plugin_module, text):
    mod = importlib.import_module(plugin_module.__name__ + ".neko_speech")
    assert mod.sanitize_speech(text) == ""


@pytest.mark.parametrize(("text", "expected"), [
    ("No commentary needed this round.", ""), (" no comment this round ", ""),
    ("无需回复", ""), ("", ""), ("[neko_emotion:happy|轻快]去找棵树吧。", "去找棵树吧。"),
    ("[[tts_style:bright]][voice_style:new]Hello Minecraft!", "Hello Minecraft!"),
    ("[neko_\nemotion:happy]Let's go!", "Let's go!"),
    ("[neko_future_style: secret", ""), ("<tts_style>quiet</tts_style>来搭个小屋。", "来搭个小屋。"),
    ("I like [Minecraft] and No Man's Sky.", "I like [Minecraft] and No Man's Sky."),
    ("No commentary about that creeper could do it justice.", "No commentary about that creeper could do it justice."),
    ("(No commentary about that creeper could do it justice.)", "(No commentary about that creeper could do it justice.)"),
    ("No new comments from the villagers yet.", "No new comments from the villagers yet."),
    ("‘Minecraft is lovely tonight.’", "‘Minecraft is lovely tonight.’"),
    ("[No commentary needed this round.]\nLet's find a tree.", "Let's find a tree."),
    ("\u3000No commentary needed this round.\u00a0\nLet's find a tree.", "Let's find a tree."),
    ("\u3000‘Minecraft 今晚真漂亮。’\u00a0", "\u3000‘Minecraft 今晚真漂亮。’\u00a0"),
    ("No\ncommentary about that creeper could do it justice.", "No\ncommentary about that creeper could do it justice."),
    ("‘桦木林真安静。’\nLet's build here.", "‘桦木林真安静。’\nLet's build here."),
])
def test_owned_boundary_preserves_speech_and_removes_controls(plugin_module, text, expected):
    mod = importlib.import_module(plugin_module.__name__ + ".neko_speech")
    assert mod.sanitize_speech(text) == expected


def prepare(plugin, monkeypatch):
    port, snapshot, _ = setup(plugin, monkeypatch)
    mod = importlib.import_module(plugin.__class__.__module__ + ".neko_speech")
    raw = {"autonomy": {"enabled": True}, "server": {"host": "example.invalid", "username": "TestBot"},
           "heartbeat": {"enabled": True, "target": "TestRole"},
           "speech_output": {"enabled": True, "base_url": "http://127.0.0.1:32123"}}
    monkeypatch.setattr(plugin.config, "dump", AsyncMock(return_value=raw))
    port.observe(snapshot)
    review = port.offer()["review"]
    port.decide(review["id"], "inspect")
    return mod, port, snapshot, review, raw


@pytest.mark.parametrize("text", PLACEHOLDERS)
def test_wrapped_placeholder_never_schedules_output(plugin_instance, run_async, monkeypatch, text):
    _, port, _, review, _ = prepare(plugin_instance, monkeypatch)
    result = run_async(plugin_instance.minecraft_autonomy_review(
        review["id"], "insert", "Need shelter", "Build shelter", spoken_text=text))
    assert result["speech"]["status"] == "empty_or_control_only"
    assert result["delivery"] == "queued"
    assert not getattr(plugin_instance, "_speech_output_tasks", None)
    assert not (port.directory / "speech-output.json").exists()


@pytest.mark.parametrize("outcome", ["accepted", "busy", "foreign", "unknown", "unsupported"])
def test_public_output_order_receipts_and_no_replay(plugin_instance, run_async, monkeypatch, outcome):
    mod, port, _, review, _ = prepare(plugin_instance, monkeypatch)
    calls = []
    client_class = httpx.AsyncClient
    async def handler(request):
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"paths": {} if outcome == "unsupported" else {"/api/game/{game_type}/speak": {"post": {}}}})
        assert port.mailbox.get(port.request_id(review["id"]))["delivery"] == "queued"
        body = json.loads(request.content)
        assert body["line"] == "先搭个小屋吧。"
        assert body["lanlan_name"] == "TestRole"
        assert body["session_id"].startswith("minecraft-")
        assert not body["interrupt_audio"] and not body["emit_turn_end"] and body["wait_for_audio_completion"]
        assert "audio_completion_timeout" not in body  # The public host owns its completion bound.
        if outcome == "unknown":
            raise httpx.ReadTimeout("synthetic unknown")
        return httpx.Response(200, json={"ok": outcome == "accepted", "audio_completed": outcome == "accepted",
            "reason": "route_owned_by_other_game" if outcome == "foreign" else "busy"})
    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda **kw: client_class(transport=httpx.MockTransport(handler), **kw))

    async def scenario():
        result = await plugin_instance.minecraft_autonomy_review(review["id"], "insert", "Need shelter", "Build shelter",
                                                                spoken_text="[neko_emotion:happy]先搭个小屋吧。")
        assert result["speech"]["status"] == "enqueued" and result["delivery"] == "queued"
        assert calls == []  # No HTTP or synthesis is awaited by the action admission.
        await asyncio.gather(*plugin_instance._speech_output_tasks)
        repeat = await plugin_instance.minecraft_autonomy_review(review["id"], "insert", "Need shelter", "Build shelter",
                                                                 spoken_text="先搭个小屋吧。")
        assert repeat["request_id"] == result["request_id"]
    run_async(scenario())
    stored = json.loads((port.directory / "speech-output.json").read_text(encoding="utf-8"))["records"][review["id"]]
    assert stored["status"] == {"accepted": "accepted", "busy": "rejected", "foreign": "rejected", "unknown": "unknown", "unsupported": "unsupported"}[outcome]
    assert stored["transport_accepted"] == (outcome == "accepted") and not stored["playback_verified"]
    if outcome in {"accepted", "busy", "foreign"}:
        assert stored["audio_completed"] == (outcome == "accepted")
    else:
        assert "audio_completed" not in stored
    assert len(calls) == (1 if outcome == "unsupported" else 2)
    assert all(request.url.path in {"/openapi.json", "/api/game/minecraft_autonomous/speak"} for request in calls)


@pytest.mark.parametrize("boundary", ["stop", "generation", "user", "expired", "scope"])
def test_queued_output_rechecks_boundaries_before_sending(plugin_instance, run_async, monkeypatch, boundary):
    _, port, snapshot, review, _ = prepare(plugin_instance, monkeypatch)
    async def scenario():
        result = await plugin_instance.minecraft_autonomy_review(review["id"], "continue", "Keep working", spoken_text="再找找看。")
        assert result["speech"]["status"] == "enqueued"
        if boundary == "stop":
            plugin_instance._operator_state(True)
        elif boundary == "generation":
            plugin_instance._generation += 1
        elif boundary == "user":
            import uuid
            port.mailbox.submit("feedback", {"goal": "Follow user", "mode": "insert"}, str(uuid.uuid4()))
        elif boundary == "expired":
            port.now = lambda: 10200
        else:
            snapshot["game_identity"] = {"realm": "other", "account": "other", "agent": "other"}
            (port.directory / "status.json").write_text(json.dumps(snapshot), encoding="utf-8")
        await asyncio.gather(*plugin_instance._speech_output_tasks)
    run_async(scenario())
    record = json.loads((port.directory / "speech-output.json").read_text(encoding="utf-8"))["records"][review["id"]]
    assert record["status"] == "rejected" and not record["transport_accepted"]


def test_idle_chat_and_strategy_turns_have_natural_roles(plugin_instance, run_async, monkeypatch):
    port, snapshot, push = setup(plugin_instance, monkeypatch)
    raw = run_async(plugin_instance.config.dump())
    raw["heartbeat"]["idle_presence_seconds"] = 45
    from agent_mc_autonomy.review import ReviewPolicy
    port.configure(ReviewPolicy(enabled=True))
    port.observe(snapshot)
    first = run_async(plugin_instance.game_decision_heartbeat()).value["heartbeat_id"]
    run_async(plugin_instance.minecraft_autonomy_review(first, "inspect"))
    run_async(plugin_instance.minecraft_autonomy_review(first, "continue", "Useful work"))
    port.now = lambda: 10045
    snapshot["updated_at"] = datetime.fromtimestamp(10045, timezone.utc).isoformat()
    (port.directory / "status.json").write_text(json.dumps(snapshot), encoding="utf-8")
    port.observe(snapshot)
    result = run_async(plugin_instance.game_decision_heartbeat()).value
    assert result["trigger"] == "idle_presence" and not result["playback_verified"]
    text = push.call_args.kwargs["parts"][0]["text"]
    assert push.call_args.kwargs["ai_behavior"] == "respond"
    assert "好奇" in text and "调用工具前不说话" not in text and "无需口播" not in text


def test_disabled_output_and_final_answer_contract(plugin_instance, run_async, monkeypatch):
    mod, port, _, review, raw = prepare(plugin_instance, monkeypatch)
    raw["speech_output"]["enabled"] = False
    result = run_async(plugin_instance.minecraft_autonomy_review(review["id"], "continue", "Keep working", spoken_text="一起走吧。"))
    assert result["speech"]["status"] == "disabled"
    assert not (port.directory / "speech-output.json").exists()
    # The public tool sees only its argument; host future final text is not intercepted.
    host_final = "No commentary needed this round."
    assert mod.sanitize_speech("一起走吧。") == "一起走吧。"
    assert host_final == "No commentary needed this round."


def test_slow_output_is_independent_and_cancelled_post_remains_unknown(plugin_instance, run_async, monkeypatch):
    mod, port, _, review, _ = prepare(plugin_instance, monkeypatch)
    client_class = httpx.AsyncClient
    async def scenario():
        entered, finish = asyncio.Event(), asyncio.Event()
        async def handler(request):
            if request.method == "GET":
                return httpx.Response(200, json={"paths": {"/api/game/{game_type}/speak": {"post": {}}}})
            entered.set()
            await finish.wait()
            return httpx.Response(200, json={"ok": True})
        monkeypatch.setattr(mod.httpx, "AsyncClient", lambda **kw: client_class(transport=httpx.MockTransport(handler), **kw))
        result = await plugin_instance.minecraft_autonomy_review(review["id"], "insert", "Need food", "Find food", spoken_text="去找点吃的。")
        await entered.wait()
        command = port.mailbox.claim()
        assert command["id"] == result["request_id"]  # Independent executor can consume while output waits.
        task = next(iter(plugin_instance._speech_output_tasks))
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    run_async(scenario())
    record = json.loads((port.directory / "speech-output.json").read_text(encoding="utf-8"))["records"][review["id"]]
    assert record["status"] == "unknown" and not record["playback_verified"]


def test_inspect_refuses_speech_and_does_not_acknowledge(plugin_instance, run_async, monkeypatch):
    _, port, _, review, _ = prepare(plugin_instance, monkeypatch)
    result = run_async(plugin_instance.minecraft_autonomy_review(review["id"], "inspect", spoken_text="先走吧。"))
    assert result["error"] == "inspect_is_read_only"
    assert not port.state()["review"].get("result")
    assert not (port.directory / "speech-output.json").exists()


@pytest.mark.parametrize("base", ["https://foreign.invalid", "http://user:secret@localhost:1234", "http://127.0.0.1:1234/chat"])
def test_output_endpoint_is_explicit_local_origin(plugin_module, base):
    mod = importlib.import_module(plugin_module.__name__ + ".neko_speech")
    with pytest.raises(ValueError):
        mod.output_settings({"speech_output": {"enabled": True, "base_url": base}}, "TestRole")
