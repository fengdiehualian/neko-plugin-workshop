"""Real pointer checks for action budgets, round changes and the provisional HUD."""

import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from browser_checks import aim, assert_viewport_fit, launch, ring_point, select_setting, snapshot
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def run(url):
    artifacts = ROOT / "tests/artifacts"
    artifacts.mkdir(exist_ok=True)
    checks, errors = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(viewport={"width": 1366, "height": 768})
            context.add_init_script("""(() => {
                const original = window.requestAnimationFrame.bind(window);
                let time = 0;
                window.requestAnimationFrame = callback => original(() => callback(time += 100));
            })();""")
            source = (ROOT / "static/battle.js").read_text(encoding="utf-8")
            # Keep real Match and pointer input, but use harmless CPU shots to
            # inspect a whole round without depending on combat balance.
            source = source.replace("return { Match, ROLES, chooseShot, respawnPoint, FLIGHT_DURATION };", """return { Match, ROLES, chooseShot: state => {
                const cat = state.actors.find(c => c.team === state.side && c.hp > 0 && !c.removed && !c.usedThisRound);
                return cat ? {id: cat.id, x: -80, y: 0} : null;
            } };""")
            context.route("**/battle.js", lambda route: route.fulfill(body=source, content_type="text/javascript"))
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(url + "?locale=zh-CN")
            page.wait_for_load_state("networkidle")
            page.wait_for_function("Boolean(window.miaoPlayground)")
            assert snapshot(page)["battle"]["remainingActions"] == [3, 3]
            aim(page, 0, 40, 0)
            page.keyboard.press("Escape")
            page.mouse.up()
            x, y, _ = ring_point(page, 0)
            page.mouse.click(x, y)
            assert snapshot(page)["battle"]["remainingActions"] == [3, 3]
            assert not snapshot(page)["moving"]
            checks.append("clicking and cancelling aim spend no opportunity")

            for i, cat_id in enumerate([0, 1, 2]):
                launch(page, cat_id, 9, 0)
                assert snapshot(page)["battle"]["actors"][cat_id]["usedThisRound"]
                page.wait_for_function("(() => { const s = miaoPlayground.snapshot().battle; return s.phase === 'ready' && s.side === 0; })()")
                state = snapshot(page)["battle"]
                if i < 2:
                    assert state["round"] == 1
                    assert state["remainingActions"] == [2 - i, 2 - i]
                    assert snapshot(page)["selected"] in [cat["id"] for cat in state["actors"] if cat["team"] == 0 and not cat["usedThisRound"]]
                    x, y, _ = ring_point(page, cat_id)
                    page.mouse.move(x, y)
                    page.mouse.down()
                    page.mouse.move(x - 30, y)
                    assert not snapshot(page)["aiming"]
                    page.mouse.up()
                    assert snapshot(page)["battle"]["turn"] == state["turn"]
                    page.screenshot(path=str(artifacts / "rounds-used-cat.png"))
                else:
                    assert state["round"] == 2 and state["roundStarter"] == 1
                    assert state["turn"] == 8
                    assert state["remainingActions"] == [3, 2]
                    assert all(not cat["usedThisRound"] for cat in state["actors"] if cat["team"] == 0)
            page.screenshot(path=str(artifacts / "rounds-second-opening.png"))
            checks.append("spent cats cannot be dragged again; selection follows remaining cats and CPU opens round two")

            for width, height in [(1366, 768), (1280, 720), (1024, 600), (872, 856), (800, 600), (640, 480), (844, 390), (390, 844)]:
                page.set_viewport_size({"width": width, "height": height})
                page.wait_for_timeout(60)
                assert_viewport_fit(page)
            for locale in ["en", "ja", "ko", "ru", "es", "pt", "zh-TW", "zh-CN"]:
                page.select_option("#language", locale)
                page.wait_for_function("locale => document.documentElement.lang === locale", arg=locale)
                assert "battle." not in page.locator("#round-status").inner_text()
                assert "battle." not in page.locator("#selected-stats").inner_text()
                assert_viewport_fit(page)
            page.screenshot(path=str(artifacts / "rounds-mobile.png"))
            checks.append("round and opportunity labels fit all eight viewports and all eight mobile locales")

            page.click("#reset")
            state = snapshot(page)["battle"]
            assert state["round"] == 1 and state["roundStarter"] == 0 and state["side"] == 0
            assert state["remainingActions"] == [3, 3]
            select_setting(page, "#game-mode", "practice")
            assert page.locator("#round-status").is_hidden()
            select_setting(page, "#game-mode", "battle")
            assert snapshot(page)["battle"]["remainingActions"] == [3, 3]
            checks.append("reset and mode changes clear round state; practice has no round restrictions")
            assert not errors, errors
            context.close()
        finally:
            browser.close()
    report = {"checks": checks, "errors": errors}
    (artifacts / "rounds-browser-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
