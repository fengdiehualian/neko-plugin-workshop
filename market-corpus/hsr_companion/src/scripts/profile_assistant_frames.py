"""Read-only helper/codec timing. No screenshot files, game input or host messages."""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import time


def emit(value):
    print(json.dumps(value, ensure_ascii=True), flush=True)


def profile(url, samples, codecs):
    from PIL import Image, features

    from plugin.plugins.hsr_companion.assistant import VisionAssistantClient

    client = VisionAssistantClient(base_url=url)
    last = None
    for index in range(samples):
        before = client.health()
        started = time.perf_counter()
        frame = client.fetch_frame(0)
        elapsed = time.perf_counter() - started
        age = time.time() - frame.timestamp if frame else None
        after = client.health()
        # Optional v0.9.3 timing extension, absent from the old helper/client's
        # health projection. Read via the same validated, bounded JSON route.
        raw = client._get_json("/health")
        emit({"kind": "live_interface", "sample": index + 1, "service_version": client.service_version,
              "transfer_seconds": round(elapsed, 4), "received_age_seconds": round(age, 4) if age is not None else None,
              "before_seq": before.get("frame_seq"), "received_seq": frame.frame_seq if frame else None,
              "after_seq": after.get("frame_seq"), "bytes": len(frame.image_bytes) if frame else None,
              "running": (after.get("game") or {}).get("running"), "connected": after.get("connected"),
              "supply_health_seq": raw.get("frame_seq"), "supply": raw.get("supply")})
        if frame:
            last = frame
        time.sleep(.35)
    if not codecs or last is None:
        return
    started = time.perf_counter()
    with Image.open(io.BytesIO(last.image_bytes)) as loaded:
        image = loaded.convert("RGB")
    emit({"kind": "decode", "seconds": round(time.perf_counter() - started, 4), "size": image.size})
    expected = hashlib.sha256(image.tobytes()).digest()
    choices = [("png1", "PNG", {"compress_level": 1}), ("png0", "PNG", {"compress_level": 0}), ("bmp_rgb24", "BMP", {})]
    if features.check("webp"):
        choices.append(("webp_lossless0", "WEBP", {"lossless": True, "quality": 100, "method": 0}))
    for name, fmt, options in choices:
        out = io.BytesIO()
        started = time.perf_counter()
        image.save(out, format=fmt, **options)
        encode_seconds = time.perf_counter() - started
        payload = out.getvalue()
        started = time.perf_counter()
        with Image.open(io.BytesIO(payload)) as decoded:
            same = decoded.size == image.size and hashlib.sha256(decoded.convert("RGB").tobytes()).digest() == expected
        decode_seconds = time.perf_counter() - started
        started = time.perf_counter()
        encoded = base64.b64encode(payload).decode("ascii")
        base64_seconds = time.perf_counter() - started
        started = time.perf_counter()
        body = json.dumps({"image_base64": encoded}, separators=(",", ":")).encode()
        json_seconds = time.perf_counter() - started
        emit({"kind": "codec_microbenchmark_not_new_capture", "codec": name, "bytes": len(payload),
              "encode_seconds": round(encode_seconds, 4), "decode_seconds": round(decode_seconds, 4),
              "base64_seconds": round(base64_seconds, 4), "json_seconds": round(json_seconds, 4),
              "json_bytes": len(body), "native_pixels_exact": same})
        assert same, "Lossless codec changed native pixels"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assistant-url", default="http://127.0.0.1:8219")
    parser.add_argument("--samples", type=int, default=3, choices=range(1, 11))
    parser.add_argument("--codecs", action="store_true")
    args = parser.parse_args()
    profile(args.assistant_url, args.samples, args.codecs)
