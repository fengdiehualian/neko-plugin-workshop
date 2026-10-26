"""Real input checks for complete player-vs-local-CPU matches."""

import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from browser_checks import assert_viewport_fit, launch, ring_point, select_setting, set_slider, snapshot
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def run(url):
    artifacts = ROOT / "tests" / "artifacts"
    artifacts.mkdir(exist_ok=True)
    checks, errors, external = [], [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(viewport={"width": 1366, "height": 768})
            # Still use real browser frames and pointer input. Advance simulated frame
            # time faster so the full-match check does not spend minutes waiting.
            context.add_init_script("""(() => {
                const original = window.requestAnimationFrame.bind(window);
                let time = 0;
                window.requestAnimationFrame = callback => original(() => callback(time += 100));
            })();""")
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda request: external.append(request.url) if not request.url.startswith(url.split('/static')[0]) else None)
            page.goto(url + "?locale=zh-CN")
            page.wait_for_load_state("networkidle")
            page.wait_for_function("Boolean(window.miaoPlayground)")
            state = snapshot(page)
            assert state["mode"] == "battle" and len(state["rings"]) == 6
            assert state["battle"]["side"] == 0 and state["battle"]["phase"] == "ready"
            assert all(cat["hp"] == cat["maxHp"] for cat in state["battle"]["actors"])
            for selector in ["#collision-count", "#shot-time", "#speed", "#power-meter", ".shot-stats", ".power"]:
                assert page.locator(selector).count() == 0
            for label in ["明显碰撞", "本次用时", "当前速度", "发射力度"]:
                assert label not in page.locator("body").inner_text()
            assert "120/120" in page.locator("#selected-stats").inner_text()
            assert "沉甸甸" in page.locator("#selected-skill").inner_text()
            assert_viewport_fit(page)
            page.screenshot(path=str(artifacts / "battle-opening.png"))
            checks.append("default 3v3 roster, HP/attack/passive details and four removed statistics absent")

            x, y, _ = ring_point(page, 3)
            page.mouse.click(x, y)
            assert snapshot(page)["selected"] == 0 and not snapshot(page)["aiming"]
            before_selection = page.locator("#pool").bounding_box()
            x, y, scale = ring_point(page, 2)
            page.mouse.click(x, y - 65 * scale)
            assert snapshot(page)["selected"] == 2
            assert not snapshot(page)["moving"]
            assert not any(cat["usedThisRound"] for cat in snapshot(page)["battle"]["actors"])
            after_selection = page.locator("#pool").bounding_box()
            assert all(abs(after_selection[key] - before_selection[key]) < 1 for key in ["x", "y", "width", "height"])
            page.click("#show-art")
            assert "8" in page.locator("#skill-description").inner_text()
            page.keyboard.press("Escape")
            page.click("#show-tuning")
            assert page.locator("#drag").is_disabled()
            page.keyboard.press("Escape")
            page.click("#sound-toggle")
            assert page.locator("#sound-toggle").get_attribute("aria-pressed") == "false"
            checks.append("enemy selection blocked, own visible heads selectable, skills inspectable and battle tuning locked")

            shot = page.evaluate("MiaoBattle.chooseShot(miaoPlayground.snapshot().battle)")
            launch(page, shot["id"], shot["x"] / 1600 * 180, shot["y"] / 1600 * 180)
            page.wait_for_function("miaoPlayground.snapshot().battle.actors.some(c => c.team === 1 && c.hp < c.maxHp)")
            assert all(cat["hp"] == cat["maxHp"] for cat in snapshot(page)["battle"]["actors"] if cat["team"] == 0)
            # Wait for the CPU before taking a screenshot: accelerated frames can
            # finish its whole turn while screenshot encoding is in progress.
            page.wait_for_function("miaoPlayground.snapshot().battle.side === 1 && miaoPlayground.snapshot().battle.phase === 'moving'")
            assert page.evaluate("MiaoI18n.t('opponent.name')") in page.locator("#state").inner_text()
            checks.append("real shot causes opponent-only damage and CPU automatically takes the next turn")

            page.evaluate("Object.defineProperty(document, 'hidden', {configurable: true, value: true}); document.dispatchEvent(new Event('visibilitychange'))")
            before = snapshot(page)
            page.screenshot(path=str(artifacts / "battle-damage.png"))
            page.wait_for_timeout(200)
            assert snapshot(page)["rings"] == before["rings"]
            page.evaluate("Object.defineProperty(document, 'hidden', {configurable: true, value: false}); document.dispatchEvent(new Event('visibilitychange'))")
            page.click("#reset")
            page.wait_for_timeout(750)
            assert snapshot(page)["battle"]["phase"] == "ready"
            assert snapshot(page)["battle"]["side"] == 0 and snapshot(page)["battle"]["turn"] == 1
            assert all(cat["hp"] == cat["maxHp"] for cat in snapshot(page)["battle"]["actors"])
            checks.append("visibility pauses the full battle; resetting a CPU turn cancels the old action")

            launch(page, 1, 140, 0)
            select_setting(page, "#game-mode", "practice")
            assert snapshot(page)["mode"] == "practice" and len(snapshot(page)["rings"]) == 3
            assert not snapshot(page)["moving"]
            page.click("#show-tuning")
            set_slider(page, "#drag", "1.2")
            page.keyboard.press("Escape")
            select_setting(page, "#game-mode", "battle")
            assert snapshot(page)["params"]["drag"] == 0.8
            assert snapshot(page)["battle"]["turn"] == 1
            checks.append("mode switch cancels the previous shot and practice settings do not leak into battles")

            for locale in ["en", "ja", "ko", "ru", "es", "pt", "zh-TW", "zh-CN"]:
                page.select_option("#language", locale)
                page.wait_for_function("locale => document.documentElement.lang === locale", arg=locale)
                assert "skill." not in page.locator("#selected-skill").inner_text()
                assert_viewport_fit(page)
            for width, height in [(1366, 768), (1280, 720), (1024, 600), (872, 856), (800, 600), (640, 480), (844, 390), (390, 844)]:
                page.set_viewport_size({"width": width, "height": height})
                page.wait_for_timeout(60)
                assert_viewport_fit(page)
                page.screenshot(path=str(artifacts / f"battle-{width}x{height}.png"))
            checks.append("battle HUD and controls fit all eight viewports and locales without page scrolling")

            mobile_context = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
            mobile = mobile_context.new_page()
            mobile.on("pageerror", lambda error: errors.append(str(error)))
            mobile.goto(url + "?locale=zh-CN")
            mobile.wait_for_function("Boolean(window.miaoPlayground)")
            x, y, scale = ring_point(mobile, 1)
            client = mobile_context.new_cdp_session(mobile)
            client.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
            client.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x - 120 * scale, "y": y}]})
            client.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
            mobile.wait_for_function("miaoPlayground.snapshot().moving")
            assert mobile.locator("#game-mode").input_value() == "battle"
            assert_viewport_fit(mobile)
            mobile.click("#reset")
            assert snapshot(mobile)["battle"]["turn"] == 1
            mobile_context.close()
            checks.append("real touch drag launches an allied cat in the default battle")

            page.set_viewport_size({"width": 1280, "height": 720})
            actions = 0
            while snapshot(page)["battle"]["phase"] != "over" and actions < 160:
                page.wait_for_function("(() => { const s = miaoPlayground.snapshot().battle; return s.phase === 'over' || (s.phase === 'ready' && s.side === 0); })()", timeout=15000)
                if snapshot(page)["battle"]["phase"] == "over":
                    break
                shot = page.evaluate("MiaoBattle.chooseShot(miaoPlayground.snapshot().battle)")
                assert shot is not None
                launch(page, shot["id"], shot["x"] / 1600 * 180, shot["y"] / 1600 * 180)
                actions += 1
            state = snapshot(page)
            assert state["battle"]["phase"] == "over"
            assert page.locator("#result-dialog").is_visible()
            assert state["battle"]["winner"] in [0, 1]
            assert state["battle"]["targetScore"] == 5
            assert state["battle"]["scores"][state["battle"]["winner"]] >= 5
            assert state["battle"]["round"] > 1
            assert not state["flights"]
            assert f'{state["battle"]["scores"][0]} : {state["battle"]["scores"][1]}' in page.locator("#result-note").inner_text()
            assert all(cat["hp"] > 0 for cat in state["battle"]["actors"] if not cat["removed"])
            page.screenshot(path=str(artifacts / "battle-result.png"))
            checks.append({"full browser match": {"player_shots": actions, "turn": state["battle"]["turn"],
                "round": state["battle"]["round"], "scores": state["battle"]["scores"], "winner": state["battle"]["winner"]}})
            page.click("#play-again")
            assert not page.locator("#result-dialog").is_visible()
            assert snapshot(page)["battle"]["phase"] == "ready" and snapshot(page)["battle"]["turn"] == 1
            assert len(snapshot(page)["rings"]) == 6
            assert snapshot(page)["battle"]["scores"] == [0, 0]
            assert not snapshot(page)["flights"]
            assert all(cat["hp"] == cat["maxHp"] for cat in snapshot(page)["battle"]["actors"])
            checks.append("result dialog restarts a clean six-cat match")
            assert not errors, errors
            assert not external, external
            context.close()
        finally:
            browser.close()
    report = {"checks": checks, "errors": errors, "external_requests": external}
    (artifacts / "battle-browser-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
