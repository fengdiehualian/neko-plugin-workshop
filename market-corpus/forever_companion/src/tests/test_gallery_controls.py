"""Gallery controls and paper-page presentation without a running host."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APPEARANCE = ROOT / "ui" / "appearance.tsx"
BOOK = ROOT / "ui" / "styles_book.ts"


@pytest.fixture(scope="module")
def gallery_controls():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the TSX gallery behavior check")
    modules = Path(
        os.environ.get(
            "NEKO_UI_NODE_MODULES",
            str(Path(tempfile.gettempdir()) / "neko-companion-ui-tooling" / "node_modules"),
        )
    )
    script = r"""
        import { readFileSync } from "node:fs";
        import { createRequire, stripTypeScriptTypes } from "node:module";
        import { join } from "node:path";
        const require = createRequire(join(process.argv[2], "__gallery_test__.cjs"));
        let transform;
        try { ({ transform } = require("sucrase")); }
        catch { process.exit(2); }
        const source = readFileSync(process.argv[1], "utf8");
        const utilsSource = stripTypeScriptTypes(readFileSync(process.argv[3], "utf8"));
        const utils = await import("data:text/javascript;base64," + Buffer.from(utilsSource).toString("base64"));
        const React = {
            createElement(type, props, ...children) {
                return {
                    type, props: props || {},
                    children: children.flat(Infinity).filter(x => x !== null && x !== undefined && x !== false),
                };
            },
        };
        const kit = {
            useState: initial => [initial, () => {}],
            useRef: initial => ({current: initial}),
            useEffect: () => {},
        };
        for (const name of ["Button", "Card", "Field", "ImageUpload", "SegmentedControl", "Slider"]) {
            kit[name] = "kit:" + name;
        }
        const exports = {};
        const output = transform(source, { transforms: ["typescript", "jsx", "imports"] }).code;
        new Function("require", "exports", "React", output)(
            name => name === "@neko/plugin-ui" ? kit : name === "./utils" ? utils : {},
            exports, React,
        );
        const walk = tree => !tree || typeof tree !== "object"
            ? [] : [tree, ...tree.children.flatMap(walk)];
        const hasClass = (tree, name) => (tree.props.className || "").split(" ").includes(name);
        const text = tree => typeof tree === "object" ? tree.children.map(text).join("") : String(tree);
        const items = [
            { id: "g1", name: "saved.png", thumb: "data:image/png;base64,AA==" },
            { id: "g2", name: "draft.png", thumb: "data:image/png;base64,AQ==" },
            { id: "g3", name: "no-thumbnail.png", thumb: "" },
        ];
        function render(draftId, savedId) {
            const draft = utils.normAppearance({ bg_id: draftId });
            const saved = utils.normAppearance({ bg_id: savedId });
            const patches = [], removals = [];
            const tree = exports.AppearanceCard({
                t: key => key, items, draft, saved, uploading: false,
                onDraft: patch => patches.push(patch),
                onAskRemove: item => removals.push(item.id),
                onRevert: () => {}, onAdd: () => {},
            });
            const nodes = walk(tree);
            const tiles = nodes.filter(n => hasClass(n, "tm-gallery-tile"));
            const before = JSON.stringify({ draft, saved });
            const summaries = tiles.map(tile => {
                const pick = tile.children.find(n => hasClass(n, "tm-gallery-pick"));
                const remove = tile.children.find(n => hasClass(n, "tm-gallery-del"));
                const mark = walk(pick).find(n => hasClass(n, "tm-gallery-use"));
                const name = walk(pick).find(n => hasClass(n, "tm-gallery-name"));
                return {
                    tileClick: typeof tile.props.onClick,
                    pickType: pick.type, removeType: remove && remove.type,
                    nestedButtons: walk(pick).slice(1).filter(n => n.type === "button").length,
                    title: pick.props.title, label: pick.props["aria-label"],
                    pressed: pick.props["aria-pressed"], type: pick.props.type,
                    name: text(name), state: mark ? text(mark) : null,
                    removeTitle: remove && remove.props.title,
                    removeLabel: remove && remove.props["aria-label"],
                    classes: tile.props.className,
                };
            });
            const deleteButton = tiles[2].children.find(n => hasClass(n, "tm-gallery-del"));
            deleteButton.props.onClick();
            const afterDeletePatches = patches.length;
            const imagePick = tiles[2].children.find(n => hasClass(n, "tm-gallery-pick"));
            imagePick.props.onClick();
            const nonePick = tiles[0].children.find(n => hasClass(n, "tm-gallery-pick"));
            nonePick.props.onClick();
            const positions = nodes.filter(n => hasClass(n, "tm-pos-cell")).map(n => ({
                type: n.type, label: n.props["aria-label"], pressed: n.props["aria-pressed"],
            }));
            const adjustment = nodes.find(n => hasClass(n, "tm-appearance-adjust"));
            const adjustmentNodes = walk(adjustment);
            const summary = adjustment.children.find(n => n.type === "summary");
            const grid = adjustmentNodes.find(n => hasClass(n, "tm-adjust-grid"));
            return {
                tiles: summaries, removals, afterDeletePatches, patches, positions,
                mutatedProps: before !== JSON.stringify({ draft, saved }),
                adjustment: {
                    type: adjustment.type,
                    hasOpenProp: Object.hasOwn(adjustment.props, "open"),
                    summary: text(summary),
                    fields: grid.children.filter(n => n.type === "kit:Field").length,
                    sliders: adjustmentNodes.filter(n => n.type === "kit:Slider").length,
                    galleryInside: adjustmentNodes.some(n => hasClass(n, "tm-gallery")),
                    uploadInside: adjustmentNodes.some(n => n.type === "kit:ImageUpload"),
                    saveInside: adjustmentNodes.some(n => hasClass(n, "tm-appearance-save")),
                },
            };
        }
        console.log(JSON.stringify({
            changed: render("g2", "g1"),
            noWallpaperSaved: render("g2", ""),
            noWallpaperPreview: render("", "g1"),
            unchanged: render("g1", "g1"),
        }));
    """
    proc = subprocess.run(
        [
            node, "--input-type=module", "-e", script,
            str(APPEARANCE), str(modules), str(ROOT / "ui" / "utils.ts"),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if proc.returncode == 2:
        pytest.skip("Sucrase is unavailable; set NEKO_UI_NODE_MODULES to the UI toolchain")
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    return json.loads(proc.stdout)


def test_gallery_uses_named_sibling_buttons(gallery_controls):
    for tile in gallery_controls["changed"]["tiles"]:
        assert tile["tileClick"] == "undefined"
        assert tile["pickType"] == "button" and tile["type"] == "button"
        assert tile["nestedButtons"] == 0
        assert tile["title"] == tile["label"] == tile["name"]
        assert tile["pressed"] in {"true", "false"}
        if tile.get("removeType"):
            assert tile["removeType"] == "button"
            assert tile["removeTitle"] == tile["removeLabel"]
            assert tile["name"] in tile["removeLabel"]


def test_gallery_delete_does_not_select_or_mutate_saved_values(gallery_controls):
    state = gallery_controls["changed"]
    assert state["removals"] == ["g2"]
    assert state["afterDeletePatches"] == 0
    assert state["patches"] == [{"bg_id": "g2"}, {"bg_id": ""}]
    assert state["mutatedProps"] is False


def test_saved_wallpaper_and_unsaved_preview_have_distinct_states(gallery_controls):
    tiles = gallery_controls["changed"]["tiles"]
    assert tiles[1]["state"] == "panel.appearance.inUse"
    assert tiles[1]["pressed"] == "false"
    assert "tm-gallery-applied" in tiles[1]["classes"]
    assert tiles[2]["state"] == "panel.appearance.previewing"
    assert tiles[2]["pressed"] == "true"
    assert "tm-gallery-preview" in tiles[2]["classes"]
    unchanged = gallery_controls["unchanged"]["tiles"]
    assert unchanged[1]["state"] == "panel.appearance.inUse"
    assert unchanged[1]["pressed"] == "true"


def test_no_wallpaper_saved_and_draft_states_are_truthful(gallery_controls):
    applied = gallery_controls["noWallpaperSaved"]["tiles"]
    assert applied[0]["state"] == "panel.appearance.inUse"
    assert applied[0]["pressed"] == "false"
    assert applied[2]["state"] == "panel.appearance.previewing"
    preview = gallery_controls["noWallpaperPreview"]["tiles"]
    assert preview[0]["state"] == "panel.appearance.previewing"
    assert preview[0]["pressed"] == "true"
    assert preview[1]["state"] == "panel.appearance.inUse"


def test_position_buttons_expose_selected_state(gallery_controls):
    positions = gallery_controls["changed"]["positions"]
    assert len(positions) == 9
    assert all(p["type"] == "button" and p["label"] for p in positions)
    assert all(p["pressed"] in {"true", "false"} for p in positions)
    assert sum(p["pressed"] == "true" for p in positions) == 1


def test_appearance_adjustments_are_native_and_collapsed_by_default(gallery_controls):
    for state in gallery_controls.values():
        adjustment = state["adjustment"]
        assert adjustment["type"] == "details"
        assert adjustment["hasOpenProp"] is False
        assert adjustment["summary"] == "panel.appearance.adjustSection"
        assert adjustment["fields"] == 10
        assert adjustment["sliders"] == 8


def test_appearance_gallery_upload_and_save_stay_outside_disclosure(gallery_controls):
    for state in gallery_controls.values():
        adjustment = state["adjustment"]
        assert adjustment["galleryInside"] is False
        assert adjustment["uploadInside"] is False
        assert adjustment["saveInside"] is False


def test_appearance_disclosure_has_keyboard_focus_and_wrapping_styles():
    css = (ROOT / "ui" / "styles.ts").read_text(encoding="utf-8")
    assert ".tm-appearance-adjust-summary:focus-visible" in css
    summary = re.search(r"\.tm-appearance-adjust-summary\s*\{([^}]+)\}", css).group(1)
    assert "min-height: 44px" in summary
    assert "overflow-wrap: anywhere" in summary
    assert "cursor: pointer" in summary


def test_appearance_sliders_scroll_clear_of_the_measured_savebar():
    css = (ROOT / "ui" / "styles.ts").read_text(encoding="utf-8")
    sliders = re.search(r"\.tm-appearance-adjust-body \.neko-slider-input\s*\{([^}]+)\}", css).group(1)
    assert "scroll-margin-block-end: var(--tm-save-clearance, 72px)" in sliders


def test_diary_signature_labels_wrap_in_the_available_column():
    css = BOOK.read_text(encoding="utf-8")
    seal = re.search(r"^\.tmb-seal\s*\{([^}]+)\}", css, flags=re.M).group(1)
    assert "writing-mode: horizontal-tb" in seal
    assert "overflow-wrap: anywhere" in seal
    assert "text-align: center" in seal
    assert 'font-family: "Segoe UI", var(--tmb-round)' in seal
    assert "max-width: 100%" in seal
    assert not re.search(r"\b(?:width|height):\s*\d+px", seal)


def test_gallery_state_labels_cover_all_locales():
    locales = sorted((ROOT / "i18n").glob("*.json"))
    assert len(locales) == 8
    for path in locales:
        bundle = json.loads(path.read_text(encoding="utf-8"))
        assert bundle["panel.appearance.previewing"]
        assert bundle["panel.appearance.inUse"]
        assert bundle["panel.appearance.previewing"] != bundle["panel.appearance.inUse"]
        assert bundle["panel.appearance.textWeightHelp"]


def test_paper_paragraphs_are_flat_without_breaking_sticky_ancestors():
    source = BOOK.read_text(encoding="utf-8")
    css = source.split("export const BOOK_STYLES = `", 1)[1]
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    paragraphs = re.search(r"\.tmb-entry\s*\{([^}]+)\}", css).group(1)
    assert "box-shadow: none" in paragraphs and "background: none" in paragraphs
    assert "border-top: 1px solid var(--tmb-rule)" in paragraphs
    assert ".tmb-entry::before" not in css and ".tmb-entry::after" not in css
    assert ".tmb-entry:nth-child" not in css
    assert "var(--tm-text-color)" not in css
    assert set(re.findall(r"letter-spacing:\s*([^;]+);", css)) == {"0"}
    ancestors = (
        ".tmb-card", ".tmb-book", ".tmb-page", ".tmb-page-body",
        ".tmb-file-cols", ".tmb-file-main", ".tmb-file-aside",
    )
    for selector in ancestors:
        blocks = re.findall(re.escape(selector) + r"\s*\{([^}]+)\}", css)
        for block in blocks:
            for property_name, value in re.findall(
                r"\b(overflow(?:-[xy])?|contain):\s*([^;]+);", block
            ):
                assert property_name != "contain" and value.strip() == "visible", selector
    footer = re.search(r"\.tmb-foot\s*\{([^}]+)\}", css).group(1)
    assert "position: sticky" in footer
    assert "bottom: var(--tm-save-clearance, 0px)" in footer


def test_reading_card_unclips_footer_and_footer_controls_can_wrap():
    source = BOOK.read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    card = re.search(r"\.neko-card\.tmb-card\s*\{([^}]+)\}", css).group(1)
    assert "overflow: visible" in card
    for selector in (".tmb-foot", ".tmb-foot-mid"):
        block = re.search(re.escape(selector) + r"\s*\{([^}]+)\}", css).group(1)
        assert "flex-wrap: wrap" in block and "min-width: 0" in block
    buttons = re.search(r"^\.tmb-btn\s*\{([^}]+)\}", css, flags=re.M).group(1)
    assert "max-width: 100%" in buttons and "overflow-wrap: anywhere" in buttons
    narrow = css.split("@media (max-width: 460px)", 1)[1]
    group = re.search(r"\.tmb-foot-mid\s*\{([^}]+)\}", narrow).group(1)
    assert "flex: 1 1 100%" in group
    assert re.search(r"\.tmb-foot-mid > \.tmb-btn\s*\{[^}]*flex: 1 1 64px", narrow)


def test_diary_paper_is_opaque_and_independent_of_wallpaper_controls():
    source = BOOK.read_text(encoding="utf-8")
    css = source.split("export const BOOK_STYLES = `", 1)[1]
    light, dark = css.split("@media (prefers-color-scheme: dark)", 1)
    for theme in (light, dark):
        for token in ("--tmb-paper", "--tmb-ink", "--tmb-ink-soft"):
            assert re.search(re.escape(token) + r":\s*#[0-9a-f]{6};", theme)
    for selector in (".tmb-page", ".tmb-foot", ".tmb-spine"):
        block = re.search(re.escape(selector) + r"\s*\{([^}]+)\}", light).group(1)
        assert "background: var(--tmb-paper)" in block
    assert "var(--tm-text-color)" not in css
