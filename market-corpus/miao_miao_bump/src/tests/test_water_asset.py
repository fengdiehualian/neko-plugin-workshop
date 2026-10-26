"""Keep the selected water source separate from the original pool atlas."""

import hashlib
import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def test_water_is_the_approved_opaque_image_and_keeps_the_classic_fallback() -> None:
    assets = ROOT / "static" / "assets"
    metadata = json.loads((assets / "summer-pool.json").read_text(encoding="utf-8"))
    source = assets / metadata["waterSurface"]["image"]
    approved = ROOT / "docs" / "art" / "water-surface-concept-v2.png"
    assert hashlib.sha256(source.read_bytes()).digest() == hashlib.sha256(approved.read_bytes()).digest()
    with Image.open(source) as image:
        assert image.width == image.height * 2
        assert image.convert("RGBA").getchannel("A").getextrema() == (255, 255)
    # Keep enough distinct pixels for the selected water at desktop size.
    grid = metadata["waterSurface"]["pixelGrid"]
    assert grid["width"] >= 480
    assert grid["width"] == grid["height"] * 2
    with Image.open(assets / metadata["image"]) as classic:
        crop = metadata["waterTexture"]
        assert 0 <= crop["x"] < crop["x"] + crop["width"] <= classic.width
        assert 0 <= crop["y"] < crop["y"] + crop["height"] <= classic.height
