"""Exercise the SDK lifecycle and the host's real static/locale routes."""

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from plugin.plugins.miao_miao_bump import MiaoMiaoBumpPlugin
from plugin.server.routes import plugin_ui

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
async def test_startup_registers_the_playground_without_background_work() -> None:
    plugin = object.__new__(MiaoMiaoBumpPlugin)
    plugin.logger = SimpleNamespace(info=lambda *_: None)
    registered = []
    actions = []
    plugin.register_static_ui = lambda *args, **kwargs: registered.append((args, kwargs)) or True
    plugin.set_list_actions = lambda value: actions.extend(value)
    result = await plugin.on_startup()
    assert result.is_ok()
    assert registered == [(("static",), {"cache_control": "no-store"})]
    assert actions[0]["target"] == "/plugin/miao_miao_bump/ui/index.html"
    assert actions[0]["label"]["$i18n"] == "actions.open"
    assert (await plugin.on_shutdown()).is_ok()


@pytest.mark.asyncio
@pytest.mark.parametrize("javascript_mime", ["application/javascript", "text/javascript"])
async def test_host_serves_game_assets_and_shared_locale_bundles(monkeypatch, javascript_mime) -> None:
    plugin_ui.mimetypes.init()
    monkeypatch.setitem(plugin_ui.mimetypes.types_map, ".js", javascript_mime)

    async def static_dir(_plugin_id):
        return ROOT / "static"

    async def meta(_plugin_id):
        return {"id": "miao_miao_bump", "config_path": str(ROOT / "plugin.toml")}

    async def static_config(_plugin_id):
        return {"enabled": True, "directory": str(ROOT / "static"), "cache_control": "no-store"}

    service = plugin_ui.plugin_ui_query_service
    monkeypatch.setattr(service, "get_static_dir", static_dir)
    monkeypatch.setattr(service, "get_plugin_meta", meta)
    monkeypatch.setattr(service, "get_static_ui_config", static_config)
    app = FastAPI()
    app.include_router(plugin_ui.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        for suffix, content_type in [
            ("", "text/html"),
            ("/index.html", "text/html"),
            ("/game.js", javascript_mime),
            ("/layout.css", "text/css"),
            ("/physics.js", javascript_mime),
            ("/maps.js", javascript_mime),
            ("/battle.js", javascript_mime),
            ("/sound.js", javascript_mime),
            ("/art.js", javascript_mime),
            ("/assets/juzi.json", "application/json"),
            ("/assets/juzi-expressions.png", "image/png"),
            ("/assets/pudding.json", "application/json"),
            ("/assets/pudding-expressions.png", "image/png"),
            ("/assets/milkcap.json", "application/json"),
            ("/assets/milkcap-expressions.png", "image/png"),
            ("/assets/characters/concept-v2/juzi.json", "application/json"),
            ("/assets/characters/concept-v2/juzi-expressions.png", "image/png"),
            ("/assets/characters/concept-v2/pudding.json", "application/json"),
            ("/assets/characters/concept-v2/pudding-expressions.png", "image/png"),
            ("/assets/characters/concept-v2/milkcap.json", "application/json"),
            ("/assets/characters/concept-v2/milkcap-expressions.png", "image/png"),
            ("/assets/summer-pool.json", "application/json"),
            ("/assets/summer-pool.png", "image/png"),
            ("/assets/summer-pool-water-v1.png", "image/png"),
            ("/assets/summer-pool-water-v2.png", "image/png"),
            ("/assets/ui/side-surfaces.json", "application/json"),
            ("/assets/ui/side-capsule-v1.png", "image/png"),
            ("/assets/ui/side-circle-v1.png", "image/png"),
            ("/assets/ui/beach-surround-side-v1.png", "image/png"),
            ("/assets/ui/icons/fa-solid/heart.svg", "image/svg+xml"),
            ("/assets/ui/icons/swords.svg", "image/svg+xml"),
            ("/vendor/matter-0.20.0.min.js", javascript_mime),
        ]:
            response = await client.get("/plugin/miao_miao_bump/ui" + suffix)
            assert response.status_code == 200
            assert content_type in response.headers["content-type"]
        for locale in ["en", "zh-CN", "zh-TW", "ja", "ko", "ru", "es", "pt"]:
            response = await client.get(f"/plugin/miao_miao_bump/ui-api/i18n/{locale}.json")
            assert response.status_code == 200
            assert "ui.hint" in response.json()
