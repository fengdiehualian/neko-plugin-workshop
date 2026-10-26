"""Local, privacy-bounded OCR adapter backed by N.E.K.O's shared RapidOCR."""

from __future__ import annotations

import threading
import time
from functools import wraps
from io import BytesIO
from typing import Any

from ..data import StructuredCatalog, normalize_lookup_text
from ..detectors import analyze_layout
from ..detectors.exploration import exploration_controls, navigation_boxes
from ..detectors.layout import PAGE_RULES, classify_supported_page, scene_control_regions
from ..detectors.scene_preview import scene_preview
from ..detectors.team import assign_team_slots, infer_complete_team_rows, locate_team_rows
from ..runtime.screen_facts import extract_screen_facts, safe_text
from ..session import visual_fingerprint
from .text_reader import ObservationSuperseded, check_current, read_native_text


def serial_ocr(method=None, *, cooperative=False):
    """All observation/reread callers share one backend execution gate."""
    if method is None:
        return lambda target: serial_ocr(target, cooperative=cooperative)
    @wraps(method)
    def run(self, *args, **kwargs):
        if not self._inference_lock.acquire(timeout=.2):
            raise ObservationSuperseded()
        queue = self._review_queue if cooperative else None
        cancelled = kwargs.get("cancelled")
        if queue is not None:
            queue.begin()
            def checkpoint():
                if cancelled is not None and cancelled():
                    return True
                queue.pump()  # Same thread still owns the reentrant native gate.
                return cancelled is not None and cancelled()
            kwargs["cancelled"] = checkpoint
        try:
            check_current(kwargs.get("cancelled"))
            return method(self, *args, **kwargs)
        finally:
            if queue is not None:
                queue.end()  # Unstarted jobs cannot escape into another pass.
            self._inference_lock.release()
    return run


class LocalVisionAdapter:
    def __init__(
        self, catalog: StructuredCatalog, *, logger: Any | None = None, avatar_pack: Any | None = None
    ) -> None:
        self.catalog = catalog
        self.logger = logger
        self._backend: Any | None = None
        self._last_error = ""
        self.avatar_pack = avatar_pack
        self._inference_lock = threading.RLock()
        from .ocr_work_queue import OCRWorkQueue
        self._review_queue = OCRWorkQueue()
        # Geometry only, never cached identities. Reuse requires a fresh,
        # independently recognized exploration page of the same pixel size.
        self._team_geometry: tuple[tuple[int, int], float, list[float]] | None = None

    def _get_backend(self) -> Any:
        if self._backend is not None:
            return self._backend
        from .upright_ocr import UprightOCRBackend, verified_models

        root = self.catalog.root / "model-comparison" / "v5"
        try:
            if verified_models(root):
                self._backend = UprightOCRBackend(root)
                return self._backend
        except (ImportError, RuntimeError, ValueError):
            # Optional isolated model/runtime unavailable: existing host OCR
            # remains usable. Never put v5 files into its default v4 cache.
            pass
        from plugin.plugins._shared.rapidocr import (
            DEFAULT_RAPIDOCR_ENGINE_TYPE,
            DEFAULT_RAPIDOCR_LANG_TYPE,
            DEFAULT_RAPIDOCR_MODEL_TYPE,
            DEFAULT_RAPIDOCR_OCR_VERSION,
            RapidOcrBackend,
        )

        self._backend = RapidOcrBackend(
            install_target_dir_raw="",
            engine_type=DEFAULT_RAPIDOCR_ENGINE_TYPE,
            lang_type=DEFAULT_RAPIDOCR_LANG_TYPE,
            model_type=DEFAULT_RAPIDOCR_MODEL_TYPE,
            ocr_version=DEFAULT_RAPIDOCR_OCR_VERSION,
            plugin_id="hsr_companion",
        )
        return self._backend

    def enqueue_review(self, callback):
        """Offer one short review at a native-call boundary, never another OCR worker."""
        return self._review_queue.submit(callback)

    @staticmethod
    def _control_conditions(boxes, *, width, height, page_type):
        if page_type not in {"exploration_hud", "unknown"}:
            return {"condition": "other_layout"}
        from ..detectors.exploration import exploration_control_evidence
        return exploration_control_evidence(boxes, width=width, height=height)[1]

    def status(self) -> dict[str, Any]:
        portrait_backend: dict[str, Any] = {"available": False, "method": "opencv_sift_ransac"}
        if self.avatar_pack is not None:
            try:
                cv2 = self.avatar_pack.load_cv2()
                portrait_backend.update(available=hasattr(cv2, "SIFT_create"), version=cv2.__version__)
            except Exception as exc:
                portrait_backend["error"] = f"{type(exc).__name__}: {exc}"
        try:
            backend = self._get_backend()
            available = bool(backend.is_available())
            return {
                "backend": "neko_shared_rapidocr",
                "model": getattr(backend, "model", "PP-OCRv4/ch/mobile"),
                "available": available,
                "last_error": self._last_error,
                "portrait_backend": portrait_backend,
            }
        except Exception as exc:  # noqa: BLE001 - optional local runtime
            self._last_error = f"{type(exc).__name__}: {exc}"
            return {
                "backend": "neko_shared_rapidocr",
                "available": False,
                "last_error": self._last_error,
                "portrait_backend": portrait_backend,
            }

    def analyze_live(self, image_bytes: bytes, *, cancelled, on_scene) -> dict[str, Any]:
        return self.analyze(image_bytes, cancelled=cancelled, on_scene=on_scene)

    @serial_ocr
    def verify_text_regions(self, image_bytes: bytes, proofs: list, *, cancelled=None, trace=None) -> set:
        from .text_verification import verify_text_regions

        return verify_text_regions(self._get_backend(), image_bytes, proofs, cancelled=cancelled, trace=trace)

    def analyze_navigation(self, image_bytes: bytes, *, cancelled=None) -> dict[str, Any]:
        """One frame, scene plus native left HUD; no portrait/name pipeline."""
        return self.analyze_scene(image_bytes, cancelled=cancelled, navigation=True)

    @serial_ocr(cooperative=True)
    def analyze_scene(self, image_bytes: bytes, *, cancelled=None, navigation=False) -> dict[str, Any]:
        return self._analyze_scene(image_bytes, cancelled=cancelled, navigation=navigation)

    @serial_ocr
    def probe_scene(self, image_bytes: bytes, *, cancelled=None) -> dict[str, Any]:
        # Runs reentrantly inside the producer checkpoint. Do not begin/end
        # its cooperative queue or instantiate another recognizer.
        return self._analyze_scene(image_bytes, cancelled=cancelled)

    def _analyze_scene(self, image_bytes: bytes, *, cancelled=None, navigation=False) -> dict[str, Any]:
        """One fresh scene-only OCR call, never queue a character atlas here.

        Retain dynamic UI evidence geometry for latest-frame validation. Camera
        motion in the 3D world is not itself evidence that these controls changed.
        """
        from PIL import Image, ImageOps

        started = time.monotonic()
        line_validation = {"supported": False, "checked": 0, "confirmed": 0, "rejected": 0}
        with Image.open(BytesIO(image_bytes)) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
        if image.width * image.height > 16_000_000:
            raise ValueError("游戏画面分辨率超过本地识别限制")
        probe = image.copy()
        probe.thumbnail((1280, 1280))
        check_current(cancelled)
        call_started = time.monotonic()
        _, values = self._get_backend().extract_text_with_boxes(probe)
        backend_seconds = time.monotonic() - call_started
        calls = [{"stage": "scene_query", "size": list(probe.size),
                  "backend_call_seconds": round(backend_seconds, 3)}]
        check_current(cancelled)
        from .text_reader import _box_value

        boxes = []
        sx, sy = image.width / probe.width, image.height / probe.height
        for value in values:
            box = _box_value(value)
            boxes.append({**box, **{key: float(box.get(key) or 0) * scale for key, scale in (
                ("left", sx), ("right", sx), ("top", sy), ("bottom", sy))}})
        probe_page = classify_supported_page("\n".join(b["text"] for b in boxes), boxes, width=image.width, height=image.height)
        known_other_page = (probe_page.get("confidence") == "high" and probe_page.get("supported")
                            and probe_page.get("page_type") not in {"exploration_hud", "unknown", "map_or_quest"})
        if not known_other_page:
            # Accuracy first: readable navigation also needs native pixels;
            # a confident thumbnail can still omit a glyph or a digit.
            # Native text from the same capture, not a second/simultaneous OCR
            # producer. The full thumbnail retains menu/title precedence.
            crop = image.crop((0, 0, round(image.width * .55), round(image.height * .60)))
            check_current(cancelled)
            call_started = time.monotonic()
            _, native_values = self._get_backend().extract_text_with_boxes(crop)
            calls.append({"stage": "navigation_native", "size": list(crop.size),
                          "backend_call_seconds": round(time.monotonic() - call_started, 3)})
            check_current(cancelled)
            # Higher-resolution rereading is authoritative only inside this
            # same-frame region. Do not keep contradictory thumbnail copies
            # there. Cross-column boxes remain untrusted; do not split prose
            # into invented text/coordinates. Outside controls are retained.
            native_boxes = [_box_value(value) for value in native_values]
            from .navigation_reader import recover_navigation_controls, reread_navigation_lines

            native_boxes, control_validation = recover_navigation_controls(
                self._get_backend(), image, native_boxes, cancelled=cancelled)
            native_boxes, line_validation = reread_navigation_lines(
                self._get_backend(), image, native_boxes, cancelled=cancelled)
            line_validation["prefix_recovery"] = control_validation
            if native_values:
                boxes = [b for b in boxes if not (
                    0 <= b["left"] < b["right"] <= crop.width
                    and 0 <= b["top"] < b["bottom"] <= crop.height)]
                boxes.extend(native_boxes)
            current_page = classify_supported_page("\n".join(b["text"] for b in boxes), boxes, width=image.width, height=image.height)
            if (current_page.get("page_type") == "unknown"
                    and navigation_boxes(boxes, width=image.width, height=image.height)):
                # The thumbnail may miss the small right HUD text. Read its
                # actual native pixels once; a left crop cannot prove it.
                # This is acquisition geometry, not permission to assume HUD.
                left, top = round(image.width * .7), round(image.height * .1)
                right, bottom = image.width, round(image.height * .5)
                control_crop = image.crop((left, top, right, bottom))
                check_current(cancelled)
                call_started = time.monotonic()
                _, control_values = self._get_backend().extract_text_with_boxes(control_crop)
                calls.append({"stage": "navigation_controls_native", "size": list(control_crop.size),
                              "backend_call_seconds": round(time.monotonic() - call_started, 3)})
                check_current(cancelled)
                if control_values:
                    boxes = [b for b in boxes if not (left <= b["left"] < b["right"] <= right
                                                       and top <= b["top"] < b["bottom"] <= bottom)]
                    for value in control_values:
                        b = _box_value(value)
                        boxes.append({**b, "left": float(b["left"]) + left, "right": float(b["right"]) + left,
                                      "top": float(b["top"]) + top, "bottom": float(b["bottom"]) + top})
        result = scene_preview(boxes, width=image.width, height=image.height)
        if result is None:
            page = classify_supported_page("\n".join(b["text"] for b in boxes), boxes, width=image.width, height=image.height)
            confirmed = page["supported"] and page["confidence"] == "high"
            result = {
                "ok": True, "page": page,
                "scene": {"primary_state": page["scene"] if confirmed else "unknown",
                          "verified": bool(confirmed), "confidence": "high" if confirmed else "low",
                          "evidence": page["evidence"] if confirmed else [], "substate": page["page_type"]},
                "characters": [], "recognition": {},
            }
        page_type = result["page"]["page_type"]
        terms = {normalize_lookup_text(term) for rule in PAGE_RULES if rule["page_type"] == page_type
                 for term in (*rule["strong"], *rule["anchors"])}
        if page_type == "warp":
            terms.add("跃迁")
        regions = list((result.get("recognition") or {}).get("scene_regions") or [])
        if page_type == "exploration_hud":
            for control in exploration_controls(boxes, width=image.width, height=image.height):
                regions.append({"text": control["text"], "control": True, "region": {key: float(control[key]) / limit for key, limit in (
                    ("left", image.width), ("right", image.width), ("top", image.height), ("bottom", image.height))}})
        from ..detectors.scene_preview import exploration_control
        for box in boxes:
            text = normalize_lookup_text(box["text"])
            if float(box.get("score") or 0) < .65:
                continue
            proof = any(term in text for term in terms)
            if page_type == "exploration_hud":
                proof |= exploration_control(box, width=image.width, height=image.height)
                proof |= (text in {"1", "2", "3", "4"} and box["left"] >= image.width * .955)
                proof |= box["right"] < image.width * .55 and any(t in text for t in ("请前往", "距离目标", "追踪目标", "开启导航"))
            if proof:
                regions.append({"text": box["text"], "control": True, "region": {key: float(box[key]) / limit for key, limit in (
                    ("left", image.width), ("right", image.width), ("top", image.height), ("bottom", image.height))}})
        regions = list({tuple(r["region"][k] for k in ("left", "top", "right", "bottom")): r for r in regions}.values())
        # No proven control geometry means no current scene assertion.
        if not regions:
            result["scene"].update(primary_state="unknown", verified=False, confidence="low")
        result["recognition"].update(
            phase="navigation_complete" if navigation else "scene_only" if result["scene"]["verified"] else "scene_only_complete",
            scene_regions=regions, scene_only_query=True, navigation_only_query=bool(navigation),
            control_trace={"origin": "navigation_reader" if navigation else "scene_reader",
                           "control_count": len(regions),
                           **self._control_conditions(boxes, width=image.width, height=image.height, page_type=page_type)},
            navigation_line_validation=line_validation,
            processing_seconds=round(time.monotonic() - started, 3),
            text_reader={"full_frame_covered": False,
                         "strategy": "fresh_navigation_regions" if navigation else "fresh_scene_only",
                         "backend_calls": calls},
        )
        result["screen_facts"] = extract_screen_facts(
            boxes, width=image.width, height=image.height, page=result["page"], catalog=self.catalog,
        )
        # Keep bounded, privacy-filtered diagnostics on the local observation,
        # not in the chat answer. Distinguish OCR/layout failure from loss of
        # fields later in the packaged-runtime delivery path.
        result["recognition"]["field_reading"] = {
            "revision": "navigation_controls_v4", "box_count": len(boxes),
            "backend_class": type(self._get_backend()).__name__,
            "fact_count": len(result["screen_facts"].get("facts") or []),
            "unclassified_count": len(result["screen_facts"].get("unclassified_text") or []),
        }
        if navigation:
            result["recognition"]["navigation_rows"] = [
                {**{key: b[key] for key in ("score", "left", "top", "right", "bottom")},
                 "text": safe_text(b["text"])} for b in boxes
                if b["right"] <= image.width * .55 and b["top"] <= image.height * .6
                and "UID" not in str(b["text"]).upper()
            ][:24]
        return result

    @serial_ocr(cooperative=True)
    def analyze(self, image_bytes: bytes, *, cancelled=None, on_scene=None) -> dict[str, Any]:
        try:
            from PIL import Image, ImageOps

            with Image.open(BytesIO(image_bytes)) as opened:
                image = ImageOps.exif_transpose(opened).convert("RGB")
            started = time.monotonic()
            if image.width * image.height > 16_000_000:
                raise ValueError("游戏画面分辨率超过本地识别限制")
            ocr_error = ""
            text_details: dict[str, Any] = {}
            sent_pages = set()
            scene_results = {}

            def progress(boxes):
                check_current(cancelled)
                if on_scene is None:
                    return
                preview = scene_preview(boxes, width=image.width, height=image.height)
                if preview is not None and preview["page"]["page_type"] not in sent_pages:
                    preview["recognition"]["processing_seconds"] = round(time.monotonic() - started, 3)
                    sent_pages.add(preview["page"]["page_type"])
                    scene_results[preview["page"]["page_type"]] = preview
                    on_scene(preview)

            try:
                text, boxes, text_details = read_native_text(
                    self._get_backend(), image, catalog=self.catalog, cancelled=cancelled,
                    on_progress=progress if on_scene is not None else None,
                    verify_names=True,
                )
            except ObservationSuperseded:
                raise
            except Exception as exc:
                text, boxes = "", []
                ocr_error = f"{type(exc).__name__}: {exc}"
            native_page = classify_supported_page(text, boxes, width=image.width, height=image.height)
            if boxes and not ocr_error and native_page["page_type"] in {"unknown", "exploration_hud", "map_or_quest"}:
                from .navigation_reader import recover_navigation_controls

                boxes, prefix_details = recover_navigation_controls(self._get_backend(), image, boxes, cancelled=cancelled)
                text_details["navigation_prefix_recovery"] = prefix_details
                if prefix_details["confirmed"]:
                    text = "\n".join(b["text"] for b in boxes if not b.get("control_only"))
            layout = analyze_layout(text, boxes, self.catalog, width=image.width, height=image.height)
            known_scene = None
            # Both passes read this exact capture. A later partial/native pass
            # missing a control cannot erase an explicit verified UI label from
            # the scene probe. Conflicting or known different pages do not merge.
            if layout["page"]["page_type"] == "unknown" and len(scene_results) == 1:
                known_scene = next(iter(scene_results.values()))
                layout["page"] = dict(known_scene["page"])
                layout["scene"] = dict(known_scene["scene"])
            elif layout["page"]["page_type"] in scene_results:
                # Same-capture high-confidence controls should not be demoted
                # by the slower pass returning a weaker version of that page.
                known_scene = scene_results[layout["page"]["page_type"]]
                layout["page"] = dict(known_scene["page"])
                layout["scene"] = dict(known_scene["scene"])
            page_type = layout["page"]["page_type"]
            if page_type == "relic_detail":
                from .relic_values import read_relic_table, refine_relic_values
                boxes, table_details = read_relic_table(
                    image, boxes, page=layout["page"], backend=self._get_backend(), cancelled=cancelled,
                )
                text_details["relic_table_recheck"] = table_details
                text_details.setdefault("backend_calls", []).extend(table_details["backend_calls"])
                boxes, numeric_details = refine_relic_values(
                    image, boxes, page=layout["page"], backend=self._get_backend(), cancelled=cancelled,
                    table_bounds=table_details.get("crop") if table_details.get("accepted") else None,
                )
                text_details["relic_value_recheck"] = numeric_details
                text_details.setdefault("backend_calls", []).extend(numeric_details["backend_calls"])
            rows = locate_team_rows(boxes, width=image.width, height=image.height)
            portrait_status: dict[str, Any] = {"available": False, "reason": "portrait_pack_not_configured"}
            portrait_started = time.monotonic()
            check_current(cancelled)
            if self.avatar_pack is not None:
                portraits, portrait_status = self.avatar_pack.match(image, page_type=page_type, rows=rows)
                check_current(cancelled)
                layout["characters"], conflicts = merge_character_evidence(
                    layout["characters"], portraits, self.catalog
                )
                portrait_status["conflicting_slots"] = conflicts
            portrait_seconds = time.monotonic() - portrait_started
            if page_type in {"unknown", "exploration_hud", "map_or_quest"}:
                rows = rows or infer_complete_team_rows(layout["characters"], height=image.height)
                cached = self._team_geometry
                if not rows and page_type == "exploration_hud" and cached is not None:
                    if cached[0] == image.size and time.monotonic() - cached[1] < 30:
                        rows = list(cached[2])
                layout["characters"] = assign_team_slots(layout["characters"], rows, height=image.height)
                hud_slots = {item.get("slot") for item in layout["characters"] if item.get("slot")}
                if rows and len(hud_slots) >= 2 and page_type == "unknown":
                    layout["page"].update(
                        page_type="exploration_hud",
                        scene="exploration",
                        supported=True,
                        confidence="mixed",
                        label="探索队伍界面",
                    )
                    layout["scene"].update(
                        primary_state="exploration",
                        substate="exploration_hud",
                        verified=True,
                        confidence="mixed",
                        evidence=["有序快捷键或完整四位头像布局", "多个独立队伍位置匹配数据库"],
                    )
            else:
                rows = []
            if rows and layout["page"]["page_type"] == "exploration_hud":
                self._team_geometry = (image.size, time.monotonic(), list(rows))
            else:
                self._team_geometry = None
            control_regions = ((known_scene or {}).get("recognition", {}).get("scene_regions")
                or scene_control_regions(layout["page"], boxes, width=image.width, height=image.height))
            layout["recognition"].update(
                # A same-capture page label must not lose its identifying
                # controls when the slower text pass misses them. Keep the
                # probe's OWN geometry/literals, never another frame's.
                scene_regions=control_regions,
                control_trace={"origin": "same_capture_preview" if (known_scene or {}).get("recognition", {}).get("scene_regions") else "native_layout",
                               "control_count": len(control_regions),
                               **self._control_conditions(boxes, width=image.width, height=image.height, page_type=page_type)},
                method="ocr_and_portrait" if portrait_status.get("available") else "ocr_name_label",
                portrait_model=False,
                portrait_templates=portrait_status,
                confirmed_label_count=len(layout["characters"]),
                source_size=list(image.size),
                native_crops=True,
                text_reader=text_details,
                team_rows=[round(y / image.height, 5) for y in rows],
                processing_seconds=round(time.monotonic() - started, 3),
                portrait_seconds=round(portrait_seconds, 3),
            )
            for character in layout["characters"]:
                region = character.get("region") or {}
                try:
                    left = max(0, int(float(region.get("left") or 0.0) * image.width) - 12)
                    top = max(0, int(float(region.get("top") or 0.0) * image.height) - 8)
                    right = min(image.width, int(float(region.get("right") or 0.0) * image.width) + 12)
                    bottom = min(image.height, int(float(region.get("bottom") or 0.0) * image.height) + 8)
                    if right > left and bottom > top:
                        cropped = image.crop((left, top, right, bottom))
                        payload = BytesIO()
                        cropped.save(payload, format="PNG")
                        character["visual_fingerprint"] = visual_fingerprint(payload.getvalue())
                except (TypeError, ValueError, OSError):
                    pass
            self._last_error = ocr_error
            return {
                "ok": not ocr_error or bool(layout["characters"]),
                "ocr": {
                    "backend": "neko_shared_rapidocr",
                    "text": text,
                    "line_count": len(boxes),
                    "text_preview": " ".join(text.split())[:300],
                    "error": ocr_error,
                },
                "page": layout["page"],
                "scene": layout["scene"],
                "characters": layout["characters"],
                "recognition": layout["recognition"],
                "screen_facts": extract_screen_facts(
                    boxes, width=image.width, height=image.height, page=layout["page"], catalog=self.catalog,
                ),
                "image_stored": False,
                "catalog_revision": self.catalog.revision,
            }
        except ObservationSuperseded:
            raise
        except Exception as exc:  # noqa: BLE001 - degrade safely to unknown
            self._team_geometry = None
            self._last_error = f"{type(exc).__name__}: {exc}"
            if self.logger is not None:
                try:
                    self.logger.warning("HSR local OCR unavailable: %s", self._last_error)
                except Exception:
                    pass
            return {
                "ok": False,
                "ocr": {
                    "backend": "neko_shared_rapidocr",
                    "text": "",
                    "line_count": 0,
                    "text_preview": "",
                    "error": self._last_error,
                },
                "scene": {
                    "primary_state": "unknown",
                    "substate": "unknown",
                    "confidence": "low",
                    "evidence": [],
                    "classifier": "hsr_layout_ocr_v1",
                    "verified": False,
                },
                "page": {
                    "page_type": "unknown",
                    "label": "暂不支持的画面",
                    "supported": False,
                },
                "characters": [],
                "image_stored": False,
                "catalog_revision": self.catalog.revision,
            }

    def close(self) -> None:
        backend = self._backend
        self._backend = None
        self._team_geometry = None
        if backend is not None:
            close = getattr(backend, "close", None)
            if callable(close):
                close()


__all__ = ["LocalVisionAdapter"]


def merge_character_evidence(labels, portraits, catalog):
    """OCR and portraits must not silently disagree about the same HUD slot."""
    accepted = [dict(item) for item in labels]
    conflicts = 0
    for portrait in portraits:
        record = catalog.get(str(portrait.get("entity_id") or ""))
        if not record or record.get("entity_type") != "character" or record.get("name") != portrait.get("name"):
            continue
        region = portrait.get("region") or {}
        for label in accepted:
            label_region = label.get("region") or {}
            if label.get("identity_scope") == "screen_label" and label.get("entity_id") == portrait.get("entity_id"):
                if (
                    abs(
                        (
                            label_region.get("top", 0)
                            + label_region.get("bottom", 0)
                            - region.get("top", 0)
                            - region.get("bottom", 0)
                        )
                        / 2
                    )
                    < 0.05
                    and label_region.get("left", 0) >= 0.72
                ):
                    label.update(
                        identity_scope=portrait["identity_scope"],
                        region=dict(region),
                        evidence="ocr_and_upstream_portrait_agree",
                    )
        nearby = [
            item
            for item in accepted
            if item.get("identity_scope") in {"hud_label", "hud_portrait"}
            and abs(
                (item["region"]["top"] + item["region"]["bottom"] - region.get("top", 0) - region.get("bottom", 0)) / 2
            )
            < 0.05
            and abs(
                (item["region"]["left"] + item["region"]["right"] - region.get("left", 0) - region.get("right", 0)) / 2
            )
            < 0.22
        ]
        if any(item["entity_id"] != portrait["entity_id"] for item in nearby):
            accepted = [item for item in accepted if item not in nearby]
            conflicts += 1
            continue
        if any(item["entity_id"] == portrait["entity_id"] for item in nearby):
            for item in nearby:
                item["evidence"] = "ocr_and_upstream_portrait_agree"
            continue
        if not any(item["entity_id"] == portrait["entity_id"] for item in accepted):
            accepted.append(dict(portrait))
    return accepted, conflicts
