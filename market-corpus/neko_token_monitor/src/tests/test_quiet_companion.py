import copy
import json
from datetime import datetime, timezone
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from test_companion import setup
from test_providers import load

companion = load("companion")
diary = load("diary")
recording = load("recording")
server = load("server")
takeover = load("takeover")


def test_snooze_rearms_once_at_30_minutes_and_survives_restart(tmp_path):
    care, record, monitor, clock, spoken, _ = setup(tmp_path)
    care.check_alerts()
    notice = care.snapshot()["notices"][0]
    care.respond("snooze", notice["id"])
    assert not care.jobs
    clock[0] += 1799
    restored = companion.Companion(tmp_path / "care", monitor, record, care.request_reply, care.announce, clock=lambda: clock[0])
    restored.tick()
    assert not spoken and not restored.snapshot()["notices"]
    clock[0] += 1
    restored.tick()
    assert len(spoken) == 1
    assert restored.snapshot()["notices"][0]["id"] != notice["id"]
    clock[0] += 10000
    restored.tick()
    assert len(spoken) == 1


@pytest.mark.parametrize("action", ["snooze", "today"])
def test_new_severity_bypasses_user_acknowledgement(tmp_path, action):
    care, _, monitor, _, _, _ = setup(tmp_path)
    monitor.state["config"]["quota_remaining"] = 20
    care.check_alerts()
    first = care.snapshot()["notices"][0]
    care.respond(action, first["id"])
    monitor.state["config"]["quota_remaining"] = 10
    care.check_alerts()
    current = care.snapshot()["notices"][0]
    assert current["level"] == 2 and current["id"] != first["id"]
    with pytest.raises(ValueError):
        care.respond("today", first["id"])
    monitor.state["config"]["quota_remaining"] = 0
    care.check_alerts()
    assert care.snapshot()["notices"][0]["level"] == 3


def test_today_acknowledgement_expires_on_local_date_change(tmp_path):
    care, _, _, clock, _, _ = setup(tmp_path)
    clock[0] = datetime.now().astimezone().replace(hour=12, minute=0, second=0).timestamp()
    care.check_alerts()
    care.respond("today", care.snapshot()["notices"][0]["id"])
    clock[0] += 3600
    care.check_alerts()
    assert not care.snapshot()["notices"]
    clock[0] += 86400
    care.check_alerts()
    assert len(care.snapshot()["notices"]) == 1
    clock[0] += 3600
    care.check_alerts()
    assert len(care.data["notices"]) == 2


def test_small_balance_fluctuation_does_not_rearm_but_recovery_does(tmp_path):
    care, _, monitor, _, _, _ = setup(tmp_path)
    for value in (24, 26, 24, 27, 23):
        monitor.state["config"]["quota_remaining"] = value
        care.check_alerts()
    assert len(care.data["notices"]) == 1
    monitor.state["config"]["quota_remaining"] = 80
    care.check_alerts()
    assert not care.snapshot()["notices"]
    monitor.state["config"]["quota_remaining"] = 24
    care.check_alerts()
    assert len(care.data["notices"]) == 2


def test_acknowledging_one_account_does_not_mute_another(tmp_path):
    care, _, monitor, _, spoken, _ = setup(tmp_path)
    monitor.state = {"profiles": [{"id": name, "enabled": True, "snapshot": copy.deepcopy(monitor.state)} for name in ("one", "two")]}
    care.check_alerts()
    notice = next(n for n in care.snapshot()["notices"] if n["profile"] == "one")
    care.respond("today", notice["id"])
    care.deliver()
    assert len(spoken) == 1 and len(care.jobs) == 0
    assert [n["profile"] for n in care.snapshot()["notices"]] == ["two"]


def test_stale_unavailable_and_muted_accounts_hide_notice_without_zero(tmp_path):
    care, _, monitor, _, _, _ = setup(tmp_path)
    monitor.state = {"config": {"source": "deepseek", "balance_total": 100, "currency": "CNY", "alerts": True}, "selected": {"balance": "9"}}
    care.check_alerts()
    assert care.snapshot()["notices"][0]["quota"]["remaining"] == 9
    for change in ({"stale": True}, {"error": "offline"}, {"selected": None}):
        saved = copy.deepcopy(monitor.state)
        monitor.state.update(change)
        assert not care.snapshot()["notices"]
        monitor.state = saved
    care.configure({"enabled": False})
    assert not care.snapshot()["notices"]


def test_old_repeating_alert_migrates_without_repeating_same_threshold(tmp_path):
    care, _, _, _, spoken, _ = setup(tmp_path)
    care.data["alerts"]["current"] = {"level": 2, "at": 1, "count": 30}
    care.tick()
    assert not spoken and not care.data["notices"]


def test_task_feedback_is_silent_deduplicated_and_optional(tmp_path):
    care, record, monitor, clock, spoken, announced = setup(tmp_path)
    event = {"id": "neko-unique-task", "at": clock[0], "status": "completed", "source": "neko_tasks", "private_prompt": "do-not-copy"}
    class Agent:
        def snapshot(self):
            return {"completed_events": [event]}
    care.agent_status = Agent()
    care.check_tasks()
    care.check_tasks()
    assert not spoken and not announced
    notices = care.snapshot()["notices"]
    assert len(notices) == 1 and notices[0]["line"] == "忙完一件啦喵～"
    assert "private_prompt" not in json.dumps(care.data) + json.dumps(record.data)
    restored = companion.Companion(tmp_path / "care", monitor, record, care.request_reply, care.announce, clock=lambda: clock[0], agent_status=Agent())
    restored.check_tasks()
    assert len(restored.data["notices"]) == 1
    restored.configure({"task_feedback": False})
    event["id"] = "neko-next-task"
    restored.check_tasks()
    assert not restored.snapshot()["notices"]
    assert len(record.data["observations"]) == 2


@pytest.mark.parametrize("patch_event", [{"source": "idle_pose"}, {"status": "failed"}, {"at": False}, {"at": -1000}, {"id": None}])
def test_task_feedback_rejects_non_completion_or_untrusted_facts(tmp_path, patch_event):
    care, _, _, clock, _, _ = setup(tmp_path)
    event = {"id": "one", "at": clock[0], "status": "completed", "source": "neko_tasks"} | patch_event
    class Agent:
        def snapshot(self):
            return {"completed_events": [event]}
    care.agent_status = Agent()
    care.check_tasks()
    assert not care.data["notices"]


def test_diary_crosses_midnight_and_avoids_double_counting_overlap():
    at = datetime.fromisoformat("2026-10-20T00:20:00+08:00")
    sessions = [{"id": "one", "start": "2026-10-19T23:40:00+08:00", "presence_start": "2026-10-19T23:50:00+08:00", "active": True, "pet": 2, "interaction_events": [{"kind": "pet", "at": "2026-10-19T23:55:00+08:00"}, {"kind": "pet", "at": "2026-10-20T00:05:00+08:00"}]},
                {"id": "two", "start": "2026-10-20T00:05:00+08:00", "presence_start": "2026-10-20T00:05:00+08:00", "end": "2026-10-20T00:10:00+08:00", "active": False}]
    result = diary.build_diary(sessions, [], at)
    assert result["seconds"] == 1200 and result["pet"] == 1
    assert result["date"] == "2026-10-20"


def test_diary_does_not_invent_legacy_timestamps_or_takeover_duration():
    sessions = [{"id": "old", "start": "2026-10-19T23:50:00+00:00", "end": "2026-10-20T00:10:00+00:00", "pet": 100},
                {"id": "failed", "diary_version": 1, "start": "2026-10-20T00:10:00+00:00", "end": "2026-10-20T00:11:00+00:00", "pet": 0}]
    result = diary.build_diary(sessions, [], datetime(2026, 10, 20, 1, tzinfo=timezone.utc))
    assert result["pet"] == 0 and result["seconds"] == 0
    assert result["partial_counts"] and result["partial_duration"]
    assert not result["entries"]


def test_new_click_diary_remains_idempotent_and_presence_requires_current_session(tmp_path):
    record = recording.Recording(tmp_path, importer=lambda _: False)
    with patch.object(recording, "now", return_value="2026-10-20T10:00:00+00:00"):
        session = record.action("start")["id"]
        assert not record.mark_presence("other")
        record.action("pet", "one", session, at="2026-10-20T10:00:00+00:00")
        record.action("pet", "one", session, at="2026-10-20T10:00:00+00:00")
        assert record.mark_presence(session)
        record.action("end", session_id=session)
        assert not record.mark_presence(session)
    result = diary.build_diary(record.data["sessions"], [], datetime(2026, 10, 20, 11, tzinfo=timezone.utc))
    assert result["pet"] == 1 and len(record.data["sessions"][0]["interaction_events"]) == 1


def test_delayed_click_replay_keeps_original_day_and_rejects_forged_time(tmp_path):
    record = recording.Recording(tmp_path, importer=lambda _: False)
    with patch.object(recording, "now", return_value="2026-10-19T23:50:00+00:00"):
        session = record.action("start")["id"]
    with patch.object(recording, "now", return_value="2026-10-19T23:59:00+00:00"):
        record.action("end", session_id=session)
    with patch.object(recording, "now", return_value="2026-10-20T00:02:00+00:00"):
        record.action("pet", "late", session, at="2026-10-19T23:58:00+00:00")
        for stamp in ("not-a-date", "2026-10-20T01:00:00+00:00", "2020-01-01T00:00:00+00:00"):
            with pytest.raises(ValueError):
                record.action("pet", "bad-" + stamp, session, at=stamp)
    assert diary.build_diary(record.data["sessions"], [], datetime(2026, 10, 20, 1, tzinfo=timezone.utc))["pet"] == 0
    assert diary.build_diary(record.data["sessions"], [], datetime(2026, 10, 19, 23, 59, tzinfo=timezone.utc))["pet"] == 1


def test_diary_keeps_game_catch_counts_and_delivery_logs_out_of_story():
    sessions = [{"id": "game", "start": "2026-10-20T10:00:00+00:00", "end": "2026-10-20T11:00:00+00:00", "game_events": [{"kind": kind, "at": "2026-10-20T10:30:00+00:00"} for kind in ("catch", "counter_tag", "rescue")]}]
    observations = [{"id": "delivery", "start": "2026-10-20T10:35:00+00:00", "kind": "yui_dialogue", "summary": "technical"}]
    result = diary.build_diary(sessions, observations, datetime(2026, 10, 20, 12, tzinfo=timezone.utc))
    assert len(result["entries"]) == 1 and "抱回" in result["entries"][0]["text"]


def test_real_http_reminder_actions_csrf_diary_and_presence(tmp_path):
    care, record, monitor, _, _, _ = setup(tmp_path)
    care.check_alerts()
    state = takeover.TakeoverState()
    httpd, thread = server.start_server(monitor, port=0, companion=care, recording=record, takeover=state)
    base = f"http://127.0.0.1:{httpd.server_port}"
    def call(path, body=None, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Neko-CSRF"] = token
        request = Request(base + path, None if body is None else json.dumps(body).encode(), headers)
        with urlopen(request, timeout=3) as response:
            return json.load(response)
    try:
        token = call("/api/status")["csrf"]
        notice = call("/api/care")["notices"][0]
        with pytest.raises(HTTPError) as denied:
            call("/api/care", {"action": "snooze", "notice_id": notice["id"]}, "wrong")
        assert denied.value.code == 403
        assert not call("/api/care", {"action": "snooze", "notice_id": notice["id"]}, token)["notices"]
        session = call("/api/recording", {"action": "start"}, token)["id"]
        assert call("/api/takeover", {"session_id": session, "ok": True}, token)["accepted"]
        assert record.data["sessions"][-1]["presence_start"]
        d = call("/api/diary")
        assert "entries" in d and "technical" in d
        call("/api/recording", {"action": "end", "session_id": session}, token)
        assert not call("/api/takeover", {"session_id": session, "ok": True}, token)["accepted"]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(2)
