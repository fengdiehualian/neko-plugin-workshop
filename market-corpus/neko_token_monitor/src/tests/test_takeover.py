from test_providers import load

takeover = load("takeover")


def test_opened_window_does_not_mean_character_replaced():
    for outcome in (None, {"ok": False, "reason": "pause_unconfirmed"},
                    {"ok": False, "reason": "hide_unconfirmed"}):
        result = takeover.companion_result(outcome)
        assert result["window_opened"] is True
        assert result["shown"] is False
        assert result["monitor_active"] is False
        assert "人物未替换" in result["message"]
        assert "权限" not in result["message"]


def test_permission_failure_only_when_reported_by_native():
    result = takeover.companion_result({"ok": False, "reason": "accessibility"})
    assert result["takeover"] == "failed"
    assert "辅助功能权限不可用" in result["message"]


def test_success_requires_native_confirmation():
    result = takeover.companion_result({"ok": True})
    assert result["takeover"] == "confirmed"
    assert result["monitor_active"] is True
    assert result["shown"] is True


def test_previous_session_cannot_complete_a_retry():
    state = takeover.TakeoverState()
    state.begin("first")
    state.report("first", False, "pause_unconfirmed")
    state.begin("retry")
    assert not state.report("first", True)
    assert state.wait("retry", 0) is None
    assert state.report("retry", True)
    assert state.wait("retry", 0) == {"ok": True, "reason": None}
