"""Extract attributed dsh-pet animations to alpha PNG atlases for WKWebView.

Run with --source-assets /path/to/dsh-pet/assets. Requires ffmpeg + Pillow.
No generated drawings, recoloring, or YUI edits are performed.
"""

import argparse
import hashlib
import json
import math
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageChops, ImageStat


def decode(path, ffmpeg):
    raw = subprocess.check_output(
        [ffmpeg, "-v", "error", "-c:v", "libvpx-vp9", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1"]
    )
    size = 640 * 360 * 4
    if len(raw) % size:
        raise ValueError("Unexpected source dimensions")
    return [Image.frombytes("RGBA", (640, 360), raw[i : i + size]) for i in range(0, len(raw), size)]


def loop_range(frames, first, last, minimum, maximum):
    thumbs = [im.resize((64, 36)) for im in frames]
    choices = []
    for start in range(first, last - minimum):
        for end in range(start + minimum, min(last, start + maximum) + 1):
            score = sum(ImageStat.Stat(ImageChops.difference(thumbs[start], thumbs[end])).rms)
            choices.append((score, start, end))
    score, start, end = min(choices)
    return start, end, round(score, 3)


def face_bounds(image, action, square):
    # Track the existing peach face, excluding hair and lower hands. Only the
    # existing neutral darkening overlay uses these bounds; no makeup is drawn.
    points = []
    x1, y1, x2, y2 = (220, 115, 320, 224) if action == "run" else (270, 100, 370, 225)
    pixels = image.load()
    for y in range(y1, y2):
        for x in range(x1, x2):
            r, g, b, a = pixels[x, y]
            if a > 180 and r > 170 and g > 130 and b > 100 and r > g + 3 and r > b + 10:
                points.append((x, y))
    if len(points) < 40:
        raise ValueError("Face bounds unavailable")
    xs, ys = sorted(x for x, _ in points), sorted(y for _, y in points)
    bound_left, r = xs[len(xs) // 30], xs[-len(xs) // 30 - 1]
    t, b = ys[len(ys) // 30], ys[-len(ys) // 30 - 1]
    left, top, side = square
    return [
        round(((bound_left + r) / 2 - left) / side, 4),
        round(((t + b) / 2 - top) / side, 4),
        round((r - bound_left + 8) / 2 / side, 4),
        round((b - t + 8) / 2 / side, 4),
    ]


def running_sheet(source, destination):
    image = Image.open(source).convert("RGBA")
    if image.size != (1536, 1024) or image.getchannel("A").getextrema()[0] != 0:
        raise ValueError("Expected transparent 3 x 2 sprite sheet")
    frames = [image.crop((i % 3 * 512, i // 3 * 512, i % 3 * 512 + 512, i // 3 * 512 + 512)) for i in range(6)]
    boxes = [im.getchannel("A").point(lambda a: 255 if a > 16 else 0).getbbox() for im in frames]
    if any(not b or b[0] < 2 or b[2] > 510 for b in boxes):
        raise ValueError("Sprite overlaps a cell boundary")
    bound_left, t, r, b = min(q[0] for q in boxes), min(q[1] for q in boxes), max(q[2] for q in boxes), max(q[3] for q in boxes)
    side = math.ceil(max(r - bound_left, b - t) / 0.86)
    left = round((bound_left + r - side) / 2)
    top = round(b - side * 0.93)
    atlas = Image.new("RGBA", (1536, 1024))
    for i, frame in enumerate(frames):
        # Identical crop/scale for all six cells preserves the authored run bob.
        atlas.paste(
            frame.crop((left, top, left + side, top + side)).resize((512, 512), Image.Resampling.LANCZOS),
            (i % 3 * 512, i // 3 * 512),
        )
    atlas.save(destination, optimize=True)
    return {
        "source_sheet": source.name,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "generated": True,
        "tool": "imagegen",
        "crop_square": [left, top, side],
        "frames": 6,
        "columns": 3,
        "rows": 2,
        "cell": 512,
        "fps": 12,
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-assets", type=Path, required=True)
    parser.add_argument("--qa", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    destination = root / "static/chase"
    ffmpeg = shutil.which("ffmpeg")
    sources = {"run": "原地左转奔跑", "shock": "被吓一跳", "angry": "点击回应-傲娇生气", "hat": "舞狮头"}
    frames = {key: decode(args.source_assets / "webm" / (name + ".webm"), ffmpeg) for key, name in sources.items()}
    run = loop_range(frames["run"], 42, 98, 20, 36)
    ranges = {"run": run[:2], "shock": (30, 66), "angry": (0, 30)}
    selected = {key: frames[key][a:b] for key, (a, b) in ranges.items() if key != "hat"}
    boxes = [
        im.getchannel("A").point(lambda a: 255 if a > 12 else 0).getbbox() for ims in selected.values() for im in ims
    ]
    bound_left, t, r, b = min(q[0] for q in boxes), min(q[1] for q in boxes), max(q[2] for q in boxes), max(q[3] for q in boxes)
    side = max(r - bound_left, b - t) + 12
    left, top = round((bound_left + r - side) / 2), b + 6 - side
    square = (left, top, side)
    clips = {}
    for key, ims in selected.items():
        columns = 8
        rows = math.ceil(len(ims) / columns)
        atlas = Image.new("RGBA", (columns * 256, rows * 256))
        faces = []
        for i, im in enumerate(ims):
            frame = im.crop((left, top, left + side, top + side)).resize((256, 256), Image.Resampling.LANCZOS)
            atlas.paste(frame, ((i % columns) * 256, (i // columns) * 256))
            if key != "hat":
                faces.append(face_bounds(im, key, square))
        name = "dsh-whale-" + key + ".png"
        atlas.save(destination / name, optimize=True)
        clips[key] = {
            "src": "/chase/" + name,
            "frames": len(ims),
            "columns": columns,
            "rows": rows,
            "fps": 24,
            "faces": faces,
            "source": sources[key] + ".webm",
            "source_frames": list(ranges[key]),
            "cell": 256,
            "sha256": hashlib.sha256((args.source_assets / "webm" / (sources[key] + ".webm")).read_bytes()).hexdigest(),
        }
        if args.qa:
            args.qa.mkdir(parents=True, exist_ok=True)
            frame = ims[len(ims) // 2].crop((left, top, left + side, top + side)).resize((256, 256))
            frame.save(args.qa / (key + "-selected.png"))
    # Keep the entire earlier hooded fish, as explicitly requested. Rebuilding
    # must never silently switch back to the rejected lion-head drawing.
    clips["hat"] = {
        "src": "/chase/whale-maid-tiger-run.png",
        "frames": 6,
        "columns": 3,
        "rows": 2,
        "cell": 512,
        "fps": 12,
        "faces": [],
        "facing": 1,
        "preserved": True,
        "reference": "用户保留的原版戴头套大肥鱼",
    }
    source = root / "artwork/chase/yui-fleeing-12-source.png"
    image = Image.open(source).convert("RGBA")
    if image.width % 4 or image.height % 3 or image.width // 4 != image.height // 3:
        raise ValueError("Expected a four-column, three-row square-cell sprite sheet")
    side = image.width // 4
    if image.getchannel("A").getextrema()[0] != 0:
        raise ValueError("Expected transparent animation source")
    atlas = Image.new("RGBA", (1024, 768))
    for i in range(12):
        frame = image.crop((i % 4 * side, i // 4 * side, (i % 4 + 1) * side, (i // 4 + 1) * side))
        atlas.paste(frame.resize((240, 240), Image.Resampling.LANCZOS), (i % 4 * 256 + 8, i // 4 * 256 + 8))
    atlas.save(destination / "yui-dsh-style-run-12.png", optimize=True)
    yui = {
        "src": "/chase/yui-dsh-style-run-12.png",
        "frames": 12,
        "columns": 4,
        "rows": 3,
        "cell": 256,
        "fps": 24,
        "faces": [],
        "generated": True,
        "tool": "imagegen",
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }
    clips["yui"] = yui
    hit_source = root / "artwork/chase/yui-hit-source.png"
    hit_image = Image.open(hit_source).convert("RGBA")
    if hit_image.width != hit_image.height or hit_image.width % 2 or hit_image.getchannel("A").getextrema()[0] != 0:
        raise ValueError("Expected transparent two-by-two YUI reaction sheet")
    side = hit_image.width // 2
    hit_atlas = Image.new("RGBA", (512, 512))
    for i in range(4):
        frame = hit_image.crop((i % 2 * side, i // 2 * side, (i % 2 + 1) * side, (i // 2 + 1) * side))
        hit_atlas.paste(frame.resize((240, 240), Image.Resampling.LANCZOS), (i % 2 * 256 + 8, i // 2 * 256 + 8))
    hit_atlas.save(destination / "yui-hit.png", optimize=True)
    clips["yuiHit"] = {
        "src": "/chase/yui-hit.png",
        "frames": 4,
        "columns": 2,
        "rows": 2,
        "cell": 256,
        "faces": [],
        "generated": True,
        "tool": "imagegen",
        "source_sha256": hashlib.sha256(hit_source.read_bytes()).hexdigest(),
    }
    js = "/* Derived from PC2005-cloud/dsh-pet 0.3.7; assets: open source, noncommercial. */\n"
    js += (
        "(function(root){const clips="
        + json.dumps(clips, ensure_ascii=False, separators=(",", ":"))
        + ";if(typeof module==='object'&&module.exports)module.exports=clips;else root.CatfoodWhaleClips=clips;})(typeof window==='object'?window:globalThis);\n"
    )
    (root / "static/whale-clips.js").write_text(js)
    provenance = {
        "author": "PC2005-cloud",
        "repository": "https://github.com/PC2005-cloud/dsh-pet",
        "version": "0.3.7",
        "license": "Assets: open source use permitted; commercial use prohibited; attribution required.",
        "source_dimensions": [640, 360],
        "source_fps": 24,
        "crop_square": square,
        "alpha_verified": True,
        "run_seam_rms": run[2],
        "clips": clips,
        "yui_running": yui,
    }
    (destination / "dsh-whale-provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2))
    if args.qa:
        (args.qa / "asset-provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2))
    print(
        json.dumps(
            {
                "crop": square,
                "clips": {
                    k: {
                        "frames": v["frames"],
                        "source_frames": v.get("source_frames"),
                        "generated": v.get("generated", False),
                    }
                    for k, v in clips.items()
                },
                "run_seam_rms": run[2],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
