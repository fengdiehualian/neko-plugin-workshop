"""Download/verify the external portrait pack and smoke-test real source images.

This is NOT a gameplay screenshot accuracy benchmark. It checks that actual
upstream template files can travel through the OpenCV matching implementation.
Raw screenshots are neither acquired nor saved by this script.
"""

from __future__ import annotations

import argparse
import json
import sys
from io import BytesIO
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--opencv-path", type=Path)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--all-templates", action="store_true")
    parser.add_argument("--capture-like", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root.parents[2]))
    if args.opencv_path:
        sys.path.insert(0, str(args.opencv_path.resolve()))
    from PIL import Image
    from plugin.plugins.hsr_companion.data.avatar_pack import AvatarTemplatePack
    from plugin.plugins.hsr_companion.data.pack import default_data_pack_root

    manifest = json.loads((root / "resources/avatar_templates.json").read_text(encoding="utf-8"))
    pack = AvatarTemplatePack(manifest, root=args.data_root or default_data_pack_root())
    status = pack.install()
    print(json.dumps({"pack": status}, ensure_ascii=True))
    if not status["installed"]:
        raise SystemExit(1)
    results = []
    for name in ([item["name"] for item in manifest["templates"]] if args.all_templates else ("银狼", "花火", "流萤")):
        item = next((item for item in manifest["templates"] if item["name"] == name), None)
        if item is None:
            continue
        with Image.open(pack._path(item)) as source:
            portrait = source.convert("RGB")
        # Composite fixtures use the same source image, not a user's screenshot.
        for scale in ((1.0, 0.7, 0.5) if args.capture_like else (1.0, 0.7)):
            image = Image.new("RGB", (1280, 800), (55, 55, 55))
            resized = portrait.resize((int(portrait.width * scale), int(portrait.height * scale)))
            image.paste(resized, (1120, 240))
            if args.capture_like:
                encoded = BytesIO()
                image.save(encoded, format="JPEG", quality=68)
                image = Image.open(BytesIO(encoded.getvalue())).convert("RGB")
            roles, diagnostic = pack.match(image, page_type="unknown")
            names = [role["name"] for role in roles]
            results.append(
                {
                    "expected": name,
                    "scale": scale,
                    "names": names,
                    "available": diagnostic["available"],
                    "reason": diagnostic.get("reason"),
                }
            )
    blank, diagnostic = pack.match(Image.new("RGB", (1280, 800), (55, 55, 55)), page_type="unknown")
    passed = all(item["names"] == [item["expected"]] for item in results) and not blank and diagnostic["available"]
    print(
        json.dumps(
            {
                "verification_kind": "source_template_composites_not_gameplay",
                "jpeg_quality": 68 if args.capture_like else None,
                "cases": results if not args.all_templates else [],
                "case_count": len(results),
                "failures": [item for item in results if item["names"] != [item["expected"]]],
                "usable_template_count": diagnostic.get("template_count"),
                "blank_rejected": not blank,
                "passed": passed,
            },
            ensure_ascii=True,
        )
    )
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
