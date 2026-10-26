"""Run the real wallpaper theme algorithm with Node's TypeScript parser."""

from __future__ import annotations

import colorsys
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
THEME = ROOT / "ui" / "theme.ts"
DEFAULT = {"primary": "#409eff", "secondary": "#7ba7d1"}


@pytest.fixture(scope="module")
def themes():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available; wallpaper theme check needs Node 22.13+")
    script = r"""
        import * as moduleApi from "node:module";
        import { readFileSync } from "node:fs";
        import { performance } from "node:perf_hooks";
        if (typeof moduleApi.stripTypeScriptTypes !== "function") process.exit(2);
        const source = moduleApi.stripTypeScriptTypes(readFileSync(process.argv[1], "utf8"));
        const m = await import("data:text/javascript;base64," + Buffer.from(source).toString("base64"));
        function pixels(groups) {
            const values = [];
            for (const [rgb, count, alpha = 255] of groups) {
                for (let index = 0; index < count; index++) values.push(...rgb, alpha);
            }
            return values;
        }
        const samples = {
            redMajority: pixels([[[220, 55, 65], 700], [[45, 110, 210], 300]]),
            blueMajority: pixels([[[220, 55, 65], 250], [[45, 110, 210], 750]]),
            balanced: pixels([[[210, 65, 70], 500], [[45, 105, 215], 500]]),
            threeColors: pixels([[[55, 130, 215], 650], [[220, 105, 55], 250], [[100, 185, 100], 100]]),
            single: pixels([[[65, 160, 100], 1000]]),
            grayscale: pixels([[[90, 90, 90], 600], [[180, 180, 180], 400]]),
            nearGrayscale: pixels([[[125, 130, 128], 1000]]),
            tinyAccent: pixels([[[125, 125, 125], 995], [[210, 50, 60], 5]]),
            vividMinority: pixels([[[150, 150, 150], 950], [[60, 145, 205], 50]]),
            extreme: pixels([[[2, 1, 0], 500], [[255, 254, 253], 500]]),
            extremeWithBlue: pixels([[[1, 1, 1], 1000], [[254, 254, 254], 1000], [[55, 120, 215], 100]]),
            transparent: pixels([[[220, 55, 65], 1000, 31]]),
            transparentWithBlue: pixels([[[220, 55, 65], 900, 0], [[45, 110, 210], 100, 32]]),
            invalid: [NaN, 100, 100, 255, 200, Infinity, 100, 255, -1, 80, 80, 255, 50, 60, 300, 255, "50", 70, 80, 255],
            incomplete: [255, 80, 80],
            trailing: [...pixels([[[65, 160, 100], 1000]]), 255, 255, 255],
        };
        const results = {};
        for (const [name, values] of Object.entries(samples)) results[name] = m.themeColorsFromPixels(values);
        const empty = [[], null, {}, { length: -1 }, { length: Infinity }, { length: 4.5 }].map(m.themeColorsFromPixels);
        const unchanged = JSON.stringify(samples.redMajority);
        m.themeColorsFromPixels(samples.redMajority);
        const inputUnchanged = unchanged === JSON.stringify(samples.redMajority);
        let reads = 0;
        const hugeSparse = new Proxy({ length: 4000000000 }, {
            get(target, key) {
                if (key === "length") return target.length;
                reads++;
                return undefined;
            },
        });
        const sparse = m.themeColorsFromPixels(hugeSparse);
        const vars = [];
        for (const primary of ["#409eff", "#ff0000", "#00ff00", "#0000ff", "#ffff00", "#00ffff", "#ff00ff", "#ffffff", "#000000", "#888888"]) {
            vars.push({ colors: { primary, secondary: "#7ba7d1" }, vars: m.themeColorVars({ primary, secondary: "#7ba7d1" }) });
        }
        for (let index = 0; index < 128; index++) {
            const hex = value => "#" + value.toString(16).padStart(6, "0");
            const colors = { primary: hex((index * 1299709) & 0xffffff), secondary: hex((index * 7919 + 1234567) & 0xffffff) };
            vars.push({ colors, vars: m.themeColorVars(colors) });
        }
        const invalidVars = m.themeColorVars({ primary: "url(evil)", secondary: "#abcd" });
        const canvasCases = [];
        function canvasTheme(mode, image = { naturalWidth: 2560, naturalHeight: 1440, complete: true }) {
            let draw;
            let read;
            const context = {
                drawImage(...args) {
                    if (mode === "draw-error") throw new Error("draw failed");
                    draw = args.slice(1);
                },
                getImageData(...args) {
                    if (mode === "read-error") throw new Error("tainted canvas");
                    read = args;
                    return { data: samples.blueMajority };
                },
            };
            const canvas = { width: 0, height: 0, getContext: () => mode === "no-context" ? null : context };
            globalThis.document = {
                createElement(tag) {
                    if (mode === "create-error") throw new Error("no document");
                    if (tag !== "canvas") throw new Error("unexpected element");
                    return canvas;
                },
            };
            if (mode === "no-document") delete globalThis.document;
            const colors = m.readWallpaperTheme(image);
            return { mode, colors, width: canvas.width, height: canvas.height, draw, read };
        }
        for (const mode of ["normal", "no-context", "draw-error", "read-error", "create-error", "no-document"]) {
            canvasCases.push(canvasTheme(mode));
        }
        canvasCases.push(canvasTheme("portrait", { naturalWidth: 900, naturalHeight: 1600, complete: true }));
        canvasCases.push(canvasTheme("small", { naturalWidth: 20, naturalHeight: 10, complete: true }));
        const invalidImages = [null, {}, { naturalWidth: 0, naturalHeight: 10, complete: true },
            { naturalWidth: Infinity, naturalHeight: 10, complete: true },
            { naturalWidth: 10, naturalHeight: 10, complete: false }];
        const invalidCanvas = invalidImages.map(image => canvasTheme("invalid", image).colors);
        const noise = new Uint8ClampedArray(160 * 160 * 4);
        let seed = 17;
        for (let index = 0; index < noise.length; index += 4) {
            for (let channel = 0; channel < 3; channel++) {
                seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
                noise[index + channel] = seed >>> 24;
            }
            noise[index + 3] = 255;
        }
        const started = performance.now();
        const noiseTheme = m.themeColorsFromPixels(noise);
        const noiseMillis = performance.now() - started;
        const repeatNoise = m.themeColorsFromPixels(noise);
        console.log(JSON.stringify({
            results, empty, inputUnchanged, sparse, reads, vars, invalidVars,
            canvasCases, invalidCanvas, noiseTheme, repeatNoise, noiseMillis,
            defaults: m.DEFAULT_THEME_COLORS,
        }));
    """
    proc = subprocess.run(
        [node, "--input-type=module", "-e", script, str(THEME)],
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


def rgb(hex_color):
    return tuple(int(hex_color[index : index + 2], 16) for index in (1, 3, 5))


def hue(hex_color):
    return colorsys.rgb_to_hls(*(channel / 255 for channel in rgb(hex_color)))[0]


def hue_distance(left, right):
    distance = abs(hue(left) - hue(right))
    return min(distance, 1 - distance)


def contrast(left, right):
    def luminance(value):
        channels = [channel / 255 for channel in rgb(value)]
        linear = [channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4 for channel in channels]
        return sum(channel * weight for channel, weight in zip(linear, (0.2126, 0.7152, 0.0722)))

    first, second = luminance(left), luminance(right)
    return (max(first, second) + 0.05) / (min(first, second) + 0.05)


def test_default_colors_are_the_requested_light_blue(themes):
    assert themes["defaults"] == DEFAULT
    assert themes["empty"] == [DEFAULT] * 6


def test_area_frequency_selects_the_dominant_color(themes):
    red = themes["results"]["redMajority"]["primary"]
    blue = themes["results"]["blueMajority"]["primary"]
    assert hue_distance(red, "#dc3741") < 0.02
    assert hue_distance(blue, "#2d6ed2") < 0.02


def test_multiple_colors_choose_a_distinct_secondary_hue(themes):
    for name in ("redMajority", "blueMajority", "balanced", "threeColors"):
        colors = themes["results"][name]
        assert hue_distance(colors["primary"], colors["secondary"]) >= 0.075
    assert hue_distance(themes["results"]["threeColors"]["primary"], "#3782d7") < 0.02


def test_single_color_derives_a_coordinated_secondary(themes):
    colors = themes["results"]["single"]
    assert colors != DEFAULT
    assert 0.06 <= hue_distance(colors["primary"], colors["secondary"]) <= 0.09
    for color in colors.values():
        _, lightness, saturation = colorsys.rgb_to_hls(*(channel / 255 for channel in rgb(color)))
        assert 0.37 <= lightness <= 0.63
        assert 0.33 <= saturation <= 0.79


def test_grayscale_and_insignificant_accents_fall_back(themes):
    for name in ("grayscale", "nearGrayscale", "tinyAccent"):
        assert themes["results"][name] == DEFAULT
    assert themes["results"]["vividMinority"] != DEFAULT


def test_extreme_brightness_and_transparency_are_filtered(themes):
    for name in ("extreme", "transparent"):
        assert themes["results"][name] == DEFAULT
    for name in ("extremeWithBlue", "transparentWithBlue"):
        assert hue_distance(themes["results"][name]["primary"], "#3778d7") < 0.035


def test_invalid_or_incomplete_pixels_are_ignored(themes):
    assert themes["results"]["invalid"] == DEFAULT
    assert themes["results"]["incomplete"] == DEFAULT
    assert themes["results"]["trailing"] == themes["results"]["single"]
    assert themes["sparse"] == DEFAULT
    assert themes["reads"] <= 25600 * 4
    assert themes["inputUnchanged"] is True


def test_theme_vars_preserve_raw_colors_and_meet_contrast_target(themes):
    for item in themes["vars"]:
        vars_ = item["vars"]
        assert len(vars_) == 6
        for role in ("primary", "secondary"):
            assert vars_[f"--tm-theme-{role}"] == item["colors"][role]
            assert contrast(vars_[f"--tm-theme-{role}-light"], "#ffffff") >= 7 - 1e-9
            assert contrast(vars_[f"--tm-theme-{role}-dark"], "#28282c") >= 7 - 1e-9
    assert themes["invalidVars"]["--tm-theme-primary"] == DEFAULT["primary"]
    assert themes["invalidVars"]["--tm-theme-secondary"] == DEFAULT["secondary"]


def test_canvas_samples_bounded_dimensions_without_upscaling(themes):
    cases = {case["mode"]: case for case in themes["canvasCases"]}
    assert (cases["normal"]["width"], cases["normal"]["height"]) == (160, 90)
    assert (cases["portrait"]["width"], cases["portrait"]["height"]) == (90, 160)
    assert (cases["small"]["width"], cases["small"]["height"]) == (20, 10)
    assert cases["normal"]["draw"] == [0, 0, 160, 90]
    assert cases["normal"]["read"] == [0, 0, 160, 90]
    assert cases["normal"]["colors"] == themes["results"]["blueMajority"]


def test_canvas_or_image_failures_do_not_throw(themes):
    cases = {case["mode"]: case for case in themes["canvasCases"]}
    for mode in ("no-context", "draw-error", "read-error", "create-error", "no-document"):
        assert cases[mode]["colors"] == DEFAULT
    assert themes["invalidCanvas"] == [DEFAULT] * 5


def test_quantization_is_deterministic_bounded_and_dependency_free(themes):
    assert themes["noiseTheme"] == themes["repeatNoise"]
    for color in themes["noiseTheme"].values():
        assert re.fullmatch(r"#[0-9a-f]{6}", color)
    source = THEME.read_text(encoding="utf-8")
    assert "const PALETTE_LIMIT = 12" in source
    assert "const SAMPLE_LIMIT = 25600" in source
    assert "while (boxes.length < PALETTE_LIMIT)" in source
    assert not re.search(r"^\s*import\s", source, re.MULTILINE)


@pytest.fixture(scope="module")
def plain_palettes():
    source = (ROOT / "ui" / "styles.ts").read_text(encoding="utf-8")

    def blocks(selector):
        matches = re.findall(re.escape(selector) + r"\s*\{([^}]+)\}", source)
        return [dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-f]{6})", block)) for block in matches]

    return {
        "page": blocks(".neko-page:not(.tm-has-bg)"),
        "roles": blocks(".neko-page:not(.tm-has-bg) .tm-appearance-root"),
    }


def test_soft_default_light_text_remains_readable(plain_palettes):
    page, roles = plain_palettes["page"][0], plain_palettes["roles"][0]
    assert contrast(page["--text"], "#ffffff") >= 7
    assert contrast(page["--muted"], "#f6f9fa") >= 4.5
    for background in ("--tm-accent-soft", "--tm-accent-hover"):
        assert contrast(roles["--primary"], roles[background]) >= 4.5
    assert contrast(roles["--secondary"], roles["--tm-secondary-soft"]) >= 4.5


def test_soft_default_controls_separate_fill_from_ink(plain_palettes):
    for roles in plain_palettes["roles"]:
        ink = colorsys.rgb_to_hls(*(channel / 255 for channel in rgb(roles["--primary"])))
        fill = colorsys.rgb_to_hls(*(channel / 255 for channel in rgb(roles["--tm-control-primary"])))
        assert roles["--primary"] != roles["--tm-control-primary"]
        assert fill[2] < colorsys.rgb_to_hls(*(channel / 255 for channel in rgb(DEFAULT["primary"])))[2]
        assert abs(fill[1] - ink[1]) > 0.08
    assert contrast(plain_palettes["roles"][0]["--tm-focus"], "#ffffff") >= 3


def test_soft_default_dark_surfaces_are_neutral_and_readable(plain_palettes):
    page, roles = plain_palettes["page"][1], plain_palettes["roles"][1]
    surface = "#303639"
    assert contrast(page["--text"], surface) >= 7
    assert contrast(page["--muted"], surface) >= 4.5
    for background in ("--tm-accent-soft", "--tm-accent-hover"):
        assert contrast(roles["--primary"], roles[background]) >= 4.5
    source = (ROOT / "ui" / "styles.ts").read_text(encoding="utf-8")
    assert "background: #272d30" in source
    assert max(rgb("#272d30")) - min(rgb("#272d30")) < 16
