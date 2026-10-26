from __future__ import annotations

from .builder import (
    _CONDENSE_BLOCKING_PUNCTUATION_RE as _CONDENSE_BLOCKING_PUNCTUATION_RE,
)
from .builder import (
    _CONDENSE_SHORT_LINE_MAX_CHARS as _CONDENSE_SHORT_LINE_MAX_CHARS,
)
from .builder import (
    _DIALOGUE_PUNCTUATION_RE as _DIALOGUE_PUNCTUATION_RE,
)
from .builder import (
    _DIALOGUE_WEAK_PUNCTUATION_RE as _DIALOGUE_WEAK_PUNCTUATION_RE,
)
from .builder import (
    _DYNAMIC_WINDOW_DEFAULT_MAX_LINES as _DYNAMIC_WINDOW_DEFAULT_MAX_LINES,
)
from .builder import (
    _DYNAMIC_WINDOW_DEFAULT_MIN_LINES as _DYNAMIC_WINDOW_DEFAULT_MIN_LINES,
)
from .builder import (
    _DYNAMIC_WINDOW_DEFAULT_TARGET_TOKENS as _DYNAMIC_WINDOW_DEFAULT_TARGET_TOKENS,
)
from .builder import (
    _IMPORTANCE_EMOTIONAL_PUNCTUATION_RE as _IMPORTANCE_EMOTIONAL_PUNCTUATION_RE,
)
from .builder import (
    _IMPORTANCE_PLOT_WORDS as _IMPORTANCE_PLOT_WORDS,
)
from .builder import (
    _IMPORTANCE_TURN_WORDS as _IMPORTANCE_TURN_WORDS,
)
from .builder import (
    _NON_DIALOGUE_CONTEXT_TOKENS as _NON_DIALOGUE_CONTEXT_TOKENS,
)
from .builder import (
    _OCR_OVERLAY_TEXT_GUARD_SUBSTRINGS as _OCR_OVERLAY_TEXT_GUARD_SUBSTRINGS,
)
from .builder import (
    _SUMMARY_MAX_CHARS as _SUMMARY_MAX_CHARS,
)
from .builder import (
    _append_limited_with_importance as _append_limited_with_importance,
)
from .builder import (
    _append_unique_line as _append_unique_line,
)
from .builder import (
    _bounded_summary_text as _bounded_summary_text,
)
from .builder import (
    _build_input_degraded_context as _build_input_degraded_context,
)
from .builder import (
    _compact_lines_by_importance as _compact_lines_by_importance,
)
from .builder import (
    _compact_profile_text as _compact_profile_text,
)
from .builder import (
    _compute_dynamic_line_limit as _compute_dynamic_line_limit,
)
from .builder import (
    _condense_dialogue_batch as _condense_dialogue_batch,
)
from .builder import (
    _condense_run_key as _condense_run_key,
)
from .builder import (
    _context_snapshot_summary_seed as _context_snapshot_summary_seed,
)
from .builder import (
    _context_window_bounds as _context_window_bounds,
)
from .builder import (
    _cumulative_scene_summary as _cumulative_scene_summary,
)
from .builder import (
    _current_line_entry as _current_line_entry,
)
from .builder import (
    _dialogue_context_lines as _dialogue_context_lines,
)
from .builder import (
    _dialogue_line_dedupe_key as _dialogue_line_dedupe_key,
)
from .builder import (
    _ensure_target_line_present as _ensure_target_line_present,
)
from .builder import (
    _fixed_character_pov_context as _fixed_character_pov_context,
)
from .builder import (
    _global_scene_context_window as _global_scene_context_window,
)
from .builder import (
    _is_memory_reader_identifier as _is_memory_reader_identifier,
)
from .builder import (
    _is_ocr_reader_identifier as _is_ocr_reader_identifier,
)
from .builder import (
    _line_condense_blocked as _line_condense_blocked,
)
from .builder import (
    _line_importance_score as _line_importance_score,
)
from .builder import (
    _llm_refined_summary_from_state as _llm_refined_summary_from_state,
)
from .builder import (
    _looks_like_game_dialogue_context_line as _looks_like_game_dialogue_context_line,
)
from .builder import (
    _looks_like_ocr_overlay_text as _looks_like_ocr_overlay_text,
)
from .builder import (
    _matching_context_snapshot as _matching_context_snapshot,
)
from .builder import (
    _merge_condensed_run as _merge_condensed_run,
)
from .builder import (
    _previous_scene_id_from_state as _previous_scene_id_from_state,
)
from .builder import (
    _previous_summary_from_state as _previous_summary_from_state,
)
from .builder import (
    _profile_voice_traits as _profile_voice_traits,
)
from .builder import (
    _recency_ordered_context_lines as _recency_ordered_context_lines,
)
from .builder import (
    _resolve_target_line as _resolve_target_line,
)
from .builder import (
    _scene_context_hint as _scene_context_hint,
)
from .builder import (
    _scene_history_dialogue_line_count as _scene_history_dialogue_line_count,
)
from .builder import (
    _scene_lines as _scene_lines,
)
from .builder import (
    _scene_selected_choices as _scene_selected_choices,
)
from .builder import (
    _scene_summary_seed_with_restored_context as _scene_summary_seed_with_restored_context,
)
from .builder import (
    _significant_char_count as _significant_char_count,
)
from .builder import (
    _snapshot_for_stable_summary_seed as _snapshot_for_stable_summary_seed,
)
from .builder import (
    _strip_importance_score as _strip_importance_score,
)
from .builder import (
    _summary_mode as _summary_mode,
)
from .builder import (
    build_explain_context,
    build_fallback_summary,
    build_local_scene_summary,
    build_ocr_context_diagnostic,
    build_suggest_context,
    build_summarize_context,
    resolve_effective_current_line,
)

__all__ = [
    "build_explain_context",
    "build_fallback_summary",
    "build_local_scene_summary",
    "build_ocr_context_diagnostic",
    "build_suggest_context",
    "build_summarize_context",
    "resolve_effective_current_line",
]
