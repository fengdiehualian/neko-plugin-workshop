"""Execute the SaveBar effect against measured DOM and lifecycle stubs."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SAVEBAR = ROOT / "ui" / "savebar.tsx"


@pytest.fixture(scope="module")
def clearance_lifecycle():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available; SaveBar effect check needs Node 22.13+")
    script = r"""
        import assert from "node:assert/strict";
        import * as moduleApi from "node:module";
        import { readFileSync } from "node:fs";
        if (typeof moduleApi.stripTypeScriptTypes !== "function") process.exit(2);
        const source = readFileSync(process.argv[1], "utf8");
        // The built-in parser does not accept JSX; keep the actual hook setup,
        // replacing only the final render return with a null vnode.
        const renderStart = source.indexOf("  return (\n");
        assert(renderStart > 0, "Expected the final SaveBar JSX return");
        const setupSource = source.slice(0, renderStart).replace(/^import .*$/gm, "")
            + "  return null\n}\n";
        const hooks = `
            const useRef = (...args) => globalThis.__savebarHooks.useRef(...args);
            const useEffect = (...args) => globalThis.__savebarHooks.useEffect(...args);
        `;
        const moduleSource = hooks + moduleApi.stripTypeScriptTypes(setupSource);
        const { SaveBar } = await import("data:text/javascript;base64,"
            + Buffer.from(moduleSource).toString("base64"));
        function mount({ canSave = true, observer = true, missingRef = false, missingContent = false } = {}) {
            const values = new Map();
            const events = new Map();
            const observers = [];
            let height = 39.2;
            let padding = "16px";
            let effect;
            let hookCalls = 0;
            const content = {
                style: {
                    setProperty: (key, value) => values.set(key, value),
                    removeProperty: key => values.delete(key),
                },
            };
            const bar = {
                closest(selector) {
                    assert.equal(selector, ".tm-content");
                    return missingContent ? null : content;
                },
                getBoundingClientRect: () => ({ height }),
            };
            globalThis.__savebarHooks = {
                useRef: () => ({ current: missingRef ? null : bar }),
                useEffect(callback, deps) {
                    effect = callback;
                    hookCalls++;
                    assert.deepEqual(deps, [canSave]);
                },
            };
            globalThis.window = {
                getComputedStyle(element) {
                    assert.equal(element, content);
                    return { paddingBottom: padding };
                },
                addEventListener: (name, callback) => events.set(name, callback),
                removeEventListener(name, callback) {
                    assert.equal(events.get(name), callback);
                    events.delete(name);
                },
            };
            globalThis.ResizeObserver = observer ? class {
                constructor(callback) {
                    this.callback = callback;
                    this.targets = [];
                    this.disconnected = false;
                    observers.push(this);
                }
                observe(element) { this.targets.push(element); }
                disconnect() { this.disconnected = true; }
            } : undefined;
            SaveBar({ t: () => "Save settings", canSave, onSave: () => {} });
            assert.equal(hookCalls, 1, "Disabled saves must not change hook order");
            const cleanup = effect();
            return {
                values, events, observers, bar, content, cleanup,
                resize(nextHeight, nextPadding) {
                    height = nextHeight;
                    padding = nextPadding;
                },
            };
        }
        const key = "--tm-save-clearance";
        const live = mount();
        const initial = live.values.get(key);
        const watched = live.observers[0].targets.length;
        assert.deepEqual(live.observers[0].targets, [live.bar, live.content]);
        live.resize(71.1, "12px");
        live.observers[0].callback();
        const wrapped = live.values.get(key);
        live.resize(76.3, "10px");
        live.events.get("resize")();
        const resized = live.values.get(key);
        live.cleanup();
        const disconnected = live.observers[0].disconnected;
        const cleaned = live.values.size === 0 && live.events.size === 0;
        live.observers[0].callback();
        const lateIgnored = live.values.size === 0;
        const fallback = mount({ observer: false });
        const fallbackInitial = fallback.values.get(key);
        fallback.resize(42.5, "bad-padding");
        fallback.events.get("resize")();
        const fallbackResized = fallback.values.get(key);
        fallback.cleanup();
        const fallbackCleaned = fallback.events.size === 0 && fallback.values.size === 0;
        const inactive = [{ canSave: false }, { missingRef: true }, { missingContent: true }].map(options => {
            const instance = mount(options);
            return instance.cleanup === undefined && instance.values.size === 0
                && instance.events.size === 0 && instance.observers.length === 0;
        });
        console.log(JSON.stringify({
            initial, watched, wrapped, resized, disconnected, cleaned, lateIgnored,
            fallbackInitial, fallbackResized, fallbackCleaned, inactive,
        }));
    """
    proc = subprocess.run(
        [node, "--input-type=module", "-e", script, str(SAVEBAR)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if proc.returncode == 2:
        pytest.skip("Node's built-in TypeScript parser is not available")
    assert proc.returncode == 0, (proc.stdout or "") + "\n" + (proc.stderr or "")
    return json.loads(proc.stdout)


def test_clearance_measures_bar_and_content_padding(clearance_lifecycle):
    assert clearance_lifecycle["initial"] == "56px"
    assert clearance_lifecycle["watched"] == 2


def test_clearance_updates_for_wrapping_and_window_resize(clearance_lifecycle):
    assert clearance_lifecycle["wrapped"] == "84px"
    assert clearance_lifecycle["resized"] == "87px"


def test_cleanup_removes_variable_events_and_late_observers(clearance_lifecycle):
    for field in ("disconnected", "cleaned", "lateIgnored"):
        assert clearance_lifecycle[field] is True


def test_resize_fallback_without_observer(clearance_lifecycle):
    assert clearance_lifecycle["fallbackInitial"] == "56px"
    assert clearance_lifecycle["fallbackResized"] == "43px"
    assert clearance_lifecycle["fallbackCleaned"] is True


def test_disabled_save_or_missing_dom_registers_no_side_effects(clearance_lifecycle):
    assert clearance_lifecycle["inactive"] == [True, True, True]


def test_native_ref_and_save_button_behavior_are_unchanged():
    source = SAVEBAR.read_text(encoding="utf-8")
    assert '<div className="tm-save" ref={barRef}>' in source
    assert '<Button tone="primary" disabled={!!saving} onClick={onSave}>' in source
