"""Exercise a second map through the real browser without shipping a second arena."""

import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from browser_checks import assert_viewport_fit, launch, ring_point, select_setting, snapshot
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "tests/fixtures/square-map.json").read_text(encoding="utf-8"))


def run(url):
    artifacts = ROOT / "tests/artifacts"
    artifacts.mkdir(exist_ok=True)
    checks, errors = [], []
    source = (ROOT / "static/maps.js").read_text(encoding="utf-8")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(viewport={"width": 1366, "height": 768})
            context.add_init_script("""(() => {
                const original = window.requestAnimationFrame.bind(window);
                let time = 0;
                window.requestAnimationFrame = callback => original(() => callback(time += 100));
            })();""")
            # Only this test response registers the extra map; production ships one.
            context.route("**/maps.js", lambda route: route.fulfill(
                body=source + "\nwindow.MiaoMaps.register(" + json.dumps(FIXTURE) + ");",
                content_type="text/javascript",
            ))

            def open_page(suffix="?locale=zh-CN&map=test-square", setup=None):
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                if setup:
                    setup(page)
                page.goto(url + suffix)
                page.wait_for_load_state("networkidle")
                page.wait_for_function("Boolean(window.miaoPlayground)")
                return page

            page = open_page()
            state = snapshot(page)
            assert state["map"]["id"] == "test-square"
            assert state["map"]["loaded"] and not state["map"]["fallback"]
            assert state["map"]["bounds"] == FIXTURE["world"]["bounds"]
            assert state["battle"]["map"]["world"] == FIXTURE["world"]
            assert [(cat["x"], cat["y"]) for cat in state["rings"]] == [
                (point["x"], point["y"]) for team in FIXTURE["spawns"] for point in team
            ]
            assert_viewport_fit(page)
            page.screenshot(path=str(artifacts / "map-square-opening.png"))
            x, y, scale = ring_point(page, 1)
            page.mouse.click(x, y - 65 * scale)
            assert snapshot(page)["selected"] == 1
            launch(page, 1, 120, 0)
            page.wait_for_function("miaoPlayground.snapshot().battle.side === 1")
            page.wait_for_function("miaoPlayground.snapshot().battle.side === 0")
            assert snapshot(page)["battle"]["turn"] == 3
            assert not snapshot(page)["fault"]
            checks.append("square geometry drives actual art, spawn positions, visible-head input and CPU turn")

            page.click("#reset")
            select_setting(page, "#game-mode", "practice")
            assert snapshot(page)["map"]["id"] == "test-square"
            select_setting(page, "#layout", "wall")
            page.click("#reset")
            assert [(cat["x"], cat["y"]) for cat in snapshot(page)["rings"]] == [(400, 400)]
            launch(page, 0, 160, 0)
            page.wait_for_function("!miaoPlayground.snapshot().moving")
            state = snapshot(page)
            assert state["collisions"] >= 1 and not state["fault"]
            assert all(99 <= cat["x"] <= 701 and 109 <= cat["y"] <= 691 for cat in state["rings"])
            checks.append("practice reset and real-pointer wall shots keep the selected map")

            for width, height in [(1366, 768), (1280, 720), (1024, 600), (872, 856), (800, 600), (640, 480), (844, 390), (390, 844)]:
                page.set_viewport_size({"width": width, "height": height})
                page.wait_for_timeout(60)
                assert_viewport_fit(page)
            page.screenshot(path=str(artifacts / "map-square-mobile.png"))
            checks.append("a square pool keeps its aspect, input surface and controls within all eight viewports")

            unknown = open_page("?locale=zh-CN&map=not-installed")
            assert snapshot(unknown)["map"]["id"] == "summer-pool"
            assert snapshot(unknown)["map"]["fallback"]
            assert unknown.locator("#map-note").is_visible()
            for locale in ["en", "ja", "ko", "ru", "es", "pt", "zh-TW", "zh-CN"]:
                unknown.select_option("#language", locale)
                unknown.wait_for_function("locale => document.documentElement.lang === locale", arg=locale)
                assert "map.fallback" not in unknown.locator("#map-note").inner_text()
                assert_viewport_fit(unknown)
            unknown.set_viewport_size({"width": 390, "height": 844})
            assert unknown.locator("#map-note").is_visible()
            assert_viewport_fit(unknown)
            checks.append("unknown maps visibly fall back to summer, including mobile and all eight locales")

            for failure in ["metadata", "image", "invalid-metadata"]:
                def block(asset_page, failure=failure):
                    if failure == "invalid-metadata":
                        asset_page.route("**/assets/summer-pool.json", lambda route: route.fulfill(
                            body='{"image":"summer-pool.png","pixelGrid":{"width":0,"height":300}}',
                            content_type="application/json",
                        ))
                    else:
                        suffix = "json" if failure == "metadata" else "png"
                        asset_page.route(f"**/assets/summer-pool.{suffix}", lambda route: route.abort())

                fallback = open_page(setup=block)
                state = snapshot(fallback)
                assert state["map"]["id"] == "test-square" and not state["map"]["loaded"]
                assert state["map"]["bounds"] == FIXTURE["world"]["bounds"]
                assert all(cat["loaded"] for cat in state["art"]["characters"])
                assert_viewport_fit(fallback)
                fallback.screenshot(path=str(artifacts / f"map-square-fallback-{failure}.png"))
                launch(fallback, 1, 80, 0)
                fallback.click("#reset")
                assert snapshot(fallback)["battle"]["map"]["world"] == FIXTURE["world"]
                fallback.close()
            checks.append("missing or malformed map art falls back visually without replacing square physics or cats")

            mobile_context = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
            mobile_context.route("**/maps.js", lambda route: route.fulfill(
                body=source + "\nwindow.MiaoMaps.register(" + json.dumps(FIXTURE) + ");",
                content_type="text/javascript",
            ))
            mobile = mobile_context.new_page()
            mobile.on("pageerror", lambda error: errors.append(str(error)))
            mobile.goto(url + "?locale=zh-CN&map=test-square")
            mobile.wait_for_function("Boolean(window.miaoPlayground)")
            x, y, scale = ring_point(mobile, 1)
            client = mobile_context.new_cdp_session(mobile)
            client.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
            client.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x - 90 * scale, "y": y}]})
            client.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
            mobile.wait_for_function("miaoPlayground.snapshot().moving")
            assert snapshot(mobile)["selected"] == 1
            assert snapshot(mobile)["map"]["width"] == 800
            assert_viewport_fit(mobile)
            checks.append("real mobile touch uses square-map coordinates")
            mobile_context.close()
            assert not errors, errors
            context.close()
        finally:
            browser.close()
    report = {"checks": checks, "errors": errors}
    (artifacts / "maps-browser-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
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
