"""Optional real-browser checks. Serve the plugin root over localhost first."""

import argparse
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def snapshot(page):
    return page.evaluate("window.miaoPlayground.snapshot()")


def ring_point(page, ring_id=0):
    state = snapshot(page)
    ring = next(ring for ring in state["rings"] if ring["id"] == ring_id)
    width, height = state["map"]["width"], state["map"]["height"]
    bounds = page.locator("#pool").bounding_box()
    return (
        bounds["x"] + ring["x"] / width * bounds["width"],
        bounds["y"] + ring["y"] / height * bounds["height"],
        bounds["width"] / width,
    )


def aim(page, ring_id=0, pull_x=180, pull_y=0):
    x, y, scale = ring_point(page, ring_id)
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x - pull_x * scale, y - pull_y * scale, steps=8)


def launch(page, ring_id=0, pull_x=180, pull_y=0):
    aim(page, ring_id, pull_x, pull_y)
    page.mouse.up()
    page.wait_for_function("window.miaoPlayground.snapshot().moving", timeout=3000)


def select_setting(page, selector, value):
    """Mode/layout live in the Settings dialog, opened through the real button."""
    if not page.locator("#tuning-dialog").is_visible():
        page.click("#show-tuning")
    page.select_option(selector, value)
    if page.locator("#tuning-dialog").is_visible():
        page.keyboard.press("Escape")


def set_slider(page, selector, value):
    page.locator(selector).evaluate(
        "(element, value) => { element.value = value; element.dispatchEvent(new Event('input', {bubbles: true})); }",
        value,
    )


def assert_viewport_fit(page):
    viewport = page.viewport_size
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), page.evaluate("({locale:document.documentElement.lang,width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth})")
    assert page.evaluate("document.documentElement.scrollHeight <= innerHeight + 1")
    pool = page.locator("#pool").bounding_box()
    controls = page.locator(".controls").bounding_box()
    assert pool["width"] > 100 and pool["height"] > 60
    map_state = snapshot(page)["map"]
    assert abs(pool["width"] / pool["height"] - map_state["width"] / map_state["height"]) < 0.01
    assert pool["y"] >= 0 and pool["y"] + pool["height"] <= controls["y"]
    assert controls["y"] + controls["height"] <= viewport["height"] + 1
    for selector in ["#layout", "#reset", "#show-tuning", "#show-art"]:
        if not page.locator(selector).is_visible():
            continue
        bounds = page.locator(selector).bounding_box()
        assert bounds["x"] >= 0 and bounds["y"] >= 0
        assert bounds["x"] + bounds["width"] <= viewport["width"] + 1
        assert bounds["y"] + bounds["height"] <= viewport["height"] + 1


def run(url):
    artifacts = ROOT / "tests" / "artifacts"
    artifacts.mkdir(exist_ok=True)
    checks = []
    errors = []
    external = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(viewport={"width": 1360, "height": 960}, device_scale_factor=1)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda request: external.append(request.url) if not request.url.startswith(url.split('/static')[0]) else None)
            page.goto(url + "?locale=zh-CN&mode=practice")
            page.wait_for_load_state("networkidle")
            page.wait_for_function("Boolean(window.miaoPlayground)")
            assert page.locator("h1").inner_text() == "喵喵碰碰大作战"
            assert len(snapshot(page)["rings"]) == 3
            assert snapshot(page)["art"]["loaded"]
            assert snapshot(page)["art"]["frameCount"] == 3
            assert snapshot(page)["map"]["loaded"]
            assert snapshot(page)["map"]["bounds"] == {"left": 44, "top": 44, "right": 916, "bottom": 556}
            assert all(cat["loaded"] and cat["frameCount"] == 3 for cat in snapshot(page)["art"]["characters"])
            assert_viewport_fit(page)
            assert snapshot(page)["art"]["collisionRadius"] == 40
            page.screenshot(path=str(artifacts / "desktop-idle.png"), full_page=True)
            checks.append("desktop load, complete pool and bottom controls fit the viewport without scrolling")
            checks.append("pixel pool art loaded with the original rectangular water boundaries")

            original = snapshot(page)["rings"]
            page.click("#show-art")
            page.click('[data-pose="1"]')
            assert snapshot(page)["art"]["previewFrame"] == 1
            assert snapshot(page)["rings"] == original
            page.locator("#character-preview").screenshot(path=str(artifacts / "juzi-impact-preview.png"))
            page.click('[data-pose="2"]')
            page.locator("#character-preview").screenshot(path=str(artifacts / "juzi-rebound-preview.png"))
            page.click('[data-pose="0"]')
            page.click('[data-close="art-dialog"]')
            assert_viewport_fit(page)
            other_x, other_y, _ = ring_point(page, 1)
            page.mouse.click(other_x, other_y)
            x, y, scale = ring_point(page)
            page.mouse.click(x, y - 65 * scale)
            assert snapshot(page)["selected"] == 0 and not snapshot(page)["moving"]
            checks.append("three sprite frames, independent expression preview and visible head selection")

            idle_previews = set()
            for cat_id, key in enumerate(["juzi", "pudding", "milkcap"]):
                x, y, scale = ring_point(page, cat_id)
                # The head is visible outside the circle, and selects this cat's own art.
                page.mouse.click(x, y - 65 * scale)
                assert snapshot(page)["selected"] == cat_id
                assert not snapshot(page)["moving"]
                page.click("#show-art")
                assert page.locator("#art-title").inner_text().endswith(["橘子", "布丁", "奶盖"][cat_id])
                poses = set()
                for pose in range(3):
                    page.click(f'[data-pose="{pose}"]')
                    image = page.locator("#character-preview").evaluate("canvas => canvas.toDataURL()")
                    poses.add(image)
                    if pose == 0:
                        idle_previews.add(image)
                    page.locator("#character-preview").screenshot(path=str(artifacts / f"{key}-pose-{pose}.png"))
                assert len(poses) == 3
                page.click('[data-close="art-dialog"]')
            assert len(idle_previews) == 3
            checks.append("all three distinct cats: visible head selection and three independent expression previews")

            x, y, _ = ring_point(page, 1)
            page.mouse.click(x, y)
            assert snapshot(page)["selected"] == 1
            assert not snapshot(page)["moving"]
            aim(page, 1)
            assert snapshot(page)["aiming"]
            page.keyboard.press("Escape")
            page.mouse.up()
            assert not snapshot(page)["moving"] and not snapshot(page)["aiming"]
            aim(page)
            page.mouse.down(button="right")
            page.mouse.up(button="right")
            page.mouse.up()
            assert not snapshot(page)["moving"]
            aim(page)
            page.locator("#pool").dispatch_event("pointercancel", {"pointerId": 1})
            page.mouse.up()
            assert not snapshot(page)["moving"]
            checks.append("click selection, Escape, context-menu and pointer-cancel")

            select_setting(page, "#layout", "duel")
            launch(page)
            page.wait_for_function("window.miaoPlayground.snapshot().art.activeFrame === 1", polling="raf")
            assert set(snapshot(page)["art"]["reactingIds"]) == {0, 1}
            assert all(cat["activeFrame"] == 1 for cat in snapshot(page)["art"]["characters"][:2])
            page.wait_for_function("window.miaoPlayground.snapshot().art.activeFrame === 2", polling="raf")
            # Each cat may hit a wall again at a different time; their rebound
            # expressions need not be visible in the exact same sampled frame.
            page.wait_for_function("window.miaoPlayground.snapshot().art.characters[1].activeFrame === 2", polling="raf")
            page.click("#reset")
            assert snapshot(page)["art"]["activeFrame"] == 0
            checks.append("collision drives impact/rebound on both floats; reset clears reactions")

            select_setting(page, "#layout", "chain")
            launch(page)
            page.wait_for_function("window.miaoPlayground.snapshot().art.characters[2].activeFrame === 1", polling="raf")
            checks.append("Milkcap switches to impact expression during a real chain collision")
            page.click("#show-tuning")
            assert page.locator("#drag").is_disabled()
            page.keyboard.press("Escape")
            assert not page.locator("#reset").is_disabled()
            page.wait_for_timeout(350)
            page.screenshot(path=str(artifacts / "desktop-shot.png"), full_page=True)
            page.wait_for_function("!window.miaoPlayground.snapshot().moving", timeout=15000)
            state = snapshot(page)
            assert state["collisions"] >= 3 and not state["fault"]
            assert 4 <= state["elapsed"] <= 7
            checks.append({"chain shot": {"seconds": round(state["elapsed"], 3), "impacts": state["collisions"]}})

            launch(page, pull_x=-160, pull_y=30)
            page.click("#reset")
            before = snapshot(page)
            page.wait_for_timeout(180)
            after = snapshot(page)
            assert before["rings"] == after["rings"]
            assert not after["moving"] and after["elapsed"] == 0 and after["collisions"] == 0
            checks.append("reset during motion removes old shot")

            page.click("#show-tuning")
            page.check("#show-hitboxes")
            assert snapshot(page)["art"]["showBoundaries"]
            page.screenshot(path=str(artifacts / "collision-outlines.png"), full_page=True)
            page.uncheck("#show-hitboxes")
            set_slider(page, "#drag", "1.2")
            set_slider(page, "#mass", "2.5")
            assert snapshot(page)["params"]["drag"] == 1.2
            assert snapshot(page)["rings"][0]["mass"] == 2.5
            page.click("#defaults")
            assert snapshot(page)["params"]["drag"] == 0.8
            assert all(ring["mass"] == 1 for ring in snapshot(page)["rings"])
            page.click('[data-close="tuning-dialog"]')
            for locale in ["en", "ja", "ko", "ru", "es", "pt", "zh-TW", "zh-CN"]:
                page.select_option("#language", locale)
                page.wait_for_function("locale => document.documentElement.lang === locale", arg=locale)
                assert not page.locator("#language").is_disabled()
                assert_viewport_fit(page)
            checks.append("tuning, default restore, all eight locales")

            select_setting(page, "#layout", "wall")
            launch(page)
            page.evaluate("Object.defineProperty(document, 'hidden', {configurable: true, value: true}); document.dispatchEvent(new Event('visibilitychange'))")
            paused = snapshot(page)
            page.wait_for_timeout(220)
            assert snapshot(page)["elapsed"] == paused["elapsed"]
            assert not snapshot(page)["aiming"]
            page.evaluate("Object.defineProperty(document, 'hidden', {configurable: true, value: false}); document.dispatchEvent(new Event('visibilitychange'))")
            page.wait_for_timeout(160)
            assert snapshot(page)["elapsed"] > paused["elapsed"]
            page.click("#reset")
            page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted: true}))")
            assert snapshot(page)["suspended"]
            page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))")
            assert not snapshot(page)["suspended"]
            checks.append("visibility pause/resume and back-forward-cache lifecycle")

            for width, height in [(1366, 768), (1280, 720), (1024, 600), (872, 856), (800, 600), (640, 480), (844, 390), (390, 844)]:
                page.set_viewport_size({"width": width, "height": height})
                page.wait_for_timeout(80)
                assert_viewport_fit(page)
                page.screenshot(path=str(artifacts / f"viewport-{width}x{height}.png"))
            page.set_viewport_size({"width": 520, "height": 900})
            launch(page, pull_x=120, pull_y=30)
            page.click("#reset")
            assert not snapshot(page)["moving"]
            page.screenshot(path=str(artifacts / "compact-idle.png"), full_page=True)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert_viewport_fit(page)
            checks.append("eight viewport sizes: full map, bottom controls and reset visible without scrolling; scaled input works")

            mobile_context = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
            mobile = mobile_context.new_page()
            mobile.on("pageerror", lambda error: errors.append(str(error)))
            mobile.goto(url + "?locale=zh-CN&mode=practice")
            mobile.wait_for_function("Boolean(window.miaoPlayground)")
            x, y, scale = ring_point(mobile)
            client = mobile_context.new_cdp_session(mobile)
            client.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
            client.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x - 130 * scale, "y": y}]})
            client.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
            mobile.wait_for_function("window.miaoPlayground.snapshot().moving")
            mobile.click("#reset")
            mobile.screenshot(path=str(artifacts / "mobile-idle.png"), full_page=True)
            assert_viewport_fit(mobile)
            checks.append("real touch drag at phone viewport")
            mobile_context.close()

            page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted: false}))")
            assert snapshot(page)["destroyed"]
            page.wait_for_timeout(100)
            assert not errors, errors
            assert not external, external
            checks.append("page cleanup, no browser errors, no external requests")
            for missing_id, key in enumerate(["juzi", "pudding", "milkcap"]):
                fallback = context.new_page()
                fallback.route(f"**/assets/{key}-expressions.png", lambda route: route.abort())
                fallback.goto(url + "?locale=zh-CN&mode=practice&art=classic")
                fallback.wait_for_function("Boolean(window.miaoPlayground)")
                cats = snapshot(fallback)["art"]["characters"]
                assert [cat["loaded"] for cat in cats] == [i != missing_id for i in range(3)]
                x, y, _ = ring_point(fallback, missing_id)
                fallback.mouse.click(x, y)
                fallback.click("#show-art")
                assert not fallback.locator('[data-pose="0"]').is_enabled()
                assert "占位泳圈" in fallback.locator("#art-note").inner_text()
                fallback.keyboard.press("Escape")
                launch(fallback, missing_id)
                fallback.click("#reset")
                fallback.close()
            fallback = context.new_page()
            fallback.route("**/assets/*-expressions.png", lambda route: route.abort())
            fallback.goto(url + "?locale=zh-CN&mode=practice&art=classic")
            fallback.wait_for_function("Boolean(window.miaoPlayground)")
            assert not any(cat["loaded"] for cat in snapshot(fallback)["art"]["characters"])
            launch(fallback)
            fallback.close()
            checks.append("each sprite fails independently; other characters and fallback launches remain available, including all missing")
            for missing in ["summer-pool.png", "summer-pool.json"]:
                fallback = context.new_page()
                fallback.on("pageerror", lambda error: errors.append(str(error)))
                fallback.route(f"**/assets/{missing}", lambda route: route.abort())
                fallback.goto(url + "?locale=zh-CN&mode=practice&art=classic")
                fallback.wait_for_function("Boolean(window.miaoPlayground)")
                assert not snapshot(fallback)["map"]["loaded"]
                assert all(cat["loaded"] for cat in snapshot(fallback)["art"]["characters"])
                launch(fallback)
                fallback.click("#reset")
                assert_viewport_fit(fallback)
                fallback.screenshot(path=str(artifacts / f"fallback-{missing}.png"))
                fallback.close()
            checks.append("missing map image or metadata falls back independently without blocking characters or play")
            assert not errors, errors
            context.close()
        finally:
            browser.close()
    report = {"checks": checks, "errors": errors, "external_requests": external}
    (artifacts / "browser-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", help="Existing preview URL; omit to use a temporary localhost server")
    args = parser.parse_args()
    if args.url:
        run(args.url)
    else:
        class QuietHandler(SimpleHTTPRequestHandler):
            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(ROOT)))
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            run(f"http://127.0.0.1:{server.server_port}/static/index.html")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
