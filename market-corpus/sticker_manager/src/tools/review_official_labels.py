"""Render spaced animation frames with existing labels for manual semantic review."""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

from bundled_media import open_pack
from PIL import Image, ImageDraw, ImageFont


def review(pack: Path, output: Path, ids: str = "") -> None:
    output.mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 18)
    with open_pack(pack) as archive:
        entries = json.loads(archive.read("manifest.json"))["stickers"]
        if ids:
            entries = [row for row in entries if row["file"].split("_", 1)[0] in ids.split(",")]
            for row in entries:
                with Image.open(io.BytesIO(archive.read("stickers/" + row["file"]))) as image:
                    sheet = Image.new("RGB", (1200, 440), "white")
                    for column in range(12):
                        image.seek(round((image.n_frames - 1) * column / 11))
                        frame = image.convert("RGBA")
                        frame.thumbnail((195, 195))
                        sheet.paste(frame, ((column % 6) * 200, (column // 6) * 220), frame)
                    sheet.save(output / f"focus-{row['file'].split('_', 1)[0]}.png")
            return
        for start in range(0, len(entries), 20):
            sheet = Image.new("RGB", (1200, 1500), "white")
            draw = ImageDraw.Draw(sheet)
            for index, row in enumerate(entries[start:start + 20]):
                left, top = (index % 2) * 600, (index // 2) * 150
                draw.text((left + 6, top + 2), row["file"].split("_", 1)[0] + " " + row["caption"],
                          font=font, fill="black")
                with Image.open(io.BytesIO(archive.read("stickers/" + row["file"]))) as image:
                    for column, fraction in enumerate((0.0, 0.3, 0.65, 0.95)):
                        image.seek(round((image.n_frames - 1) * fraction))
                        frame = image.convert("RGBA")
                        frame.thumbnail((138, 120))
                        sheet.paste(frame, (left + 6 + column * 145, top + 28), frame)
            target = output / f"review-{start // 20 + 1:02d}.png"
            sheet.save(target)
            print(target)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, default=Path(__file__).resolve().parents[1] / "official/official_pack.zip")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--ids", default="")
    args = parser.parse_args()
    review(args.pack, args.out, args.ids)
