"""Deterministic animation/lifecycle cases alongside the real full-match browser test."""

import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from browser_checks import assert_viewport_fit, launch, select_setting, snapshot
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def tick(page, count=1, milliseconds=100):
    page.evaluate("([n, ms]) => { for (let i = 0; i < n; i++) testStep(ms); }", [count, milliseconds])


def run(url):
    artifacts = ROOT / "tests/artifacts"
    artifacts.mkdir(exist_ok=True)
    checks, errors = [], []
    battle_source = (ROOT / "static/battle.js").read_text(encoding="utf-8")
    # This response alone exposes Match for controlled lethal-contact fixtures.
    # The production page exports only the existing read-only snapshot.
    battle_source = battle_source.replace("this.reset();", "this.reset(); window.testMatch = this;", 1)
    maps_source = (ROOT / "static/maps.js").read_text(encoding="utf-8")
    fixture = json.loads((ROOT / "tests/fixtures/square-map.json").read_text(encoding="utf-8"))
    clock = """(() => {
        const frames = new Map(); let id = 0, time = 1;
        window.requestAnimationFrame = callback => { frames.set(++id, callback); return id; };
        window.cancelAnimationFrame = key => frames.delete(key);
        window.testStep = ms => {
            time += ms; const pending = [...frames.values()]; frames.clear();
            pending.forEach(callback => callback(time));
        };
    })();"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            for map_id in ["summer-pool", "test-square"]:
                context = browser.new_context(viewport={"width": 1280, "height": 720})
                context.add_init_script(clock)
                context.route("**/battle.js", lambda route: route.fulfill(body=battle_source, content_type="text/javascript"))
                context.route("**/maps.js", lambda route: route.fulfill(
                    body=maps_source + "\nMiaoMaps.register(" + json.dumps(fixture) + ");", content_type="text/javascript",
                ))
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(url + "?locale=zh-CN&map=" + map_id)
                page.wait_for_load_state("networkidle")
                page.wait_for_function("Boolean(window.miaoPlayground)")
                tick(page, milliseconds=0)
                launch(page, 0, 9, 0)
                page.evaluate("""() => {
                    testMatch.targetScore = 2;
                    for (const id of [3, 4]) {
                        testMatch.actors[id].hp = 1;
                        testMatch.handleContact({wall: false, ringIds: [0, id]});
                    }
                    testMatch.world._stop();
                }""")
                # Contacts here are injected between frames; drain their events at
                # the same instant, as real contacts are drained in their own frame.
                tick(page, milliseconds=0)
                tick(page)
                state = snapshot(page)
                assert state["battle"]["scores"] == [2, 0]
                assert state["battle"]["phase"] == "resolving"
                assert len(state["flights"]) == 2 and len(state["rings"]) == 4
                assert page.locator("#result-dialog").is_hidden()
                assert "2 : 0" in page.locator("#score-status").inner_text()
                tick(page, 2)
                page.screenshot(path=str(artifacts / f"scoring-{map_id}-flight.png"))
                before = snapshot(page)
                page.evaluate("Object.defineProperty(document, 'hidden', {configurable: true, value: true}); document.dispatchEvent(new Event('visibilitychange'))")
                tick(page, 5)
                assert snapshot(page)["flights"] == before["flights"]
                assert snapshot(page)["battle"]["presentationTime"] == before["battle"]["presentationTime"]
                page.evaluate("Object.defineProperty(document, 'hidden', {configurable: true, value: false}); document.dispatchEvent(new Event('visibilitychange'))")
                tick(page, milliseconds=0)
                tick(page, 4)
                assert page.locator("#result-dialog").is_hidden()
                assert not page.evaluate("testMatch.canLaunch(1)")
                tick(page)
                assert snapshot(page)["battle"]["phase"] == "over"
                assert not snapshot(page)["flights"]
                assert page.locator("#result-dialog").is_visible()
                assert "2 : 0" in page.locator("#result-note").inner_text()
                page.screenshot(path=str(artifacts / f"scoring-{map_id}-result.png"))

                page.locator("#play-again").evaluate("button => button.click()")
                assert snapshot(page)["battle"]["scores"] == [0, 0]
                assert not snapshot(page)["flights"]
                launch(page, 0, 9, 0)
                page.evaluate("""() => {
                    testMatch.targetScore = 5;
                    testMatch.actors[3].hp = 1;
                    testMatch.handleContact({wall: false, ringIds: [0, 3]});
                    testMatch.world._stop();
                    testMatch.actors[1].hp = 60;
                    const spawn = testMatch.map.spawns[1][0];
                    Matter.Body.setPosition(testMatch.world.rings.find(r => r.id === 1).body, spawn);
                    for (const cat of testMatch.actors) cat.usedThisRound = true;
                }""")
                tick(page, milliseconds=0)
                tick(page)
                survivors = snapshot(page)["rings"]
                tick(page, 7)
                state = snapshot(page)
                assert state["battle"]["round"] == 2
                assert state["battle"]["scores"] == [1, 0]
                assert state["battle"]["actors"][3]["hp"] == 120
                assert state["battle"]["actors"][1]["hp"] == 60
                assert [ring for ring in state["rings"] if ring["id"] != 3] == survivors
                spawn = state["battle"]["map"]["spawns"][1][0]
                returned = state["battle"]["actors"][3]
                assert (returned["x"] - spawn["x"]) ** 2 + (returned["y"] - spawn["y"]) ** 2 >= 81 ** 2 - 1e-5
                assert not state["flights"]
                assert_viewport_fit(page)
                page.screenshot(path=str(artifacts / f"scoring-{map_id}-return.png"))

                page.locator("#reset").evaluate("button => button.click()")
                launch(page, 0, 9, 0)
                page.evaluate("testMatch.actors[3].hp = 1; testMatch.handleContact({wall:false, ringIds:[0,3]})")
                tick(page)
                assert snapshot(page)["flights"]
                page.locator("#reset").evaluate("button => button.click()")
                tick(page, 10)
                assert not snapshot(page)["flights"]
                assert snapshot(page)["battle"]["scores"] == [0, 0]
                assert page.locator("#result-dialog").is_hidden()
                select_setting(page, "#game-mode", "practice")
                assert page.locator("#score-status").is_hidden()
                context.close()
                checks.append(map_id + ": parallel flights, paused presentation, delayed score result, legal return and mid-flight reset")

            context = browser.new_context(viewport={"width": 390, "height": 844}, reduced_motion="reduce")
            context.add_init_script(clock)
            context.route("**/battle.js", lambda route: route.fulfill(body=battle_source, content_type="text/javascript"))
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(url + "?locale=zh-CN")
            page.wait_for_load_state("networkidle")
            tick(page, milliseconds=0)
            launch(page, 0, 9, 0)
            page.evaluate("testMatch.actors[3].hp=1; testMatch.handleContact({wall:false, ringIds:[0,3]}); testMatch.world._stop()")
            tick(page, milliseconds=0)
            tick(page)
            tick(page)
            state = snapshot(page)
            assert state["flights"][0]["rotation"] == 0
            assert state["flights"][0]["duration"] == 0.25
            assert state["flights"][0]["y"] == state["flights"][0]["shadowY"]
            page.screenshot(path=str(artifacts / "scoring-mobile-reduced-motion.png"))
            assert_viewport_fit(page)
            tick(page, 2)
            assert not snapshot(page)["flights"]
            assert snapshot(page)["battle"]["phase"] == "ready"
            context.close()
            checks.append("mobile reduced-motion departure is short, without spin or an arc")
            assert not errors, errors
        finally:
            browser.close()
    report = {"checks": checks, "errors": errors}
    (artifacts / "scoring-browser-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
