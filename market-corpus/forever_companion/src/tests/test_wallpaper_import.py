"""Wallpaper Engine directory parsing, with actual browser-style File objects."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PARSER = ROOT / "ui" / "wallpaper_import.ts"


@pytest.fixture(scope="module")
def node():
    executable = shutil.which("node")
    if not executable:
        pytest.skip("Node is required for the Wallpaper Engine parser")
    return executable


def run_parser(node: str, operation: str) -> None:
    scaffold = r"""
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";
const source = stripTypeScriptTypes(readFileSync(process.argv[1], "utf8"));
const parser = await import("data:text/javascript;base64," + Buffer.from(source).toString("base64"));
function file(path, contents = "") {
    const result = new File([contents], path.split("/").at(-1));
    Object.defineProperty(result, "webkitRelativePath", { value: path });
    return result;
}
function project(path, value) { return file(path + "/project.json", JSON.stringify(value)); }
"""
    result = subprocess.run(
        [node, "--input-type=module", "-e", scaffold + operation, str(PARSER)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr


def test_only_project_primary_media_is_selected(node):
    run_parser(node, r"""
const result = await parser.scanWallpaperFiles([
    project("root/motion", { type: "video", file: "media/clip.WEBM", title: "<img onerror=alert(1)>", preview: "preview.jpg" }),
    file("root/motion/media/clip.WEBM", "video bytes"), file("root/motion/preview.jpg"),
    project("root/static", { type: "image", file: "wallpaper.png" }), file("root/static/wallpaper.png"),
    file("root/unrelated.jpg"), file("root/loose.webm"),
], () => false);
assert.equal(result.candidates.length, 2);
assert.equal(result.candidates[0].key, "root/motion/media/clip.WEBM");
assert.equal(result.candidates[0].title, "<img onerror=alert(1)>");
assert.equal(result.candidates[0].kind, "video");
assert.equal(await result.candidates[0].file.text(), "video bytes");
assert.equal(result.candidates[1].kind, "image");
assert.equal(result.invalid, 0);
assert.equal(result.unsupported, 0);
""")


@pytest.mark.parametrize("kind,media", [
    ("scene", "scene.pkg"), ("web", "index.html"), ("application", "program.exe"),
    ("unknown", "clip.webm"), ("video", "scene.pkg"),
])
def test_unsupported_projects_never_import_previews_or_textures(node, kind, media):
    import json

    run_parser(node, r"""
const kind = """ + json.dumps(kind) + r""";
const media = """ + json.dumps(media) + r""";
const result = await parser.scanWallpaperFiles([
    project("root/project", { type: kind, file: media, preview: "preview.png" }),
    file("root/project/" + media), file("root/project/preview.png"), file("root/project/textures/texture.png"),
], () => false);
assert.equal(result.candidates.length, 0);
assert.equal(result.unsupported, 1);
""")


def test_absolute_traversal_url_and_ambiguous_paths_are_rejected(node):
    run_parser(node, r"""
for (const path of ["../clip.webm", "/clip.webm", "C:\\clip.webm", "\\\\server\\clip.webm",
    "https://host/clip.webm", "folder/../../clip.webm", "folder/./clip.webm", "folder//clip.webm", "a\0.webm"]) {
    assert.equal(parser.safeWallpaperPath(path), "", path);
    const result = await parser.scanWallpaperFiles([
        project("root/p", { type: "video", file: path }), file("root/p/clip.webm"),
    ], () => false);
    assert.equal(result.candidates.length, 0, path);
    assert.equal(result.invalid, 1, path);
}
const duplicate = await parser.scanWallpaperFiles([
    project("root/p", { type: "video", file: "clip.webm" }),
    file("root/p/clip.webm", "first"), file("root/p/clip.webm", "second"),
], () => false);
assert.equal(duplicate.candidates.length, 0);
assert.equal(duplicate.invalid, 1);
assert.equal(parser.safeWallpaperPath("media\\clip.webm"), "media/clip.webm");
""")


def test_invalid_manifests_missing_files_and_mismatched_types_are_counted(node):
    run_parser(node, r"""
const result = await parser.scanWallpaperFiles([
    file("root/broken/project.json", "{"),
    project("root/array", []),
    project("root/missing", { type: "video", file: "missing.webm" }),
    project("root/mismatch", { type: "image", file: "clip.webm" }), file("root/mismatch/clip.webm"),
    project("root/preview", { type: "video", file: "clip.webm", preview: "../unsafe.png" }), file("root/preview/clip.webm"),
    file("root/large/project.json", " ".repeat(1024 * 1024 + 1)),
], () => false);
assert.equal(result.candidates.length, 0);
assert.equal(result.invalid, 6);
""")


def test_directory_scan_limits_and_cancellation_fail_closed(node):
    run_parser(node, r"""
await assert.rejects(() => parser.scanWallpaperFiles(
    Array.from({length: 10001}, (_, i) => file("root/" + i + ".png")), () => false
), /wallpaper_directory_too_large/);
await assert.rejects(() => parser.scanWallpaperFiles(
    Array.from({length: 501}, (_, i) => project("root/" + i, {type: "video", file: "clip.webm"})), () => false
), /wallpaper_directory_too_large/);
await assert.rejects(() => parser.scanWallpaperFiles([
    project("root/p", {type: "video", file: "clip.webm"}), file("root/p/clip.webm"),
], () => true), /wallpaper_cancelled/);
let calls = 0;
await assert.rejects(() => parser.scanWallpaperFiles([
    project("root/p", {type: "video", file: "clip.webm"}), file("root/p/clip.webm"),
], () => ++calls > 1), /wallpaper_cancelled/);
""")
