"""Same in-memory frame/proof, line recognizer vs detector+recognizer. Diagnostic only."""
from io import BytesIO
import hashlib
import math
import time


def compare(adapter, payload, proof):
    from PIL import Image
    from plugin.plugins.hsr_companion.adapters.text_reader import _box_value
    from plugin.plugins.hsr_companion.adapters.text_verification import text_key
    with Image.open(BytesIO(payload)) as opened:
        image = opened.convert("RGB")
    r = proof["region"]
    bounds = (max(0, math.floor(r["left"] * image.width) - 5), max(0, math.floor(r["top"] * image.height) - 4),
              min(image.width, math.ceil(r["right"] * image.width) + 5),
              min(image.height, math.ceil(r["bottom"] * image.height) + 4))
    crop, records = image.crop(bounds), []
    tight_bounds = (max(0, math.floor(r["left"] * image.width)), max(0, math.floor(r["top"] * image.height)),
                    min(image.width, math.ceil(r["right"] * image.width)),
                    min(image.height, math.ceil(r["bottom"] * image.height)))
    tight = image.crop(tight_bounds)
    # Called after resident drain, before the installed observer is restored.
    with adapter._inference_lock:
        backend = adapter._get_backend()
        for scale in (2, 3, 4):
            resized = crop.resize((crop.width * scale, crop.height * scale), Image.Resampling.BICUBIC)
            sample = Image.new("RGB", (resized.width + 32, resized.height + 32), (24, 24, 24))
            sample.paste(resized, (16, 16))
            line = backend.recognize_line(resized)
            _, detected = backend.extract_text_with_boxes(sample)
            rows = sorted([_box_value(b) for b in detected], key=lambda b: b["left"])
            variants = {}
            for name, source in (("tight_source", tight), ("tight_neutral", tight)):
                resized_source = source.resize((source.width * scale, source.height * scale), Image.Resampling.BICUBIC)
                if name == "tight_neutral":
                    padding = max(2, round(source.height * .25)) * scale
                    neutral = Image.new("RGB", (resized_source.width + padding * 2,
                                                resized_source.height + padding * 2), (24, 24, 24))
                    neutral.paste(resized_source, (padding, padding))
                    resized_source = neutral
                started = time.monotonic()
                result = backend.recognize_line(resized_source)
                variants[name] = {"actual": text_key("".join(t for t, _ in result)),
                                  "scores": [float(s) for _, s in result],
                                  "seconds": round(time.monotonic() - started, 4)}
            records.append({"scale": scale, "variants": variants, "line": {"actual": text_key("".join(t for t, _ in line)),
                "scores": [float(s) for _, s in line]}, "detect_recognize": {
                "actual": text_key("".join(b["text"] for b in rows)), "scores": [float(b["score"]) for b in rows],
                "boxes": rows}})
    return {"input_sha256": hashlib.sha256(payload).hexdigest(), "expected": proof["text"],
            "region": r, "bounds": bounds, "tight_bounds": tight_bounds, "readings": records, "image_stored": False,
            "diagnostic_unbound_crop": proof.get("diagnostic_unbound_crop", False),
            "authorizes_production_delivery": False}
