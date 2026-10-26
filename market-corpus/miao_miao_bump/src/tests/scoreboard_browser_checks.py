"""State, accessibility and responsive checks for the selected portrait scoreboard."""

import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from browser_checks import aim, assert_viewport_fit, launch, select_setting, snapshot
from playwright.sync_api import sync_playwright
from scoring_browser_checks import tick

ROOT = Path(__file__).resolve().parents[1]
CLOCK = """(() => {
    const frames = new Map(); let id = 0, time = 1;
    window.requestAnimationFrame = callback => { frames.set(++id, callback); return id; };
    window.cancelAnimationFrame = key => frames.delete(key);
    window.testStep = ms => {
        time += ms; const pending = [...frames.values()]; frames.clear();
        pending.forEach(callback => callback(time));
    };
})();"""


def assert_scoreboard_fit(page):
    assert_viewport_fit(page)
    for selector in ["#ally-panel", "#enemy-panel"]:
        panel = page.locator(selector).bounding_box()
        for face in page.locator(selector + " .portrait-frame").all():
            box = face.bounding_box()
            assert box["x"] >= panel["x"] - 1, (selector, page.evaluate("document.documentElement.lang"), box, panel)
            assert box["x"] + box["width"] <= panel["x"] + panel["width"] + 1, (selector, page.evaluate("document.documentElement.lang"), box, panel)
    board = page.locator("#scoreboard").bounding_box()
    pool = page.locator("#pool").bounding_box()
    assert board["y"] + board["height"] <= pool["y"] + 1
    controls = page.locator(".controls").bounding_box()
    for selector in ["#sound-toggle", "#reset", "#show-tuning", "#show-art"]:
        box = page.locator(selector).bounding_box()
        assert box["y"] >= controls["y"] and box["y"] + box["height"] <= controls["y"] + controls["height"] + 1


def run(url):
    artifacts = ROOT / "tests/artifacts"
    artifacts.mkdir(exist_ok=True)
    checks, errors = [], []
    source = (ROOT / "static/battle.js").read_text(encoding="utf-8")
    source = source.replace("this.reset();", "this.reset(); window.testMatch = this;", 1)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(viewport={"width": 1366, "height": 768})
            context.add_init_script(CLOCK)
            context.route("**/battle.js", lambda route: route.fulfill(body=source, content_type="text/javascript"))
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(url + "?locale=zh-CN")
            page.wait_for_load_state("networkidle")
            page.wait_for_function("Boolean(window.miaoPlayground)")
            page.evaluate("document.fonts.ready")
            tick(page, milliseconds=0)
            assert page.locator("#title-art").evaluate("e => e.complete && e.naturalWidth > 0")
            assert page.evaluate("document.fonts.check('900 16px MiaoRounded')")
            assert page.locator("#show-art #selected-portrait").count() == 1
            assert page.locator("#tuning-dialog #game-mode").count() == 1
            assert page.locator(".launch-tip").inner_text()
            assert "concept-header-v2.png" in page.locator("#scoreboard").evaluate("e => getComputedStyle(e, '::before').backgroundImage")
            assert "concept-footer-v2.png" in page.locator(".controls").evaluate("e => getComputedStyle(e, '::before').borderImageSource")
            assert page.locator(".cat-portrait").count() == 6
            assert page.locator('.cat-portrait[data-status="available"]').count() == 6
            assert page.locator('.cat-portrait[data-id="3"]').is_disabled()
            assert page.locator("#ally-score").inner_text() == "0"
            assert page.locator("#enemy-score").inner_text() == "0"
            page.locator('.cat-portrait[data-id="2"]').focus()
            page.keyboard.press("Enter")
            assert snapshot(page)["selected"] == 2
            assert snapshot(page)["battle"]["remainingActions"] == [3, 3]
            assert "奶盖" in page.locator("#selected-name").inner_text()
            aim(page, 2, 30, 0)
            page.keyboard.press("Escape")
            page.mouse.up()
            assert snapshot(page)["battle"]["remainingActions"] == [3, 3]
            launch(page, 0, 9, 0)
            tick(page, milliseconds=0)
            spent = page.locator('.cat-portrait[data-id="0"]')
            assert spent.get_attribute("data-status") == "acted"
            assert spent.locator(".portrait-mark").inner_text() == "✓"
            assert spent.is_disabled()
            assert spent.locator("canvas").evaluate("e => getComputedStyle(e).filter") == "none"
            assert spent.locator(".portrait-label").inner_text() == "橘子"
            assert page.locator("#pool").evaluate("e => getComputedStyle(e).filter") == "none"
            page.evaluate("""() => {
                for (let i = 0; i < 300; i++) {
                    testStep(100);
                    const s = miaoPlayground.snapshot().battle;
                    if (s.phase === 'ready' && s.side === 0) return;
                }
                throw new Error('CPU did not return control');
            }""")
            assert snapshot(page)["battle"]["round"] == 1
            assert spent.get_attribute("data-status") == "acted"
            assert page.locator('.cat-portrait[data-selected="true"]').get_attribute("data-id") in ["1", "2"]
            page.screenshot(path=str(artifacts / "scoreboard-acted.png"))
            checks.append("six real pixel portraits; keyboard selection and cancelling spend nothing; spent portraits keep their colours and names with a check")

            page.locator("#reset").evaluate("e => e.click()")
            launch(page, 0, 9, 0)
            page.evaluate("testMatch.actors[3].hp=1; testMatch.handleContact({wall:false, ringIds:[0,3]}); testMatch.world._stop()")
            tick(page, milliseconds=0)
            defeated = page.locator('.cat-portrait[data-id="3"]')
            assert defeated.get_attribute("data-status") == "waiting"
            assert defeated.locator("canvas").evaluate("e => getComputedStyle(e).filter") == "grayscale(1)"
            assert defeated.locator(".portrait-mark").inner_text() == "×"
            assert defeated.locator(".portrait-label").inner_text() == "待复归"
            assert "0/120" in defeated.get_attribute("aria-label")
            assert page.locator("#ally-score").inner_text() == "1"
            page.screenshot(path=str(artifacts / "scoreboard-waiting.png"))
            page.evaluate("for (const cat of testMatch.actors) if (cat.hp > 0) cat.usedThisRound = true")
            tick(page, 8)
            assert snapshot(page)["battle"]["round"] == 2
            assert page.locator('.cat-portrait[data-status="available"]').count() == 6
            assert defeated.locator("canvas").evaluate("e => getComputedStyle(e).filter") == "none"
            assert "120/120" in defeated.get_attribute("aria-label")
            assert page.evaluate("MiaoI18n.t('opponent.name')") in page.locator("#first-status").inner_text()
            checks.append("knockouts show a distinct waiting cross and live score; the next round restores portrait colour, HP and the opener")

            page.locator("#reset").evaluate("e => e.click()")
            for width, height in [(1672, 941), (1366, 768), (1280, 720), (1024, 600), (872, 856),
                                  (800, 600), (640, 480), (844, 390), (390, 844), (390, 600), (360, 800), (320, 568)]:
                page.set_viewport_size({"width": width, "height": height})
                page.wait_for_timeout(60)
                tick(page, milliseconds=0)
                assert_scoreboard_fit(page)
                if width in [1366, 390, 320]:
                    page.screenshot(path=str(artifacts / f"scoreboard-{width}x{height}.png"))
            for locale in ["en", "ja", "ko", "ru", "es", "pt", "zh-TW", "zh-CN"]:
                for size in [{"width": 390, "height": 844}, {"width": 640, "height": 480}]:
                    page.set_viewport_size(size)
                    page.select_option("#language", locale)
                    page.wait_for_function("code => document.documentElement.lang === code", arg=locale, polling=50)
                    tick(page, milliseconds=0)
                    assert "hud." not in page.locator("#scoreboard").inner_text()
                    assert "battle." not in page.locator('.cat-portrait[data-id="0"]').get_attribute("aria-label")
                    assert_scoreboard_fit(page)
            checks.append("twelve viewports and eight languages: portraits stay inside their teams; complete pool and four bottom actions fit without scrolling")

            page.set_viewport_size({"width": 1672, "height": 941})
            page.select_option("#language", "zh-CN")
            page.wait_for_function("document.documentElement.lang === 'zh-CN'", polling=50)
            page.evaluate("testMatch.round=3; testMatch.roundStarter=0; testMatch.scores=[2,1]; testMatch.actors[0].hp=84; testMatch.actors[1].usedThisRound=true; testMatch.actors[3].usedThisRound=true")
            page.locator('.cat-portrait[data-id="0"]').click(force=True)
            tick(page, milliseconds=0)
            page.screenshot(path=str(artifacts / "scoreboard-concept-state.png"))
            page.locator("#reset").evaluate("e => e.click()")
            assert page.locator('.cat-portrait[data-status="available"]').count() == 6
            assert page.locator("#ally-score").inner_text() == "0"
            select_setting(page, "#game-mode", "practice")
            assert page.locator("#ally-panel").is_hidden()
            assert page.locator("#enemy-panel").is_hidden()
            assert page.locator("#target-status").is_hidden()
            assert_viewport_fit(page)
            select_setting(page, "#game-mode", "battle")
            assert page.locator('.cat-portrait[data-status="available"]').count() == 6
            checks.append("restart and mode changes clear portrait state; practice has a compact status bar")
            context.close()

            context = browser.new_context(viewport={"width": 390, "height": 844})
            context.route("**/assets/pudding.json", lambda route: route.abort())
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(url + "?locale=zh-CN&art=classic")
            page.wait_for_load_state("networkidle")
            page.wait_for_function("Boolean(window.miaoPlayground)")
            face = page.locator('.cat-portrait[data-id="1"]')
            assert face.locator("canvas").is_hidden()
            assert face.locator(".portrait-fallback").inner_text() == "布丁"
            face.click()
            assert snapshot(page)["selected"] == 1
            assert page.locator('.cat-portrait[data-id="0"] canvas').is_visible()
            assert_scoreboard_fit(page)
            checks.append("a missing character asset falls back to its name, while portrait selection and the other heads still work")
            context.close()
            assert not errors, errors
        finally:
            browser.close()
    report = {"checks": checks, "errors": errors}
    (artifacts / "scoreboard-browser-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
