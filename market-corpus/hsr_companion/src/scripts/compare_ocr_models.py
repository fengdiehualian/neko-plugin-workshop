"""Same live capture, existing OCR models; no images or raw text persisted."""

from __future__ import annotations

import json
import logging
import time
from io import BytesIO
from pathlib import Path

from PIL import Image
from plugin.plugins.hsr_companion.adapters.text_reader import read_native_text
from plugin.plugins.hsr_companion.adapters.upright_ocr import UprightOCRBackend
from plugin.plugins.hsr_companion.assistant import VisionAssistantClient
from plugin.plugins.hsr_companion.data import ExternalKnowledgeCatalog
from plugin.plugins.hsr_companion.detectors import analyze_layout


def main():
    logging.getLogger("httpx").setLevel(logging.WARNING)
    root = Path(__file__).resolve().parents[1]
    catalog = ExternalKnowledgeCatalog(registry=json.loads((root / "resources/character_registry.json").read_text(encoding="utf-8")))
    frame = VisionAssistantClient().fetch_frame(0)
    if frame is None:
        raise RuntimeError("No current isolated game frame")
    with Image.open(BytesIO(frame.image_bytes)) as opened:
        image = opened.convert("RGB")
    for version in ("PP-OCRv4", "PP-OCRv5"):
        model_root = catalog.root / "model-comparison" / "v5" if version == "PP-OCRv5" else None
        if model_root and not (model_root / "ch_PP-OCRv5_rec_mobile.onnx").is_file():
            print(json.dumps({"model": version, "available": False}), flush=True)
            continue
        backend = UprightOCRBackend(model_root)
        try:
            started = time.monotonic()
            text, boxes, timings = read_native_text(backend, image, catalog=catalog, verify_names=True)
            result = analyze_layout(text, boxes, catalog, width=image.width, height=image.height)
            print(json.dumps({"model": version, "seconds": round(time.monotonic() - started, 3), "page": result["page"]["page_type"],
                              "names": [c["name"] for c in result["characters"]], "coverage_complete": timings["full_frame_covered"],
                              "name_verification": timings.get("name_verification"), "backend_calls": timings.get("backend_calls")}, ensure_ascii=False), flush=True)
        finally:
            backend.close()


if __name__ == "__main__":
    main()
