import re
from pathlib import Path

from mood_engine import MoodEngine
from safety_limiter import SafetyLimiter

_TEXT_ACTION_RE = re.compile(
    r"[（(]\s*郊狼\s*[·・:：\-]?\s*"
    r"(?P<mode>温柔奖励|活泼奖励|轻度调戏|轻度惩罚|强力惩罚|归零|停止)"
    r"(?:\s*[·・:：\-]?\s*强度\s*(?P<intensity>\d{1,3}))?"
    r"\s*[）)]"
)


def test_plugin_manifest_version() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "plugin.toml").read_text(encoding="utf-8")
    assert 'version = "0.3.9"' in text


def test_text_marker_parses_strong_punish() -> None:
    match = _TEXT_ACTION_RE.search("哼！（郊狼·强力惩罚·强度200）")
    assert match is not None
    assert match.group("mode") == "强力惩罚"
    assert match.group("intensity") == "200"


def test_mood_engine_allows_device_max() -> None:
    engine = MoodEngine(logger=type("L", (), {"warning": lambda *a, **k: None, "info": lambda *a, **k: None})())
    params = engine.generate_params("punish_strong", custom_intensity=200)
    assert params["intensity"] == 200


def test_validate_pulse_clamps_instead_of_rejecting() -> None:
    limiter = SafetyLimiter(
        {"coyote": {"safety_max_intensity_a": 50, "safety_max_intensity_b": 50}},
        logger=type("L", (), {"info": lambda *a, **k: None, "warning": lambda *a, **k: None})(),
    )
    clamped = limiter.validate_pulse(intensity=200, duration_ms=3000)
    assert clamped == 50
