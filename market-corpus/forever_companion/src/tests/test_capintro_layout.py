"""Capability introduction reading order and technical details stay presentation-only."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_intro_places_practical_information_before_the_demo() -> None:
    source = _source("ui/capintro.tsx")
    sections = re.findall(r'<section\b[^>]*data-section="([^"]+)"', source)
    assert sections == ["purpose", "actions", "scenarios", "limits", "requirements", "demo"]
    assert source.index('data-section="requirements"') < source.index("<CapPrincipleDemo")
    assert "panel.capintro.masterRequired" in source
    assert source.index("panel.capintro.masterRequired") < source.index("<details")


def test_intro_uses_separate_dependency_model_and_technical_rows() -> None:
    source = _source("ui/capintro.tsx")
    assert re.findall(r'className="tm-ci-fact"\s+data-kind="([^"]+)"', source) == [
        "deps", "model", "config", "tools",
    ]
    assert 'className="tm-ci-deps"' in source
    assert 'className="tm-ci-model"' in source
    assert 'className="tm-ci-chips"' not in source
    assert 'title={tools.join(' not in source, "Tool names must not be available only on hover"
    assert 'tools.map((tool)' in source and 'configKeys.map((k)' in source


def test_intro_technical_details_are_native_and_closed_by_default() -> None:
    source = _source("ui/capintro.tsx")
    details = re.search(r'<details\b[^>]*className="tm-ci-technical"[^>]*>', source)
    assert details
    assert not re.search(r"\bopen(?:=|\s|>)", details.group(0))
    assert 'key={item.id}' in details.group(0), "A different capability must start collapsed"
    assert '<summary className="tm-ci-tech-summary">' in source
    assert 'className="tm-ci-mono"' in source
    styles = _source("ui/styles.ts")
    assert ".tm-ci-tech-summary:focus-visible" in styles


def test_intro_layout_uses_the_actual_container_and_readable_body_text() -> None:
    styles = _source("ui/styles.ts")
    assert "container: tm-ci / inline-size" in styles
    assert "minmax(min(320px, 100%), 1fr)" in styles
    assert "@container tm-ci (max-width: 520px)" in styles
    for selector in (".tm-ci-purpose", ".tm-ci-list li", ".tm-ci-fact dd"):
        rule = re.search(rf"{re.escape(selector)}\s*\{{([^}}]+)\}}", styles)
        assert rule, selector
        size = re.search(r"font-size:\s*([0-9.]+)px", rule.group(1))
        assert size and float(size.group(1)) >= 13, selector
        assert "overflow-wrap: anywhere" in rule.group(1), selector


def test_intro_keeps_runtime_actions_out_of_the_reading_view() -> None:
    source = _source("ui/capintro.tsx")
    assert not re.search(r"\b(?:fetch|XMLHttpRequest|WebSocket|EventSource)\s*\(", source)
    assert not re.search(r"\bapi\s*\.", source)
    assert not re.search(r"\b(?:localStorage|sessionStorage|indexedDB)\b", source)
    assert 'onClick={onBack}' in source
    assert source.index("export const LLM_BADGES") < source.index("export function CapIntroView")
    assert source.index("export function resolveText") < source.index("export function CapIntroView")
