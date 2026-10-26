"""Build full-motion official WebP assets within the inline transport budget.

The existing pack is the source of captions, groups and image-history aliases.
This tool never edits the source directory and publishes the result atomically.
Adjacent identical frames may merge, but unique motion and timing must survive.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
import tempfile
import zipfile
from concurrent.futures import ProcessPoolExecutor
from contextlib import nullcontext
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

BUDGET = 250 * 1024


def frames_of(data: bytes) -> tuple[list, list[int], int]:
    frames, durations = [], []
    with Image.open(io.BytesIO(data)) as image:
        loop = image.info.get("loop", 0)
        for index in range(image.n_frames):
            image.seek(index)
            image.load()
            frame = image.convert("RGBA")
            duration = image.info.get("duration", 100)
            if durations and frame.tobytes() == frames[-1].tobytes():
                durations[-1] += duration
            else:
                frames.append(frame)
                durations.append(duration)
    return frames, durations, loop


def encode_asset(data: bytes) -> tuple[bytes, dict]:
    frames, durations, loop = frames_of(data)
    width, height = frames[0].size
    chosen = None
    for edge, quality in [(240, 100), (240, 90), (240, 75), (240, 60), (240, 45), (240, 30),
                          (240, 20), (220, 30), (200, 30), (180, 30), (160, 30), (140, 30)]:
        size = (min(width, edge), min(height, edge))
        scaled = [frame.resize(size, Image.Resampling.LANCZOS) if frame.size != size else frame for frame in frames]
        output = io.BytesIO()
        scaled[0].save(
            output, format="WEBP", save_all=True, append_images=scaled[1:],
            duration=durations, loop=loop, lossless=quality == 100, quality=quality, method=4,
            minimize_size=True, allow_mixed=False,
        )
        payload = output.getvalue()
        if len(payload) <= BUDGET:
            chosen = (payload, size, quality, scaled)
            break
    if chosen is None:
        raise ValueError("Cannot preserve full motion within the image budget")
    payload, size, quality, scaled = chosen
    decoded, actual_durations, actual_loop = frames_of(payload)
    if sum(actual_durations) != sum(durations) or actual_loop != loop:
        raise ValueError(f"WebP changed timing: {sum(durations)} -> {sum(actual_durations)}")
    original_boundaries, actual_boundaries = [], []
    elapsed = 0
    for duration in durations:
        elapsed += duration
        original_boundaries.append(elapsed)
    elapsed = 0
    for duration in actual_durations:
        elapsed += duration
        actual_boundaries.append(elapsed)
    if not set(actual_boundaries).issubset(original_boundaries):
        raise ValueError("WebP moved animation boundaries")
    errors = []
    moment, position = 0, 0
    for source, duration in zip(scaled, durations):
        while actual_boundaries[position] <= moment:
            position += 1
        encoded = decoded[position]
        moment += duration
        if source.getchannel("A").tobytes() != encoded.getchannel("A").tobytes():
            raise ValueError("WebP encoding changed transparency")
        white = Image.new("RGBA", size, "white")
        a = Image.alpha_composite(white, source).convert("RGB")
        b = Image.alpha_composite(white, encoded).convert("RGB")
        errors.append(sum(ImageStat.Stat(ImageChops.difference(a, b)).rms) / 3)
    return payload, {
        "source_bytes": len(data), "output_bytes": len(payload), "source_size": [width, height],
        "output_size": list(size), "quality": "lossless" if quality == 100 else quality,
        "unique_frames": len(frames), "duration_ms": sum(durations),
        "encoded_frames": len(decoded), "encoder_merged_frames": len(frames) - len(decoded),
        "mean_rgb_rms": sum(errors) / len(errors), "max_rgb_rms": max(errors),
    }

ROOT = Path(__file__).resolve().parents[1]
_READER_SPEC = importlib.util.spec_from_file_location("sticker_media_reader", ROOT / "services/bundled_pack.py")
_reader = importlib.util.module_from_spec(_READER_SPEC)
_READER_SPEC.loader.exec_module(_reader)


def encode_cached(task: tuple[bytes, str | None]) -> tuple[bytes, dict]:
    data, directory = task
    key = hashlib.sha256(b"full-motion-originals-dual-v2\0" + data).hexdigest()
    if directory:
        cache = Path(directory)
        cache.mkdir(parents=True, exist_ok=True)
        image_path, report_path = cache / (key + ".webp"), cache / (key + ".json")
        if image_path.exists() and report_path.exists():
            payload = image_path.read_bytes()
            report = json.loads(report_path.read_text(encoding="utf-8"))
            if report.get("output_sha256") == hashlib.sha256(payload).hexdigest():
                return payload, report
    payload, report = encode_asset(data)
    report["output_sha256"] = hashlib.sha256(payload).hexdigest()
    if directory:
        image_path.write_bytes(payload)
        report_path.write_text(json.dumps(report), encoding="utf-8")
    return payload, report


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_official_pack(
    source_dir: Path,
    template_path: Path,
    target: Path,
    *,
    version: int | None = None,
    workers: int = 1,
    cache_dir: Path | None = None,
) -> dict:
    """Re-encode originals, retaining labels and every historical fingerprint."""
    with _reader.open_pack(template_path) as template:
        manifest = json.loads(template.read("manifest.json").decode("utf-8"))
        entries = manifest.get("stickers")
        if not isinstance(entries, list) or not entries:
            raise ValueError("Template has no stickers")
        old_version = manifest.get("pack_version", 0)
        if isinstance(old_version, bool) or not isinstance(old_version, int):
            old_version = 0
        next_version = old_version + 1 if version is None else version
        if isinstance(next_version, bool) or not isinstance(next_version, int) or next_version <= old_version:
            raise ValueError("Pack version must increase")
        assets: list[tuple[str, bytes]] = []
        reports = []
        source_rows = []
        seen: set[str] = set()
        total = 0
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("Invalid sticker entry")
            name = entry.get("source_file", entry.get("file"))
            if not isinstance(name, str) or not name or Path(name).name != name or any(c in name for c in "\\/:"):
                raise ValueError("Unsafe source filename")
            if name.startswith(".") or name in seen:
                raise ValueError("Duplicate or hidden source filename")
            seen.add(name)
            data = (source_dir / name).read_bytes()
            if data[:6] not in {b"GIF87a", b"GIF89a"}:
                raise ValueError(f"Source is not a GIF: {name}")
            if len(data) > 8 * 1024 * 1024:
                raise ValueError(f"Source exceeds library image limit: {name}")
            old_file = entry.get("file")
            old_bytes = template.read("stickers/" + old_file)
            old_digest = digest(old_bytes)
            if entry.get("sha256") != old_digest:
                raise ValueError(f"Template image digest mismatch: {old_file}")
            source_rows.append((entry, name, data, old_digest))
        tasks = [(row[2], str(cache_dir) if cache_dir else None) for row in source_rows]
        pool = ProcessPoolExecutor(max_workers=workers) if workers > 1 else nullcontext()
        with pool as executor:
            encoded = []
            results = executor.map(encode_cached, tasks) if executor else map(encode_cached, tasks)
            for index, result in enumerate(results, start=1):
                encoded.append(result)
                print(f"[MEDIA] {index}/{len(source_rows)}", flush=True)
        for (entry, name, source, old_digest), (data, report) in zip(source_rows, encoded):
            new_digest = digest(source)
            aliases = entry.get("legacy_sha256", entry.get("previous_sha256", []))
            if not isinstance(aliases, list):
                raise ValueError(f"Invalid fingerprint history: {name}")
            history = list(dict.fromkeys([*aliases, old_digest]))
            output_name = Path(name).stem + ".webp"
            entry.update(
                file=name,
                source_file=name,
                sha256=new_digest,
                delivery_file=output_name,
                delivery_sha256=digest(data),
                source_sha256=digest(source),
                asset_id=entry.get("asset_id", "official-" + name.split("_", 1)[0]),
                legacy_sha256=[value for value in history if value != new_digest],
            )
            assets.append(("stickers/" + name, source))
            assets.append(("delivery/" + output_name, data))
            reports.append({"source_file": name, **report})
            total += len(data)
    manifest["pack_version"] = next_version
    manifest["media_version"] = 1
    manifest["media_policy"] = "original_gif_with_full_motion_delivery"
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
    os.close(handle)
    temporary_path = Path(temporary)
    try:
        with zipfile.ZipFile(temporary_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
            for name, data in assets:
                archive.writestr(name, data)
        temporary_path.replace(target)
    finally:
        temporary_path.unlink(missing_ok=True)
    return {"version": next_version, "entries": len(reports), "delivery_bytes": total,
            "original_bytes": sum(len(row[2]) for row in source_rows), "path": str(target), "assets": reports}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT.parent / "GIF")
    parser.add_argument("--template", type=Path, default=ROOT / "official" / "official_pack.zip")
    parser.add_argument("--out", type=Path, default=ROOT.parent / "dist" / "official_original_pack.zip")
    parser.add_argument("--version", type=int)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--cache", type=Path, default=ROOT / ".tmpgate" / "media-cache")
    args = parser.parse_args()
    report = build_official_pack(
        args.source, args.template, args.out, version=args.version, workers=args.workers, cache_dir=args.cache,
    )
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "assets"}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
