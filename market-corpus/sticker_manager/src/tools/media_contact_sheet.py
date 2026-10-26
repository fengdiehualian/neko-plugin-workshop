"""Create a source / previous / current comparison at matching animation times."""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

from bundled_media import open_pack
from PIL import Image, ImageDraw


def frame_at(data: bytes, moment: int) -> Image.Image:
    with Image.open(io.BytesIO(data)) as image:
        elapsed = 0
        for index in range(image.n_frames):
            image.seek(index)
            image.load()
            elapsed += image.info.get("duration", 100)
            if elapsed > moment:
                break
        return image.convert("RGBA")


def build(source: Path, baseline: Path, current: Path, report: Path, target: Path) -> None:
    rows = json.loads(report.read_text(encoding="utf-8"))["assets"]
    rows.sort(key=lambda row: row["output_size"][0])
    picked = rows[:4] + rows[len(rows) // 2:len(rows) // 2 + 4] + rows[-4:]
    sheet = Image.new("RGB", (3 * 256, len(picked) * 270 + 36), "#f2f2f2")
    draw = ImageDraw.Draw(sheet)
    for column, label in enumerate(("Original GIF", "Previous GIF", "Full-motion WebP")):
        draw.text((column * 256 + 8, 10), label, fill="black")
    with open_pack(baseline) as old, open_pack(current) as new:
        manifest = json.loads(new.read("manifest.json"))
        lookup = {row["source_file"]: row["delivery_file"] for row in manifest["stickers"]}
        for index, row in enumerate(picked):
            filename = row["source_file"]
            moment = row["duration_ms"] // 2
            images = [(source / filename).read_bytes(), old.read("stickers/" + filename),
                      new.read("delivery/" + lookup[filename])]
            for column, data in enumerate(images):
                frame = frame_at(data, moment)
                frame = frame.resize((240, 240), Image.Resampling.LANCZOS)
                white = Image.new("RGBA", frame.size, "white")
                rendered = Image.alpha_composite(white, frame).convert("RGB")
                sheet.paste(rendered, (column * 256 + 8, 36 + index * 270))
            label = f"{filename.split('_')[0]}  {row['output_size'][0]}px  {row['quality']}  RMS {row['mean_rgb_rms']:.2f}"
            draw.text((8, 280 + index * 270), label, fill="black")
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    build(args.source, args.baseline, args.current, args.report, args.out)


if __name__ == "__main__":
    main()
