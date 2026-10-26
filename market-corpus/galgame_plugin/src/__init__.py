"""Galgame plugin package entry point.

The plugin runtime resolves ``plugin.toml``'s entry directly from
``plugin_core``. Re-exporting ``GalgamePlugin`` (and its
``GalgameBridgePlugin`` alias) here keeps the public import surface unchanged
after the PR2 split.

Before the split, ``__init__.py`` was a 7,500-line monolith whose top-level
imports (``time``, ``json_copy``, ``build_summarize_context``,
``MemoryReaderManager``, ...) became attributes of the package object.
Several tests and external callers reach into that surface
(``monkeypatch.setattr(galgame_plugin_module, "build_summarize_context", ...)``,
``from plugin.plugins.galgame_plugin import GalgamePluginConfigService``),
so we star-import from ``plugin_core`` here to keep the original public
attribute surface intact. The explicit private re-exports below cover the
two underscore-prefixed helpers tests monkeypatch - star-import skips them.
``__all__`` stays narrow (only the three classes external code is meant to
depend on) so ``from plugin.plugins.galgame_plugin import *`` still yields a
curated surface.
"""
from __future__ import annotations

from .plugin_config_service import GalgamePluginConfigService
from .plugin_core import (
    ADVANCE_SPEED_MEDIUM as ADVANCE_SPEED_MEDIUM,
)
from .plugin_core import (
    ADVANCE_SPEEDS as ADVANCE_SPEEDS,
)
from .plugin_core import (
    DATA_SOURCE_BRIDGE_SDK as DATA_SOURCE_BRIDGE_SDK,
)
from .plugin_core import (
    DATA_SOURCE_MEMORY_READER as DATA_SOURCE_MEMORY_READER,
)
from .plugin_core import (
    DATA_SOURCE_NONE as DATA_SOURCE_NONE,
)
from .plugin_core import (
    DATA_SOURCE_OCR_READER as DATA_SOURCE_OCR_READER,
)
from .plugin_core import (
    MODE_CHOICE_ADVISOR as MODE_CHOICE_ADVISOR,
)
from .plugin_core import (
    MODE_COMPANION as MODE_COMPANION,
)
from .plugin_core import (
    MODES as MODES,
)
from .plugin_core import (
    OCR_CAPTURE_PROFILE_RATIO_KEYS as OCR_CAPTURE_PROFILE_RATIO_KEYS,
)
from .plugin_core import (
    OCR_CAPTURE_PROFILE_SAVE_SCOPE_PROCESS_FALLBACK as OCR_CAPTURE_PROFILE_SAVE_SCOPE_PROCESS_FALLBACK,
)
from .plugin_core import (
    OCR_CAPTURE_PROFILE_SAVE_SCOPE_WINDOW_BUCKET as OCR_CAPTURE_PROFILE_SAVE_SCOPE_WINDOW_BUCKET,
)
from .plugin_core import (
    OCR_CAPTURE_PROFILE_SAVE_SCOPES as OCR_CAPTURE_PROFILE_SAVE_SCOPES,
)
from .plugin_core import (
    OCR_CAPTURE_PROFILE_STAGE_CONFIG as OCR_CAPTURE_PROFILE_STAGE_CONFIG,
)
from .plugin_core import (
    OCR_CAPTURE_PROFILE_STAGE_DEFAULT as OCR_CAPTURE_PROFILE_STAGE_DEFAULT,
)
from .plugin_core import (
    OCR_CAPTURE_PROFILE_STAGE_DIALOGUE as OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
)
from .plugin_core import (
    OCR_CAPTURE_PROFILE_STAGE_GALLERY as OCR_CAPTURE_PROFILE_STAGE_GALLERY,
)
from .plugin_core import (
    OCR_CAPTURE_PROFILE_STAGE_GAME_OVER as OCR_CAPTURE_PROFILE_STAGE_GAME_OVER,
)
from .plugin_core import (
    OCR_CAPTURE_PROFILE_STAGE_MENU as OCR_CAPTURE_PROFILE_STAGE_MENU,
)
from .plugin_core import (
    OCR_CAPTURE_PROFILE_STAGE_MINIGAME as OCR_CAPTURE_PROFILE_STAGE_MINIGAME,
)
from .plugin_core import (
    OCR_CAPTURE_PROFILE_STAGE_SAVE_LOAD as OCR_CAPTURE_PROFILE_STAGE_SAVE_LOAD,
)
from .plugin_core import (
    OCR_CAPTURE_PROFILE_STAGE_TITLE as OCR_CAPTURE_PROFILE_STAGE_TITLE,
)
from .plugin_core import (
    OCR_CAPTURE_PROFILE_STAGE_TRANSITION as OCR_CAPTURE_PROFILE_STAGE_TRANSITION,
)
from .plugin_core import (
    OCR_CAPTURE_PROFILE_STAGES as OCR_CAPTURE_PROFILE_STAGES,
)
from .plugin_core import (
    OCR_CAPTURE_PROFILE_WINDOW_BUCKETS_KEY as OCR_CAPTURE_PROFILE_WINDOW_BUCKETS_KEY,
)
from .plugin_core import (
    OCR_TRIGGER_MODE_AFTER_ADVANCE as OCR_TRIGGER_MODE_AFTER_ADVANCE,
)
from .plugin_core import (
    OCR_TRIGGER_MODE_INTERVAL as OCR_TRIGGER_MODE_INTERVAL,
)
from .plugin_core import (
    OCR_TRIGGER_MODES as OCR_TRIGGER_MODES,
)
from .plugin_core import (
    READER_MODE_AUTO as READER_MODE_AUTO,
)
from .plugin_core import (
    READER_MODE_MEMORY as READER_MODE_MEMORY,
)
from .plugin_core import (
    READER_MODE_OCR as READER_MODE_OCR,
)
from .plugin_core import (
    READER_MODES as READER_MODES,
)
from .plugin_core import (
    SESSION_ORIGIN_PREEXISTING as SESSION_ORIGIN_PREEXISTING,
)
from .plugin_core import (
    STATE_ACTIVE as STATE_ACTIVE,
)
from .plugin_core import (
    STATE_ERROR as STATE_ERROR,
)
from .plugin_core import (
    STORE_ADVANCE_SPEED as STORE_ADVANCE_SPEED,
)
from .plugin_core import (
    STORE_BOUND_GAME_ID as STORE_BOUND_GAME_ID,
)
from .plugin_core import (
    STORE_CHARACTER_FIXED_NAME as STORE_CHARACTER_FIXED_NAME,
)
from .plugin_core import (
    STORE_CHARACTER_MODE as STORE_CHARACTER_MODE,
)
from .plugin_core import (
    STORE_CHARACTER_PROFILE_VERSION as STORE_CHARACTER_PROFILE_VERSION,
)
from .plugin_core import (
    STORE_CHARACTER_PROFILES as STORE_CHARACTER_PROFILES,
)
from .plugin_core import (
    STORE_CHARACTER_RUNTIME_STATE as STORE_CHARACTER_RUNTIME_STATE,
)
from .plugin_core import (
    STORE_EVENTS_BYTE_OFFSET as STORE_EVENTS_BYTE_OFFSET,
)
from .plugin_core import (
    STORE_EVENTS_FILE_SIZE as STORE_EVENTS_FILE_SIZE,
)
from .plugin_core import (
    STORE_LAST_ERROR as STORE_LAST_ERROR,
)
from .plugin_core import (
    STORE_LAST_SEQ as STORE_LAST_SEQ,
)
from .plugin_core import (
    STORE_LLM_VISION_ENABLED as STORE_LLM_VISION_ENABLED,
)
from .plugin_core import (
    STORE_LLM_VISION_MAX_IMAGE_PX as STORE_LLM_VISION_MAX_IMAGE_PX,
)
from .plugin_core import (
    STORE_MEMORY_READER_TARGET as STORE_MEMORY_READER_TARGET,
)
from .plugin_core import (
    STORE_MODE as STORE_MODE,
)
from .plugin_core import (
    STORE_OCR_BACKEND_SELECTION as STORE_OCR_BACKEND_SELECTION,
)
from .plugin_core import (
    STORE_OCR_CAPTURE_BACKEND as STORE_OCR_CAPTURE_BACKEND,
)
from .plugin_core import (
    STORE_OCR_CAPTURE_PROFILES as STORE_OCR_CAPTURE_PROFILES,
)
from .plugin_core import (
    STORE_OCR_FAST_LOOP_ENABLED as STORE_OCR_FAST_LOOP_ENABLED,
)
from .plugin_core import (
    STORE_OCR_POLL_INTERVAL_SECONDS as STORE_OCR_POLL_INTERVAL_SECONDS,
)
from .plugin_core import (
    STORE_OCR_SCREEN_TEMPLATES as STORE_OCR_SCREEN_TEMPLATES,
)
from .plugin_core import (
    STORE_OCR_TRIGGER_MODE as STORE_OCR_TRIGGER_MODE,
)
from .plugin_core import (
    STORE_OCR_WINDOW_TARGET as STORE_OCR_WINDOW_TARGET,
)
from .plugin_core import (
    STORE_PUSH_NOTIFICATIONS as STORE_PUSH_NOTIFICATIONS,
)
from .plugin_core import (
    STORE_RAPIDOCR_AUTO_DETECT_LANG as STORE_RAPIDOCR_AUTO_DETECT_LANG,
)
from .plugin_core import (
    STORE_RAPIDOCR_AUTO_DETECT_LAST_LANG as STORE_RAPIDOCR_AUTO_DETECT_LAST_LANG,
)
from .plugin_core import (
    STORE_RAPIDOCR_LANG_TYPE as STORE_RAPIDOCR_LANG_TYPE,
)
from .plugin_core import (
    STORE_RAPIDOCR_OCR_VERSION as STORE_RAPIDOCR_OCR_VERSION,
)
from .plugin_core import (
    STORE_READER_MODE as STORE_READER_MODE,
)
from .plugin_core import (
    STORE_SESSION_ID as STORE_SESSION_ID,
)
from .plugin_core import (
    Any as Any,
)
from .plugin_core import (
    CharacterProfileManager as CharacterProfileManager,
)
from .plugin_core import (
    Err as Err,
)
from .plugin_core import (
    Future as Future,
)
from .plugin_core import (
    GalgameBridgePlugin as GalgameBridgePlugin,
)
from .plugin_core import (
    GalgamePlugin as GalgamePlugin,
)
from .plugin_core import (
    GalgameSharedState as GalgameSharedState,
)
from .plugin_core import (
    GalgameStore as GalgameStore,
)
from .plugin_core import (
    GameLLMAgent as GameLLMAgent,
)
from .plugin_core import (
    HostAgentAdapter as HostAgentAdapter,
)
from .plugin_core import (
    LLMGateway as LLMGateway,
)
from .plugin_core import (
    MemoryReaderManager as MemoryReaderManager,
)
from .plugin_core import (
    NekoPluginBase as NekoPluginBase,
)
from .plugin_core import (
    OcrReaderManager as OcrReaderManager,
)
from .plugin_core import (
    Ok as Ok,
)
from .plugin_core import (
    SdkError as SdkError,
)
from .plugin_core import (
    SimpleNamespace as SimpleNamespace,
)
from .plugin_core import (  # explicit: star-import skips underscore names
    _after_advance_screen_refresh_needed as _after_advance_screen_refresh_needed,
)
from .plugin_core import (
    _open_url_in_browser as _open_url_in_browser,
)
from .plugin_core import (
    apply_event_to_histories as apply_event_to_histories,
)
from .plugin_core import (
    apply_event_to_snapshot as apply_event_to_snapshot,
)
from .plugin_core import (
    apply_input_degraded_result as apply_input_degraded_result,
)
from .plugin_core import (
    asyncio as asyncio,
)
from .plugin_core import (
    build_active_session_meta as build_active_session_meta,
)
from .plugin_core import (
    build_config as build_config,
)
from .plugin_core import (
    build_explain_context as build_explain_context,
)
from .plugin_core import (
    build_explain_degraded_result as build_explain_degraded_result,
)
from .plugin_core import (
    build_history_payload as build_history_payload,
)
from .plugin_core import (
    build_initial_state as build_initial_state,
)
from .plugin_core import (
    build_ocr_background_status as build_ocr_background_status,
)
from .plugin_core import (
    build_ocr_capture_profile_bucket_key as build_ocr_capture_profile_bucket_key,
)
from .plugin_core import (
    build_ocr_context_diagnostic as build_ocr_context_diagnostic,
)
from .plugin_core import (
    build_open_ui_payload as build_open_ui_payload,
)
from .plugin_core import (
    build_primary_diagnosis as build_primary_diagnosis,
)
from .plugin_core import (
    build_snapshot_payload as build_snapshot_payload,
)
from .plugin_core import (
    build_status_payload as build_status_payload,
)
from .plugin_core import (
    build_suggest_context as build_suggest_context,
)
from .plugin_core import (
    build_suggest_degraded_result as build_suggest_degraded_result,
)
from .plugin_core import (
    build_summarize_context as build_summarize_context,
)
from .plugin_core import (
    build_summarize_degraded_result as build_summarize_degraded_result,
)
from .plugin_core import (
    choose_candidate as choose_candidate,
)
from .plugin_core import (
    classify_screen_from_ocr as classify_screen_from_ocr,
)
from .plugin_core import (
    classify_session_origin as classify_session_origin,
)
from .plugin_core import (
    clear_install_inspection_cache as clear_install_inspection_cache,
)
from .plugin_core import (
    compute_ocr_window_aspect_ratio as compute_ocr_window_aspect_ratio,
)
from .plugin_core import (
    deque as deque,
)
from .plugin_core import (
    derive_connection_state as derive_connection_state,
)
from .plugin_core import (
    evaluate_screen_awareness_model as evaluate_screen_awareness_model,
)
from .plugin_core import (
    event_releases_empty_snapshot_gate as event_releases_empty_snapshot_gate,
)
from .plugin_core import (
    filter_memory_reader_candidates as filter_memory_reader_candidates,
)
from .plugin_core import (
    filter_ocr_reader_candidates as filter_ocr_reader_candidates,
)
from .plugin_core import (
    infer_inspection_failed_dependencies as infer_inspection_failed_dependencies,
)
from .plugin_core import (
    infer_missing_dependencies as infer_missing_dependencies,
)
from .plugin_core import (
    inspect_dxcam_installation as inspect_dxcam_installation,
)
from .plugin_core import (
    inspect_rapidocr_installation as inspect_rapidocr_installation,
)
from .plugin_core import (
    install_textractor as install_textractor,
)
from .plugin_core import (
    json_copy as json_copy,
)
from .plugin_core import (
    lifecycle as lifecycle,
)
from .plugin_core import (
    make_error as make_error,
)
from .plugin_core import (
    mode_allows_agent_actuation as mode_allows_agent_actuation,
)
from .plugin_core import (
    neko_plugin as neko_plugin,
)
from .plugin_core import (
    next_poll_interval_for_state as next_poll_interval_for_state,
)
from .plugin_core import (
    normalize_screen_type as normalize_screen_type,
)
from .plugin_core import (
    os as os,
)
from .plugin_core import (
    parse_ocr_capture_profile_bucket_key as parse_ocr_capture_profile_bucket_key,
)
from .plugin_core import (
    plugin_entry as plugin_entry,
)
from .plugin_core import (
    re as re,
)
from .plugin_core import (
    read_stream_checkpoint as read_stream_checkpoint,
)
from .plugin_core import (
    rebuild_histories_from_events as rebuild_histories_from_events,
)
from .plugin_core import (
    scan_session_candidates as scan_session_candidates,
)
from .plugin_core import (
    session_identity_key as session_identity_key,
)
from .plugin_core import (
    snapshot_events_boundary as snapshot_events_boundary,
)
from .plugin_core import (
    subprocess as subprocess,
)
from .plugin_core import (
    sys as sys,
)
from .plugin_core import (
    tail_events_jsonl as tail_events_jsonl,
)
from .plugin_core import (
    threading as threading,
)
from .plugin_core import (
    time as time,
)
from .plugin_core import (
    timer_interval as timer_interval,
)
from .plugin_core import (
    tr as tr,
)
from .plugin_core import (
    train_screen_awareness_model as train_screen_awareness_model,
)
from .plugin_core import (
    utc_now_iso as utc_now_iso,
)
from .plugin_core import (
    warmup_replay_events as warmup_replay_events,
)

__all__ = ["GalgameBridgePlugin", "GalgamePlugin", "GalgamePluginConfigService"]
