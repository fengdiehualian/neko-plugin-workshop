"""Shared imports for plugin_entries mixin files.

Each mixin file does `from ._common import *` to inherit all names that
entry method bodies reference. This avoids per-file import bookkeeping
and keeps each mixin focused on its method definition.
"""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from plugin.plugins._shared.rapidocr.rapidocr_support import (
    inspect_rapidocr_installation as _inspect_rapidocr_installation,
)
from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    neko_plugin,
    plugin_entry,
    timer_interval,
    tr,
)

from ..character_profile import CharacterProfileManager
from ..dependency_status import (
    infer_inspection_failed_dependencies,
    infer_missing_dependencies,
)
from ..dxcam_support import inspect_dxcam_installation
from ..game_llm_agent import GameLLMAgent
from ..host_agent_adapter import HostAgentAdapter
from ..llm_gateway import LLMGateway
from ..memory_reader import MemoryReaderManager
from ..models import (
    ADVANCE_SPEED_MEDIUM,
    ADVANCE_SPEEDS,
    DATA_SOURCE_BRIDGE_SDK,
    DATA_SOURCE_MEMORY_READER,
    DATA_SOURCE_NONE,
    DATA_SOURCE_OCR_READER,
    MODE_CHOICE_ADVISOR,
    MODE_COMPANION,
    MODES,
    OCR_CAPTURE_PROFILE_RATIO_KEYS,
    OCR_CAPTURE_PROFILE_SAVE_SCOPE_PROCESS_FALLBACK,
    OCR_CAPTURE_PROFILE_SAVE_SCOPE_WINDOW_BUCKET,
    OCR_CAPTURE_PROFILE_SAVE_SCOPES,
    OCR_CAPTURE_PROFILE_STAGE_CONFIG,
    OCR_CAPTURE_PROFILE_STAGE_DEFAULT,
    OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
    OCR_CAPTURE_PROFILE_STAGE_GALLERY,
    OCR_CAPTURE_PROFILE_STAGE_GAME_OVER,
    OCR_CAPTURE_PROFILE_STAGE_MENU,
    OCR_CAPTURE_PROFILE_STAGE_MINIGAME,
    OCR_CAPTURE_PROFILE_STAGE_SAVE_LOAD,
    OCR_CAPTURE_PROFILE_STAGE_TITLE,
    OCR_CAPTURE_PROFILE_STAGE_TRANSITION,
    OCR_CAPTURE_PROFILE_STAGES,
    OCR_CAPTURE_PROFILE_WINDOW_BUCKETS_KEY,
    OCR_TRIGGER_MODE_AFTER_ADVANCE,
    OCR_TRIGGER_MODE_INTERVAL,
    OCR_TRIGGER_MODES,
    READER_MODE_AUTO,
    READER_MODE_MEMORY,
    READER_MODE_OCR,
    READER_MODES,
    STATE_ACTIVE,
    STATE_ERROR,
    STORE_ADVANCE_SPEED,
    STORE_BOUND_GAME_ID,
    STORE_CHARACTER_FIXED_NAME,
    STORE_CHARACTER_MODE,
    STORE_CHARACTER_PROFILE_VERSION,
    STORE_CHARACTER_PROFILES,
    STORE_CHARACTER_RUNTIME_STATE,
    STORE_DEDUPE_WINDOW,
    STORE_EVENTS_BYTE_OFFSET,
    STORE_EVENTS_FILE_SIZE,
    STORE_LAST_ERROR,
    STORE_LAST_SEQ,
    STORE_LLM_VISION_ENABLED,
    STORE_LLM_VISION_MAX_IMAGE_PX,
    STORE_MEMORY_READER_TARGET,
    STORE_MODE,
    STORE_OCR_BACKEND_SELECTION,
    STORE_OCR_CAPTURE_BACKEND,
    STORE_OCR_CAPTURE_PROFILES,
    STORE_OCR_FAST_LOOP_ENABLED,
    STORE_OCR_POLL_INTERVAL_SECONDS,
    STORE_OCR_SCREEN_TEMPLATES,
    STORE_OCR_TRIGGER_MODE,
    STORE_OCR_WINDOW_TARGET,
    STORE_PUSH_NOTIFICATIONS,
    STORE_RAPIDOCR_AUTO_DETECT_LANG,
    STORE_RAPIDOCR_AUTO_DETECT_LAST_LANG,
    STORE_RAPIDOCR_LANG_TYPE,
    STORE_RAPIDOCR_OCR_VERSION,
    STORE_READER_MODE,
    STORE_SESSION_ID,
    build_ocr_capture_profile_bucket_key,
    compute_ocr_window_aspect_ratio,
    json_copy,
    make_error,
    normalize_rapidocr_ocr_version,
    parse_ocr_capture_profile_bucket_key,
)
from ..ocr_reader import OcrReaderManager
from ..ocr_runtime_types import utc_now_iso
from ..plugin_capture_profile_helpers import (
    _capture_profile_bucket_entry_to_stage_map,
    _capture_profile_components_to_entry,
    _capture_profile_entry_to_stage_map,
    _capture_profile_entry_to_window_bucket_map,
    _is_ratio_profile_payload,
    _normalize_ocr_capture_profile_payload,
    _normalize_ocr_capture_profile_save_scope,
    _normalize_ocr_capture_profile_stage,
    _window_bucket_map_to_capture_profile_payload,
)
from ..plugin_constants import (
    _OCR_BACKEND_SELECTIONS,
    _OCR_CAPTURE_BACKEND_SELECTIONS,
)
from ..plugin_ocr_helpers import (
    _AFTER_ADVANCE_SCREEN_REFRESH_STAGES,
    _OCR_BRIDGE_DIAGNOSTIC_RUNTIME_KEYS,
    _after_advance_screen_refresh_needed,
    _apply_ocr_decision_diagnostics,
    _companion_after_advance_ocr_refresh_needed,
    _merge_ocr_runtime_preserving_bridge_diagnostics,
    _normalize_ocr_trigger_mode,
    _normalize_reader_mode,
    _ocr_emit_block_reason,
    _ocr_reader_allowed_block_reason,
    _ocr_tick_block_reason,
    _pending_data_source_for_reader_mode,
    _session_candidate_has_text,
)
from ..plugin_util_helpers import (
    _duration_percentile,
    _duration_summary,
    _log_plugin_noncritical,
    _migrate_legacy_capture_backend,
    _open_url_in_browser,
    _package_public_attr,
    _public_context_snapshot,
)
from ..reader import tail_events_jsonl, warmup_replay_events
from ..screen_awareness_training import (
    evaluate_screen_awareness_model,
    train_screen_awareness_model,
)
from ..screen_classifier import classify_screen_from_ocr, normalize_screen_type
from ..service import (
    apply_event_to_histories,
    apply_event_to_snapshot,
    apply_input_degraded_result,
    build_active_session_meta,
    build_config,
    build_explain_context,
    build_explain_degraded_result,
    build_history_payload,
    build_ocr_background_status,
    build_ocr_context_diagnostic,
    build_primary_diagnosis,
    build_snapshot_payload,
    build_status_payload,
    build_suggest_context,
    build_suggest_degraded_result,
    build_summarize_context,
    build_summarize_degraded_result,
    choose_candidate,
    clear_install_inspection_cache,
    derive_connection_state,
    filter_memory_reader_candidates,
    filter_ocr_reader_candidates,
    mode_allows_agent_actuation,
    next_poll_interval_for_state,
    rebuild_histories_from_events,
    scan_session_candidates,
)
from ..state import GalgameSharedState, build_initial_state
from ..store import GalgameStore
from ..textractor_support import install_textractor
from ..ui_api import build_open_ui_payload


def inspect_rapidocr_installation(**kwargs):
    kwargs.setdefault("plugin_id", "galgame_plugin")
    return _inspect_rapidocr_installation(**kwargs)


def _coerce_int_range(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _coerce_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    return default


# Auto-compute __all__ from the module's globals so that `from ._common import *`
# in mixin files exposes every imported name (including underscore-prefixed
# private helpers). Computing it here — instead of hand-maintaining a long list —
# prevents drift: any new import added above is automatically re-exported, and
# any removed import disappears from __all__ at the same time. The dunder filter
# keeps Python's own attributes (__name__, __doc__, __builtins__, ...) out of
# the star-import surface.
__all__ = [
    "_after_advance_screen_refresh_needed",
    "_AFTER_ADVANCE_SCREEN_REFRESH_STAGES",
    "_apply_ocr_decision_diagnostics",
    "_capture_profile_bucket_entry_to_stage_map",
    "_capture_profile_components_to_entry",
    "_capture_profile_entry_to_stage_map",
    "_capture_profile_entry_to_window_bucket_map",
    "_coerce_bool",
    "_coerce_int_range",
    "_companion_after_advance_ocr_refresh_needed",
    "_duration_percentile",
    "_duration_summary",
    "_inspect_rapidocr_installation",
    "_is_ratio_profile_payload",
    "_log_plugin_noncritical",
    "_merge_ocr_runtime_preserving_bridge_diagnostics",
    "_migrate_legacy_capture_backend",
    "_normalize_ocr_capture_profile_payload",
    "_normalize_ocr_capture_profile_save_scope",
    "_normalize_ocr_capture_profile_stage",
    "_normalize_ocr_trigger_mode",
    "_normalize_reader_mode",
    "_OCR_BACKEND_SELECTIONS",
    "_OCR_BRIDGE_DIAGNOSTIC_RUNTIME_KEYS",
    "_OCR_CAPTURE_BACKEND_SELECTIONS",
    "_ocr_emit_block_reason",
    "_ocr_reader_allowed_block_reason",
    "_ocr_tick_block_reason",
    "_open_url_in_browser",
    "_package_public_attr",
    "_pending_data_source_for_reader_mode",
    "_public_context_snapshot",
    "_session_candidate_has_text",
    "_window_bucket_map_to_capture_profile_payload",
    "ADVANCE_SPEED_MEDIUM",
    "ADVANCE_SPEEDS",
    "Any",
    "apply_event_to_histories",
    "apply_event_to_snapshot",
    "apply_input_degraded_result",
    "asyncio",
    "build_active_session_meta",
    "build_config",
    "build_explain_context",
    "build_explain_degraded_result",
    "build_history_payload",
    "build_initial_state",
    "build_ocr_background_status",
    "build_ocr_capture_profile_bucket_key",
    "build_ocr_context_diagnostic",
    "build_open_ui_payload",
    "build_primary_diagnosis",
    "build_snapshot_payload",
    "build_status_payload",
    "build_suggest_context",
    "build_suggest_degraded_result",
    "build_summarize_context",
    "build_summarize_degraded_result",
    "CharacterProfileManager",
    "choose_candidate",
    "classify_screen_from_ocr",
    "clear_install_inspection_cache",
    "compute_ocr_window_aspect_ratio",
    "DATA_SOURCE_BRIDGE_SDK",
    "DATA_SOURCE_MEMORY_READER",
    "DATA_SOURCE_NONE",
    "DATA_SOURCE_OCR_READER",
    "deque",
    "derive_connection_state",
    "Err",
    "evaluate_screen_awareness_model",
    "filter_memory_reader_candidates",
    "filter_ocr_reader_candidates",
    "Future",
    "GalgameSharedState",
    "GalgameStore",
    "GameLLMAgent",
    "HostAgentAdapter",
    "infer_inspection_failed_dependencies",
    "infer_missing_dependencies",
    "inspect_dxcam_installation",
    "inspect_rapidocr_installation",
    "install_textractor",
    "json_copy",
    "lifecycle",
    "LLMGateway",
    "make_error",
    "MemoryReaderManager",
    "mode_allows_agent_actuation",
    "MODE_CHOICE_ADVISOR",
    "MODE_COMPANION",
    "MODES",
    "neko_plugin",
    "NekoPluginBase",
    "next_poll_interval_for_state",
    "normalize_rapidocr_ocr_version",
    "normalize_screen_type",
    "OCR_CAPTURE_PROFILE_RATIO_KEYS",
    "OCR_CAPTURE_PROFILE_SAVE_SCOPE_PROCESS_FALLBACK",
    "OCR_CAPTURE_PROFILE_SAVE_SCOPE_WINDOW_BUCKET",
    "OCR_CAPTURE_PROFILE_SAVE_SCOPES",
    "OCR_CAPTURE_PROFILE_STAGE_CONFIG",
    "OCR_CAPTURE_PROFILE_STAGE_DEFAULT",
    "OCR_CAPTURE_PROFILE_STAGE_DIALOGUE",
    "OCR_CAPTURE_PROFILE_STAGE_GALLERY",
    "OCR_CAPTURE_PROFILE_STAGE_GAME_OVER",
    "OCR_CAPTURE_PROFILE_STAGE_MENU",
    "OCR_CAPTURE_PROFILE_STAGE_MINIGAME",
    "OCR_CAPTURE_PROFILE_STAGE_SAVE_LOAD",
    "OCR_CAPTURE_PROFILE_STAGE_TITLE",
    "OCR_CAPTURE_PROFILE_STAGE_TRANSITION",
    "OCR_CAPTURE_PROFILE_STAGES",
    "OCR_CAPTURE_PROFILE_WINDOW_BUCKETS_KEY",
    "OCR_TRIGGER_MODE_AFTER_ADVANCE",
    "OCR_TRIGGER_MODE_INTERVAL",
    "OCR_TRIGGER_MODES",
    "OcrReaderManager",
    "Ok",
    "os",
    "parse_ocr_capture_profile_bucket_key",
    "Path",
    "plugin_entry",
    "re",
    "READER_MODE_AUTO",
    "READER_MODE_MEMORY",
    "READER_MODE_OCR",
    "READER_MODES",
    "rebuild_histories_from_events",
    "scan_session_candidates",
    "SdkError",
    "SimpleNamespace",
    "STATE_ACTIVE",
    "STATE_ERROR",
    "STORE_ADVANCE_SPEED",
    "STORE_BOUND_GAME_ID",
    "STORE_CHARACTER_FIXED_NAME",
    "STORE_CHARACTER_MODE",
    "STORE_CHARACTER_PROFILE_VERSION",
    "STORE_CHARACTER_PROFILES",
    "STORE_CHARACTER_RUNTIME_STATE",
    "STORE_DEDUPE_WINDOW",
    "STORE_EVENTS_BYTE_OFFSET",
    "STORE_EVENTS_FILE_SIZE",
    "STORE_LAST_ERROR",
    "STORE_LAST_SEQ",
    "STORE_LLM_VISION_ENABLED",
    "STORE_LLM_VISION_MAX_IMAGE_PX",
    "STORE_MEMORY_READER_TARGET",
    "STORE_MODE",
    "STORE_OCR_BACKEND_SELECTION",
    "STORE_OCR_CAPTURE_BACKEND",
    "STORE_OCR_CAPTURE_PROFILES",
    "STORE_OCR_FAST_LOOP_ENABLED",
    "STORE_OCR_POLL_INTERVAL_SECONDS",
    "STORE_OCR_SCREEN_TEMPLATES",
    "STORE_OCR_TRIGGER_MODE",
    "STORE_OCR_WINDOW_TARGET",
    "STORE_PUSH_NOTIFICATIONS",
    "STORE_RAPIDOCR_AUTO_DETECT_LANG",
    "STORE_RAPIDOCR_AUTO_DETECT_LAST_LANG",
    "STORE_RAPIDOCR_LANG_TYPE",
    "STORE_RAPIDOCR_OCR_VERSION",
    "STORE_READER_MODE",
    "STORE_SESSION_ID",
    "subprocess",
    "sys",
    "tail_events_jsonl",
    "threading",
    "time",
    "timer_interval",
    "tr",
    "train_screen_awareness_model",
    "utc_now_iso",
    "warmup_replay_events",
]
