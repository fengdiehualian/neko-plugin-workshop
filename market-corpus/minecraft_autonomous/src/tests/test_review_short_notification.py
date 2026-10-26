"""Death recovery cues must fit the public host's small notification preview."""
import importlib


def test_death_retry_keeps_required_completion_and_identity_before_optional_scene(plugin_module):
    heartbeat = importlib.import_module(plugin_module.__name__ + ".neko_heartbeat")
    review = {"id": "abc123def456", "attempts": 2, "inspected_at": 1,
              "trigger": "death_review", "evidence": {
                  "standing_goal": "长期自主生存" * 200, "death_episode": {"id": "episode"},
                  "facts": {"body": "生命20/20", "gear": "物品" * 1000, "clock": "后半夜",
                            "queue": "空闲", "foodReserve": "熟食充足"},
                  "read_activity": {"mc_scout": 8},
                  "tool_failure": {"tool": "mc_do", "error": "贴火把没有可用支撑" * 300,
                      "native_execution": {"phase": "blocked"}, "native_event": {"run": "r" * 36, "cursor": 763}}}}
    text = heartbeat.heartbeat_text(review)
    assert len(text.encode()) <= 650
    assert review["id"] in text[:160]
    assert "death_reflection" in text
    assert "短句" in text and "先提交" in text
    assert text.index("death_reflection") < text.index("贴火把")
    assert "感受或打算" in text
