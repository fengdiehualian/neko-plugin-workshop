"""Read-only local latency sample; no images, raw OCR or account data written."""

from __future__ import annotations

import argparse
import json
import time
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from plugin.plugins.hsr_companion.adapters.local_vision import LocalVisionAdapter
from plugin.plugins.hsr_companion.assistant import VisionAssistantClient
from plugin.plugins.hsr_companion.data import ExternalKnowledgeCatalog
from plugin.plugins.hsr_companion.data.avatar_pack import AvatarTemplatePack
from plugin.plugins.hsr_companion.data.vision_runtime import VisionMatchRuntime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--direct", action="store_true", help="Read the isolated game window directly; no helper required")
    parser.add_argument("--samples", type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.samples <= 10:
        parser.error("samples must be between 1 and 10")
    root = Path(__file__).resolve().parents[1]
    catalog = ExternalKnowledgeCatalog(registry=json.loads((root / "resources/character_registry.json").read_text(encoding="utf-8")))
    avatars = AvatarTemplatePack(
        json.loads((root / "resources/avatar_templates.json").read_text(encoding="utf-8")),
        root=catalog.root, runtime=VisionMatchRuntime(root=catalog.root),
    )
    adapter = LocalVisionAdapter(catalog, avatar_pack=avatars)
    client = VisionAssistantClient()
    try:
        # Each sample gets a new capture. Still not a host chat/voice test or
        # an automatic scene-switching benchmark; never sends game input.
        for sample in range(args.samples):
            if args.direct:
                from plugin.plugins.hsr_companion.assistant_runtime.server import _find_window
                from plugin.plugins.hsr_companion.assistant_runtime.window_capture import capture_isolated_window
                window = _find_window()
                if window is None or window.minimized:
                    raise RuntimeError("Keep the game open and not minimized")
                image, captured_at = capture_isolated_window(window)
                payload = BytesIO()
                image.save(payload, format="PNG")
                frame = SimpleNamespace(image_bytes=payload.getvalue(), timestamp=captured_at)
            else:
                frame = client.fetch_frame(0)
            if frame is None:
                raise RuntimeError("No current isolated game frame")
            started = time.monotonic()
            previews = []
            result = adapter.analyze_live(
                frame.image_bytes, cancelled=lambda: False,
                on_scene=lambda value: previews.append({
                    "page": value["page"]["page_type"],
                    "seconds": round(time.monotonic() - started, 3),
                }),
            )
            print(json.dumps({
                "sample": sample, "scene_previews": previews,
                "page": (result.get("page") or {}).get("page_type"),
                "total_seconds": round(time.monotonic() - started, 3),
                "names_count": len(result.get("characters") or []),
                "names": [c["name"] for c in result.get("characters") or []],
                "source_size": (result.get("recognition") or {}).get("source_size"),
                "text_reader": (result.get("recognition") or {}).get("text_reader"),
            }), flush=True)
    finally:
        adapter.close()
        if args.direct:
            from plugin.plugins.hsr_companion.assistant_runtime.window_capture import close_window_capture
            close_window_capture()


if __name__ == "__main__":
    main()
