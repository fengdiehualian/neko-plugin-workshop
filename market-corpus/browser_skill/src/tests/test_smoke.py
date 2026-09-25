from __future__ import annotations

import json
import tomllib
from pathlib import Path

from plugin.plugins.browser_skill import BrowserSkillPlugin
from plugin.plugins.browser_skill.runtime import BrowserSkillRuntime
from plugin.plugins.browser_skill.runtime import bsk_client as bsk_client_module


def test_plugin_manifest_and_public_runtime_load() -> None:
    root = Path(__file__).parent.parent
    manifest = tomllib.loads((root / "plugin.toml").read_text(encoding="utf-8"))
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert manifest["plugin"]["id"] == "browser_skill"
    assert manifest["plugin"]["entry"].endswith(":BrowserSkillPlugin")
    assert manifest["plugin"]["ui"]["panel"][0]["entry"] == "static/panel.html"
    assert manifest["plugin"]["version"] == "0.1.4"
    assert project["project"]["version"] == "0.1.4"
    assert project["tool"]["neko"]["build"]["exclude_dirs"] == [
        "scripts",
        "tests",
        ".vscode",
        ".ruff_cache",
        ".package-tmp",
    ]
    assert manifest["browser_skill"]["bsk_executable"] == "bundled"
    binary_manifest = json.loads((root / "bin" / "manifest.json").read_text(encoding="utf-8"))
    assert binary_manifest["version"] == bsk_client_module.BUNDLED_BSK_VERSION
    assert set(binary_manifest["assets"]) == set(bsk_client_module._BUNDLED_BSK_ASSETS)
    for platform_key, asset in binary_manifest["assets"].items():
        runtime_asset = bsk_client_module._BUNDLED_BSK_ASSETS[platform_key]
        assert asset["path"] == runtime_asset["path"]
        assert asset["sha256"] == runtime_asset["sha256"]
    bundled_names = {asset["path"] for asset in binary_manifest["assets"].values()}
    assert all((root / "bin" / name).stat().st_size > 1_000_000 for name in bundled_names)
    assert BrowserSkillPlugin.__name__ == "BrowserSkillPlugin"
    assert BrowserSkillRuntime.__name__ == "BrowserSkillRuntime"
    asset_workflow = (root / ".github" / "workflows" / "_market-build.yml").read_text(
        encoding="utf-8"
    )
    assert "python3 scripts/fetch_bsk.py" in asset_workflow
    assert "check -r --market-release" in asset_workflow
    context_meta = getattr(
        BrowserSkillPlugin.get_dashboard_ui_context,
        "__neko_ui_context__",
    )
    assert context_meta["id"] == "browser_skill"

    assert (root / "static" / "panel.html").is_file()
    offline_entry = (root / "static" / "index.html").read_text(encoding="utf-8")
    assert "panel.html" in offline_entry

    script = (root / "static" / "app.js").read_text(encoding="utf-8")
    assert "/hosted-ui/context" in script
    assert "setInterval(refreshSilently,1000)" in script

    panel = (root / "static" / "panel.html").read_text(encoding="utf-8")
    style = (root / "static" / "style.css").read_text(encoding="utf-8")
    assert 'id="scrollMaxPages" type="number" min="1" max="1" readonly' in panel
    assert 'for="scrollTokens">滚动观察 token</label><button type="button" class="help-tip open-right"' in panel
    assert 'for="livePageChars">实时页面摘要字符</label><button type="button" class="help-tip open-right"' in panel
    assert 'id="tokenCount"' in panel
    assert "estimated_calls" in script
    assert "markPluginOffline" in script
    assert "resolved_path" in script
    assert "bskSettingValue" in script
    assert ".help-tip.open-right::after" in style
