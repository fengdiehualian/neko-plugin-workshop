import json
import threading
from unittest.mock import patch

from test_providers import load

companion = load("companion")
recording = load("recording")


class Monitor:
    def __init__(self):
        self.state = {"configured": True, "config": {"source": "manual_quota", "model": "隔离模拟", "quota_remaining": 9, "quota_total": 100, "quota_unit": "%", "alerts": True, "api_key": "never-export-secret", "endpoint": "https://private.invalid"}, "stale": True}

    def snapshot(self):
        return self.state


def setup(tmp_path):
    clock = [1000.0]
    monitor, spoken, announced = Monitor(), [], []
    record = recording.Recording(tmp_path / "record", importer=lambda row: False)
    care = companion.Companion(tmp_path / "care", monitor, record,
                               lambda text, target: spoken.append((text, target)) or {"submitted": True},
                               lambda text, target: announced.append((text, target)) or True,
                               clock=lambda: clock[0])
    return care, record, monitor, clock, spoken, announced


def test_reminders_run_without_widget_once_per_threshold_and_persist(tmp_path):
    care, record, monitor, clock, spoken, _ = setup(tmp_path)
    assert not record.snapshot()["active"]
    care.tick()
    assert len(spoken) == 1 and spoken[0][1] == "YUI"
    assert "手动记录" in spoken[0][0] and "9.0" in spoken[0][0]
    clock[0] += 149
    care.tick()
    assert len(spoken) == 1
    clock[0] += 10000
    care.tick()
    assert len(spoken) == 1
    restarted = companion.Companion(tmp_path / "care", monitor, record, care.request_reply, care.announce, clock=lambda: clock[0])
    restarted.tick()
    assert len(spoken) == 1
    monitor.state["config"]["quota_remaining"] = 0
    restarted.tick()
    assert len(spoken) == 2
    assert record.snapshot()["pending_memory"] >= 2
    assert care.snapshot()["last_delivery"]["playback_confirmed"] is False


def test_recovery_mute_and_stale_data_stop_reminders(tmp_path):
    care, _, monitor, clock, spoken, _ = setup(tmp_path)
    care.check_alerts()  # Pending voice becomes obsolete before it is delivered.
    monitor.state["config"]["quota_remaining"] = 100
    care.deliver()
    assert not spoken
    care.configure({"enabled": False})
    monitor.state["config"]["quota_remaining"] = 1
    care.tick()
    assert not spoken
    care.configure({"enabled": True})
    monitor.state = {"config": {"source": "deepseek", "alerts": True, "currency": "CNY"}, "selected": {"balance": "0.2"}, "stale": True}
    clock[0] += 1000
    care.tick()
    assert not spoken


def test_multiple_profiles_share_alert_without_overwriting(tmp_path):
    care, _, monitor, _, spoken, _ = setup(tmp_path)
    import copy
    rows = []
    for name in ("DeepSeek", "OpenAI", "Claude"):
        state = copy.deepcopy(monitor.state)
        state["config"]["model"] = name
        rows.append({"id": name, "enabled": True, "snapshot": state})
    monitor.state = {"profiles": rows}
    care.check_alerts()
    rows[-1]["enabled"] = False
    care.deliver()
    assert len(spoken) == 1
    assert "DeepSeek" in spoken[0][0] and "OpenAI" in spoken[0][0]
    assert "Claude" not in spoken[0][0]


def test_safe_balances_unknown_tokens_and_codex(tmp_path):
    care, _, monitor, _, _, _ = setup(tmp_path)
    quota = companion.quota_snapshot(monitor.snapshot())
    text = json.dumps(quota)
    assert "secret" not in text and "private.invalid" not in text
    monitor.state["config"].update(quota_unit="token", quota_remaining=1200, quota_total=12000)
    assert companion.quota_snapshot(monitor.snapshot())["percent"] == 10
    monitor.state["config"]["quota_total"] = None
    assert companion.quota_snapshot(monitor.snapshot())["percent"] is None
    assert companion.quota_snapshot({"config": {"source": "jev"}, "reported_usage": {"tokens": 123}}) is None
    q = companion.quota_snapshot({"config": {"source": "codex"}, "codex_quota": {"windows": [{"remaining": 50, "label": "周"}, {"remaining": 5, "label": "小时"}]}})
    assert q["percent"] == 5 and "小时" in q["basis"]
    q = companion.quota_snapshot({"config": {"source": "openai", "budget": 100}, "selected": {"balance": "4"}})
    assert q["percent"] == 4 and "并非官方账户余额" in q["basis"]


def test_game_dialogue_and_complete_memory_facts(tmp_path):
    care, record, _, clock, spoken, _ = setup(tmp_path)
    session = record.action("start")["id"]
    care.handle_game("start", "start-1", session)
    for n in range(10):
        care.handle_game("catch", "catch-" + str(n), session)
    assert care.handle_game("reverse", "reverse-1", session)["event_accepted"]
    assert not care.handle_game("reverse", "reverse-1", session)["event_accepted"]
    care.deliver()
    assert "怎么变成本喵被追了" in spoken[-1][0]
    clock[0] += 13
    care.handle_game("counter_tag", "tag-1", session)
    care.deliver()
    assert "救命喵" in spoken[-1][0]
    care.handle_game("flee", "flee-1", session)
    care.handle_game("rescue", "rescue-1", session)
    care.handle_game("stop", "stop-1", session)
    record.action("end")
    row = record.data["sessions"][-1]
    text = recording.Recording.memory_text(row)
    for expected in ("追到肥鱼 10 次", "攻防转换 1 次", "YUI 被反追逮住 1 次", "用户抱回 1 次", "9.0 %", "手动记录", "虎头帽", "精神恢复"):
        assert expected in text
    assert "never-export" not in text
    assert "追到" not in row["summary"]  # Keep hidden game counts out of the visible exit feedback.
    restarted = recording.Recording(tmp_path / "record")
    assert restarted.snapshot()["game_totals"] == {"catches": 10, "rescues": 1}


def test_game_voice_is_cancelled_on_exit_and_wrong_session(tmp_path):
    care, record, _, _, spoken, _ = setup(tmp_path)
    session = record.action("start")["id"]
    care.handle_game("reverse", "reverse-1", session)
    record.action("end")
    care.deliver()
    assert not spoken
    record.action("start")
    care.queue("game", "旧会话喊话喵～", {}, session)
    care.deliver()
    assert not spoken


def test_reverse_shout_waits_through_voice_cooldown(tmp_path):
    care, record, _, clock, spoken, _ = setup(tmp_path)
    session = record.action("start")["id"]
    care.last_voice = clock[0]
    clock[0] += .1
    care.handle_game("reverse", "reverse-urgent", session)
    care.handle_game("counter_tag", "tag-after-reverse", session)
    clock[0] += 13
    care.deliver()
    assert len(spoken) == 1 and "怎么变成本喵被追了" in spoken[0][0]


def test_memory_inflight_revision_and_retry(tmp_path):
    care, record, _, _, _, _ = setup(tmp_path)
    session = record.action("start")["id"]
    care.handle_game("catch", "catch-1", session)
    entered, release = threading.Event(), threading.Event()
    def importer(row):
        entered.set()
        release.wait(2)
        return True
    record.importer = importer
    thread = threading.Thread(target=record.flush_pending)
    thread.start()
    assert entered.wait(1)
    care.handle_game("catch", "catch-2", session)
    release.set()
    thread.join(2)
    assert record.snapshot()["pending_memory"] == 1
    record.flush_pending()
    assert record.snapshot()["pending_memory"] == 0


def test_import_contract_contains_chase_balance_and_correct_character(tmp_path):
    care, record, _, _, _, _ = setup(tmp_path)
    care.configure({"character_name": "YUI·测试角色"})
    session = record.action("start")["id"]
    care.handle_game("reverse", "reverse-1", session)
    record.action("end")
    calls = []
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def read(self, *_):
            return b'{"success":true,"added_facts":1}'
    class Opener:
        def open(self, request, **_):
            calls.append(json.loads(request.data))
            return Response()
    with patch.object(recording, "build_opener", return_value=Opener()):
        assert record.import_memory(record.data["sessions"][-1])
    assert calls[0]["character_name"] == "YUI·测试角色"
    assert "虎头帽" in calls[0]["files"][0]["content"] and "9.0 %" in calls[0]["files"][0]["content"]


def test_voice_disabled_uses_text_alert_and_settings_validate(tmp_path):
    care, _, _, _, spoken, announced = setup(tmp_path)
    care.configure({"voice": False, "interval": 60})
    care.tick()
    assert not spoken and len(announced) == 1
    for item in ({"enabled": 1}, {"interval": 0}, {"interval": 60.1}, {"character_name": "../wrong"}):
        try:
            care.configure(item)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid preference accepted")
