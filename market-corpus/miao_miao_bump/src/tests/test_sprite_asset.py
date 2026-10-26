"""Inspect the distributed sprite and its frame registration without editing it."""

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("character", ["juzi", "pudding", "milkcap"])
@pytest.mark.parametrize("art_set", ["classic", "concept-v2"])
def test_frames_have_transparency_and_do_not_crop_opaque_pixels(character: str, art_set: str) -> None:
    assets = ROOT / "static" / "assets"
    if art_set != "classic":
        assets = assets / "characters" / art_set
    metadata = json.loads((assets / f"{character}.json").read_text(encoding="utf-8"))
    with Image.open(assets / metadata["image"]) as image:
        assert image.mode == "RGBA"
        alpha = image.getchannel("A")
        assert alpha.getpixel((0, 0)) < 10
        assert alpha.getpixel((image.width // 2, 0)) < 10
        opaque = alpha.point(lambda value: 255 if value > 128 else 0)
        previous_right = 0
        for frame in metadata["frames"]:
            assert frame["x"] >= previous_right
            right = frame["x"] + frame["width"]
            bottom = frame["y"] + frame["height"]
            assert right <= image.width and bottom <= image.height
            bounds = opaque.crop((frame["x"], frame["y"], right, bottom)).getbbox()
            assert bounds is not None
            assert bounds[0] > 0 and bounds[1] > 0
            assert bounds[2] < frame["width"] and bounds[3] < frame["height"]
            previous_right = right
    assert [frame["name"] for frame in metadata["frames"]] == ["idle", "impact", "rebound"]
    assert metadata["collisionRadius"] == 40
    if art_set == "classic":
        assert metadata["pixelGrid"] == {"width": 48, "height": 56}
    else:
        assert metadata["integratedFloat"] is True
        grid = metadata["pixelGrid"]
        assert 80 <= grid["width"] <= 96 and 80 <= grid["height"] <= 112
        box = metadata["drawBox"]
        assert abs((box["width"] / grid["width"]) / (box["height"] / grid["height"]) - 1) < 0.02
        float_ref = metadata["floatReference"]
        scale = box["width"] / metadata["frames"][0]["width"]
        assert abs(scale * float_ref["diameterInSource"] - 80) < 0.001
        assert abs(box["x"] + float_ref["centerInFirstFrame"]["x"] * scale) < 0.001
        assert abs(box["y"] + float_ref["centerInFirstFrame"]["y"] * scale) < 0.001
        crop = metadata["portraitBox"]
        assert crop["width"] == crop["height"]
        assert crop["x"] + crop["width"] <= grid["width"]
        assert crop["y"] + crop["height"] <= grid["height"]


def test_original_character_files_remain_byte_for_byte_available() -> None:
    hashes = json.loads((ROOT / "docs/art/classic-assets-sha256.json").read_text(encoding="utf-8"))
    for filename, expected in hashes.items():
        assert "\\" not in filename, "Asset manifest paths must use portable forward slashes"
        assert hashlib.sha256((ROOT / filename).read_bytes()).hexdigest() == expected
