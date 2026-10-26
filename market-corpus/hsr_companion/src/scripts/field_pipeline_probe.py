"""Opt-in, isolated-process observers. Never substitutes production results."""
from __future__ import annotations

import copy
import hashlib
import inspect
import re
import time
from contextlib import ExitStack
from contextvars import ContextVar
from io import BytesIO
from unittest.mock import patch


def facts(value):
    if not isinstance(value, dict):
        return []
    return copy.deepcopy([f for f in (value.get("screen_facts") or {}).get("facts", [])
        if f.get("scope") in {"equipment", "location", "quest", "attributes"}])


def summary(value):
    if not isinstance(value, dict):
        return {"returned": "none" if value is None else "superseded_or_nonmapping"}
    return {"page": value.get("page_type", (value.get("page") or {}).get("page_type")),
            "serial": value.get("observation_serial", (value.get("screen_facts") or {}).get("observation_serial")),
            "observed_at": value.get("observed_at"), "fresh": value.get("fresh"), "facts": facts(value),
            "read_scope": value.get("read_scope"),
            "fact_count": len((value.get("screen_facts") or {}).get("facts", [])),
            "equipment_labels": [{k: f.get(k) for k in ("entity_id", "value")} for f in
                (value.get("screen_facts") or {}).get("facts", []) if f.get("kind") == "entity_label"
                and f.get("entity_type") in {"light_cone", "relic_set"}][:24],
            "field_checks": copy.deepcopy((value.get("screen_facts") or {}).get("field_checks", []))}


def coords(value):
    if isinstance(value, (set, frozenset, list, tuple)):
        return sorted([list(c) for c in value if isinstance(c, (tuple, list)) and len(c) == 4])
    return []


class FieldPipelineProbe:
    """Bounded diagnostic data only; wrappers call the original exactly once."""
    def __init__(self, owner, *, limit=4000, keep_failed_numeric=False):
        self.owner, self.limit = owner, limit
        self.events, self.dropped, self.errors = [], 0, 0
        self.phase = "baseline"
        self.keep_failed_numeric = keep_failed_numeric
        self.numeric_failures = []  # At most three in-memory frames, never serialized.
        self.stack = ExitStack()
        self.read_context = ContextVar("hsr_diagnostic_reader_context", default=None)

    def emit(self, kind, **data):
        try:
            if len(self.events) >= self.limit:
                self.dropped += 1
                return
            self.events.append(copy.deepcopy({"kind": kind, "at": time.time(), "phase": self.phase,
                "query_generation": getattr(self.owner, "_answer_query_generation", 0), **data}))
        except Exception:
            self.errors += 1  # A diagnostic failure cannot authorize or reject data.

    def __enter__(self):
        from plugin.plugins.hsr_companion.adapters import local_vision, relic_values
        from plugin.plugins.hsr_companion.runtime import delivery_scope, live_refresh, query_reuse
        from plugin.plugins.hsr_companion.runtime.observation_store import ObservationStore
        from plugin.plugins.hsr_companion.runtime.screen_facts import _boxes
        owner, adapter = self.owner, self.owner._vision

        from plugin.plugins.hsr_companion.runtime import fact_lifecycle
        original_late = fact_lifecycle.late_read_proved
        def late_read(target, analysis):
            result = original_late(target, analysis)
            if target is owner:
                candidate = analysis.get("_candidate_validation") or {}
                receipt = analysis.get("_live_validation") or {}
                checked = receipt.get("checked_frame_at")
                self.emit("late_candidate_admission", retained=result,
                    candidate_proof_present=bool(candidate), answer_proof_present=bool(receipt),
                    old_frame_age=round(time.time() - checked, 4) if isinstance(checked, (float, int)) else None,
                    old_query_generation_matches=receipt.get("query_generation") == getattr(owner, "_query_refresh_generation", 0),
                    answer_permission_implied=False)
            return result
        self.stack.enter_context(patch.object(fact_lifecycle, "late_read_proved", late_read))

        original_extract = local_vision.extract_screen_facts
        def extract(boxes, **kw):
            trace = kw.get("trace") if kw.get("trace") is not None else []
            result = original_extract(boxes, **{**kw, "trace": trace})
            materialized = _boxes(boxes, kw["width"], kw["height"])
            page = kw.get("page", {}).get("page_type")
            materialized = [b for b in materialized if "UID" not in str(b.get("text", "")).upper() and
                (b["top"] <= kw["height"] * .8 if page == "character_detail" else
                 b["left"] >= kw["width"] * .55 if page == "relic_detail" else
                 b["right"] <= kw["width"] * .55 and b["bottom"] <= kw["height"] * .6)]
            source = self.read_context.get()
            self.emit("ocr_to_parser", captured_at=(source or {}).get("captured_at"), reader=source,
                      main_task_capture_at=getattr(owner, "_analysis_capture_at", ""), page=page,
                      boxes=materialized, trace=trace, facts=result.get("facts", []))
            return result
        self.stack.enter_context(patch.object(local_vision, "extract_screen_facts", extract))

        original_verify = adapter.verify_text_regions
        def verify(payload, proofs, **kw):
            trace = kw.get("trace") if kw.get("trace") is not None else []
            start = len(trace)
            result = original_verify(payload, proofs, **{**kw, "trace": trace})
            self.emit("literal_review", input_sha256=hashlib.sha256(payload).hexdigest(),
                      proofs=proofs, confirmed_keys=list(result), trace=trace[start:])
            if self.keep_failed_numeric and len(self.numeric_failures) < 3:
                attempts = [t for t in trace[start:] if t.get("stage") == "reread_attempt"
                    and re.fullmatch(r"\+?\d[\d,]*(?:\.\d+)?%?", t.get("expected", ""))
                    and (t.get("quality_valid", t.get("valid"))) and t.get("actual") != t.get("expected")]
                if attempts:
                    key = attempts[0]["key"]
                    proof = next((p for p in proofs if tuple(p["region"][k] for k in
                        ("left", "top", "right", "bottom")) == tuple(key[0]) and p.get("text") == key[1]), None)
                    if proof:
                        self.numeric_failures.append((payload, copy.deepcopy(proof)))
            return result
        self.stack.enter_context(patch.object(adapter, "verify_text_regions", verify))

        for name in ("read_relic_table", "refine_relic_values"):
            original = getattr(relic_values, name)
            def number_read(image, boxes, _original=original, _name=name, **kw):
                calls = []
                backend = kw["backend"]
                class TracedBackend:
                    def __getattr__(self, key):
                        return getattr(backend, key)

                    def extract_text_with_boxes(self, sample):
                        result = backend.extract_text_with_boxes(sample)
                        calls.append({"size": list(sample.size), "boxes": [_box_value(b) for b in result[1]]})
                        return result

                from plugin.plugins.hsr_companion.adapters.text_reader import _box_value
                result = _original(image, boxes, **{**kw, "backend": TracedBackend()})
                self.emit(_name, details=result[1], boxes=_boxes(result[0], image.width, image.height),
                          backend_outputs=calls)
                # Diagnostic comparison only: retain failed source crops, not an
                # invented numeric answer. The actual malformed OCR text remains
                # the label; none of these crops can authorize formal delivery.
                if self.keep_failed_numeric and _name == "refine_relic_values" and not self.numeric_failures:
                    failed = [b for b in result[0] if (b.get("numeric_recheck") or {}).get("confirmed") is False][:3]
                    if failed:
                        encoded = BytesIO()
                        image.save(encoded, format="PNG")
                        for b in failed:
                            region = {k: b[k] / (image.width if k in {"left", "right"} else image.height)
                                      for k in ("left", "top", "right", "bottom")}
                            self.numeric_failures.append((encoded.getvalue(), {"region": region,
                                "text": b["numeric_recheck"]["original_text"], "diagnostic_unbound_crop": True}))
                return result
            self.stack.enter_context(patch.object(relic_values, name, number_read))

        original_stable = live_refresh.stable_screen_content
        def stable(analysis, original, latest, **kw):
            trace = kw.get("trace") if kw.get("trace") is not None else []
            start = len(trace)
            result = original_stable(analysis, original, latest, **{**kw, "trace": trace})
            self.emit("producer_fact_review", original_sha256=hashlib.sha256(original).hexdigest(),
                validation_sha256=hashlib.sha256(latest).hexdigest(), before=summary(analysis),
                after=summary(result), trace=trace[start:])
            return result
        self.stack.enter_context(patch.object(live_refresh, "stable_screen_content", stable))

        original_trace = live_refresh.trace_refresh
        def trace_refresh(target, stage, **details):
            original_trace(target, stage, **details)
            if target is not owner:
                return
            caller = inspect.currentframe().f_back
            try:
                values = caller.f_locals
                context = {key: coords(values.get(key)) for key in ("coordinates", "proved", "admitted", "unchanged")
                           if isinstance(values.get(key), (set, frozenset))}
                if stage == "query_started" and values.get("query_plan") is not None:
                    context["query_plan"] = values["query_plan"].public()
                checked = values.get("same_text")
                if checked is not None and hasattr(checked, "confirmed_coordinates"):
                    context["review_state"] = checked.state
                    context["confirmed_coordinates"] = coords(checked.confirmed_coordinates)
                current_frame = values.get("latest")
                if current_frame is not None and hasattr(current_frame, "timestamp"):
                    context["frame_seq"] = current_frame.frame_seq
                    context["frame_age"] = round(time.time() - current_frame.timestamp, 4)
                if stage == "query_guards_built":
                    context["guard_coverage"] = {
                        "full_present": bool(getattr(owner, "_next_frame_full_query_guard", None)),
                        "full_regions": len(values.get("full_regions") or []),
                        "all_rows_guarded": values.get("all_rows_guarded"),
                        "all_facts_guarded": values.get("all_facts_guarded"),
                    }
                self.emit("runtime_condition", stage=stage, details=details,
                    function=caller.f_code.co_name, context=context)
            finally:
                del caller  # Do not keep stack locals or image bytes alive.
        self.stack.enter_context(patch.object(live_refresh, "trace_refresh", trace_refresh))

        original_withhold = delivery_scope.withhold_regions
        def withhold(value, regions, **kw):
            result = original_withhold(value, regions, **kw)
            before, after = facts(value), facts(result)
            self.emit("field_withheld", coordinates=coords(regions), removed=[f for f in before if f not in after],
                      before=summary(value), after=summary(result))
            return result
        self.stack.enter_context(patch.object(delivery_scope, "withhold_regions", withhold))

        original_review = query_reuse.review_snapshot
        async def review(target, snapshot, **kw):
            if target is owner:
                self.emit("candidate_selected", candidate=summary((snapshot or {}).get("evidence")),
                          owner_serial=getattr(owner, "_observation_serial", 0),
                          pending=getattr(owner, "_observation_pending_reason", ""))
            result = await original_review(target, snapshot, **kw)
            if target is owner:
                self.emit("candidate_review_return", result=summary(result))
            return result
        self.stack.enter_context(patch.object(query_reuse, "review_snapshot", review))

        original_remember = query_reuse.remember_reusable
        def remember(target, analysis):
            result = original_remember(target, analysis)
            if target is owner:
                self.emit("cache_admission", incoming=summary(analysis),
                    full_guard_present=bool(getattr(owner, "_next_frame_full_query_guard", None)),
                    snapshots={key: summary(packet.get("evidence")) for key, packet in
                               getattr(owner, "_reusable_observations", {}).items()})
            return result
        self.stack.enter_context(patch.object(query_reuse, "remember_reusable", remember))

        original_defer = ObservationStore.defer_regions
        def defer(store, regions):
            result = original_defer(store, regions)
            if store.owner is owner:
                self.emit("store_deferred", coordinates=coords(regions),
                    serial=getattr(owner, "_observation_serial", 0),
                    pending=getattr(owner, "_observation_pending_reason", ""))
            return result
        self.stack.enter_context(patch.object(ObservationStore, "defer_regions", defer))

        original_commit = ObservationStore.commit
        def commit(store, analysis, **kw):
            result = original_commit(store, analysis, **kw)
            if store.owner is owner:
                self.emit("store_commit", observation=summary(analysis))
            return result
        self.stack.enter_context(patch.object(ObservationStore, "commit", commit))
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)
