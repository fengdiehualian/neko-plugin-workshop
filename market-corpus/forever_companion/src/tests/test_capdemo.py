"""Capability demonstrations stay localized, presentation-only, and disposable."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LOCALES = ("zh-CN", "zh-TW", "en", "ja", "ko", "ru", "es", "pt")
PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
STORY_FIELDS = {"scene", "before", "after", "input", "process", "output", "example", "detail"}
SCENES = {"chat", "calendar", "activity", "mood", "tone", "fragments", "journal", "review"}


def _source(relative: str) -> str:
    path = ROOT / relative
    assert path.is_file(), f"Required capability demo file is missing: {relative}"
    return path.read_text(encoding="utf-8")


def _literal(name: str) -> dict:
    source = _source("ui/capdemo_data.ts")
    pattern = (
        rf"export\s+const\s+{re.escape(name)}"
        r"(?:\s*:\s*[A-Za-z_$][\w$]*)?\s*=\s*(\{.*?^\})"
    )
    match = re.search(pattern, source, re.MULTILINE | re.DOTALL)
    assert match, f"{name} must be an exported, strict JSON object literal"
    try:
        value = json.loads(match.group(1))
    except json.JSONDecodeError as error:
        pytest.fail(f"{name} is not strict JSON: {error}")
    assert isinstance(value, dict) and value, f"{name} must be a non-empty object"
    return value


def _messages(locale: str) -> dict[str, str]:
    return json.loads(_source(f"i18n/{locale}.json"))


def _defaults() -> dict[str, str]:
    result = {f"panel.capdemo.{key}": value for key, value in _literal("DEMO_UI").items()}
    for capability, story in _literal("DEMO_STORIES").items():
        result.update({
            f"panel.capdemo.{capability}.{field}": text
            for field, text in story.items()
            if field != "scene"
        })
    return result


def test_demo_stories_cover_exactly_the_managed_capabilities() -> None:
    from forever_companion.core.capabilities import CAPABILITY_SPECS

    stories = _literal("DEMO_STORIES")
    managed = {key for key, spec in CAPABILITY_SPECS.items() if spec.managed}
    assert set(stories) == managed, "Demo stories must cover managed capabilities without extra IDs"
    assert len(stories) == 10, "Update the demo acceptance matrix when managed capabilities change"
    for capability, story in stories.items():
        assert set(story) == STORY_FIELDS, f"{capability}: missing or unexpected story fields"
        assert story["scene"] in SCENES, f"{capability}: unknown visual scene"
        assert all(isinstance(text, str) and text.strip() for text in story.values()), capability
        assert story["before"] != story["after"], f"{capability}: comparison has identical outcomes"


def test_demo_ui_has_complete_controls_and_plain_text_defaults() -> None:
    messages = _literal("DEMO_UI")
    required = {
        "before", "after", "play", "pause", "replay", "previous", "next", "step",
        "privacy", "private", "write", "later", "pending", "complete", "unavailable",
    }
    assert required <= set(messages), f"Missing demo controls: {sorted(required - set(messages))}"
    assert all(isinstance(text, str) and text.strip() for text in messages.values())
    assert set(PLACEHOLDER.findall(messages["step"])) == {"n", "label"}


def test_demo_copy_preserves_optional_writing_and_existing_records() -> None:
    stories = _literal("DEMO_STORIES")
    journal = stories["journal"]
    assert "保留" in journal["before"] and "不发" in journal["before"], (
        "Disabling journal invitations must not imply deleting existing pages"
    )
    assert "决定" in journal["process"] and "愿意" in journal["output"]
    assert "不等于" in journal["detail"], "An invitation must not promise a completed entry"
    for capability in ("fragments", "review"):
        assert "保留" in stories[capability]["before"], (
            f"{capability}: before-state must preserve existing records"
        )


@pytest.mark.parametrize("locale", LOCALES)
def test_demo_locales_match_the_source_and_placeholders(locale: str) -> None:
    defaults = _defaults()
    reference = _messages("zh-CN")
    messages = _messages(locale)
    demo_keys = {key for key in messages if key.startswith("panel.capdemo.")}
    assert demo_keys == set(defaults), (
        f"{locale}: demo keys differ; missing={sorted(set(defaults) - demo_keys)}, "
        f"extra={sorted(demo_keys - set(defaults))}"
    )
    for key, default in defaults.items():
        assert reference.get(key) == default, f"{key}: Chinese locale differs from the story source"
        text = messages[key]
        assert isinstance(text, str) and text.strip(), f"{locale}: empty demo message {key}"
        assert set(PLACEHOLDER.findall(text)) == set(PLACEHOLDER.findall(default)), (
            f"{locale}: placeholder drift in {key}"
        )


def test_demo_component_is_presentation_only() -> None:
    source = _source("ui/capdemo.tsx")
    forbidden = {
        "network requests": r"\b(?:fetch|XMLHttpRequest|WebSocket|EventSource)\s*\(",
        "persistent storage": r"\b(?:localStorage|sessionStorage|indexedDB)\b",
        "plugin API": r"\bapi\s*\.\s*(?:call|refresh|set|update)",
        "real plugin actions": (
            r"""["'](?:set_capability|set_capability_flags|update_settings|toggle|"""
            r"""invite_journal|write_review_now|mood_journal_write)["']"""
        ),
    }
    for label, pattern in forbidden.items():
        assert not re.search(pattern, source), f"Demo component must not access {label}"
    imports = re.findall(r"""(?:from\s+|import\s*)["']([^"']+)["']""", source)
    assert imports, "Demo component dependencies were not found"
    assert all(
        path in {"@neko/plugin-ui", "./types", "./capintro", "./capdemo_data"}
        for path in imports
    ), f"Demo imports must remain presentation-only: {imports}"
    intro = _source("ui/capintro.tsx")
    assert re.search(r"""from\s+["']\./capdemo["']""", intro), "Introduction must mount the demo"


def test_demo_has_owned_motion_and_event_cleanup() -> None:
    source = _source("ui/capdemo.tsx")
    assert "requestAnimationFrame" in source, "Expected a shared, controllable animation clock"
    assert "cancelAnimationFrame" in source, "Animation clock needs cancellation on cleanup"
    assert "IntersectionObserver" in source and re.search(r"\.\s*disconnect\s*\(", source), (
        "Offscreen observation must be disconnected on cleanup"
    )
    assert "prefers-reduced-motion" in source, "Demo must respect the runtime motion preference"
    events = re.findall(r"""\.addEventListener\s*\(\s*["']([^"']+)["']""", source)
    removals = re.findall(r"""\.removeEventListener\s*\(\s*["']([^"']+)["']""", source)
    assert "visibilitychange" in events, "Hidden-page motion needs explicit lifecycle handling"
    assert "change" in events, "Motion preference changes must be observed"
    assert sorted(events) == sorted(removals), "Every demo event subscription needs cleanup"
    for selector in (
        "tm-demo-stage", "tm-demo-mode-before", "tm-demo-mode-after", "tm-demo-play",
        "tm-demo-replay", "tm-demo-prev", "tm-demo-next", "tm-demo-journal-write",
        "tm-demo-journal-later", "tm-demo-privacy",
    ):
        assert selector in source, f"Required accessible demonstration control is missing: {selector}"
    assert "tm-demo-mode-status" not in source, "Redundant current-mode status card must stay removed"


def test_demo_before_after_contrast_has_semantic_color_layers() -> None:
    source = _source("ui/styles_capdemo.ts")
    for token in (
        "--tm-demo-input-ink",
        "--tm-demo-process-ink",
        "--tm-demo-output-ink",
        "--tm-demo-process-surface",
        "--tm-demo-output-surface",
    ):
        assert token in source, f"Missing semantic demo color token: {token}"
    assert '.tm-demo[data-mode="before"]' in source
    assert '.tm-demo[data-mode="after"]' in source
    assert "#7c3aed" in source, "After-state processing color should remain visibly distinct"
    assert "#15803d" in source, "After-state result color should remain visibly distinct"
    assert "data-phase=\"1\"" in source and "data-phase=\"2\"" in source


def test_demo_mode_selection_uses_hosted_safe_aria_strings() -> None:
    source = _source("ui/capdemo.tsx")
    for mode in ("before", "after"):
        button = re.search(
            rf'<button\b[^>]*className="tm-demo-mode-{mode}"[^>]*>',
            source,
        )
        assert button, f"Missing {mode} comparison control"
        assert re.search(
            r'aria-pressed=\{[^}]*\?\s*["\'](?:true|false)["\']\s*'
            r':\s*["\'](?:true|false)["\']\s*\}',
            button.group(0),
        ), f"{mode}: boolean ARIA values become empty attributes in the hosted runtime"
