"""Explicit local diagnostic: capture the game once, optionally save HUD only.

No upload, game input or automatic screenshot logging. Output is build-local.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root.parents[2]))
    from plugin.plugins.hsr_companion.assistant_runtime.server import _find_window
    from plugin.plugins.hsr_companion.assistant_runtime.window_capture import (
        capture_isolated_window,
        close_window_capture,
    )

    parser = argparse.ArgumentParser()
    parser.add_argument("--save-hud", action="store_true")
    parser.add_argument("--analyze", action="store_true")
    args = parser.parse_args()
    window = _find_window()
    if window is None or window.minimized:
        raise RuntimeError("请保持星铁窗口开启且未最小化")
    try:
        image, timestamp = capture_isolated_window(window)
        result = {"size": image.size, "timestamp": timestamp, "foreground": window.foreground}
        if args.analyze:
            from io import BytesIO

            from plugin.plugins.hsr_companion.adapters.local_vision import LocalVisionAdapter
            from plugin.plugins.hsr_companion.data import ExternalKnowledgeCatalog
            from plugin.plugins.hsr_companion.data.avatar_pack import AvatarTemplatePack
            from plugin.plugins.hsr_companion.data.vision_runtime import VisionMatchRuntime

            registry = json.loads((root / "resources/character_registry.json").read_text(encoding="utf-8"))
            catalog = ExternalKnowledgeCatalog(registry=registry)
            avatars = AvatarTemplatePack(json.loads((root / "resources/avatar_templates.json").read_text(encoding="utf-8")), root=catalog.root, runtime=VisionMatchRuntime(root=catalog.root))
            adapter = LocalVisionAdapter(catalog, avatar_pack=avatars)
            payload = BytesIO()
            image.save(payload, format="PNG")
            analysis = adapter.analyze(payload.getvalue())
            result["analysis"] = {key: analysis.get(key) for key in ("ok", "scene", "characters", "recognition", "selection")}
            result["ocr_error"] = (analysis.get("ocr") or {}).get("error")
            adapter.close()
        if args.save_hud:
            output = root / "build" / "recognition-checks" / "live-hud.png"
            output.parent.mkdir(parents=True, exist_ok=True)
            image.crop((int(image.width * .70), 0, image.width, int(image.height * .80))).save(output)
            result["hud_path"] = str(output)
        print(json.dumps(result))
    finally:
        close_window_capture()


if __name__ == "__main__":
    main()
