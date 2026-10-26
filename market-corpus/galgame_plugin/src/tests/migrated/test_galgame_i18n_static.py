from __future__ import annotations

import ast
import importlib
import json
import os
import re
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

logging_config = sys.modules.setdefault(
    "plugin.logging_config", ModuleType("plugin.logging_config")
)
logging_config.get_logger = lambda *_args, **_kwargs: SimpleNamespace(
    warning=lambda *_args, **_kwargs: None,
    info=lambda *_args, **_kwargs: None,
)

sdk_core = sys.modules.setdefault(
    "plugin.sdk.shared.core", ModuleType("plugin.sdk.shared.core")
)
sdk_core.__path__ = []
base_runtime = sys.modules.setdefault(
    "plugin.sdk.shared.core.base_runtime",
    ModuleType("plugin.sdk.shared.core.base_runtime"),
)
base_runtime.resolve_runtime_data_root = lambda: Path(
    os.environ.get("NEKO_STORAGE_SELECTED_ROOT", ".test-runtime")
)

_tutorial_migration = importlib.import_module(
    "market_plugins.galgame_plugin._tutorial_migration"
)

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
I18N_DIR = PLUGIN_ROOT / "i18n"
UI_I18N_DIR = I18N_DIR / "ui"
STATIC_ROOT = PLUGIN_ROOT / "static"

ENTRY_IDS = [
    "galgame_get_status",
    "galgame_install_textractor",
    "galgame_download_rapidocr_models",
    "galgame_set_rapidocr_lang",
    "galgame_install_dxcam",
    "galgame_get_snapshot",
    "galgame_get_history",
    "galgame_set_mode",
    "galgame_set_ocr_backend",
    "galgame_set_ocr_timing",
    "galgame_set_llm_vision",
    "galgame_set_ocr_screen_templates",
    "galgame_build_ocr_screen_template_draft",
    "galgame_validate_ocr_screen_templates",
    "galgame_get_ocr_screen_awareness_snapshot",
    "galgame_train_ocr_screen_awareness_model",
    "galgame_evaluate_ocr_screen_awareness_model",
    "galgame_bind_game",
    "galgame_set_ocr_capture_profile",
    "galgame_auto_recalibrate_ocr_dialogue_profile",
    "galgame_apply_recommended_ocr_capture_profile",
    "galgame_rollback_ocr_capture_profile",
    "galgame_list_memory_reader_processes",
    "galgame_set_memory_reader_target",
    "galgame_list_ocr_windows",
    "galgame_set_ocr_window_target",
    "galgame_open_ui",
    "galgame_explain_line",
    "galgame_summarize_scene",
    "galgame_suggest_choice",
    "galgame_agent_command",
    "galgame_continue_auto_advance",
    "galgame_get_character_profile",
    "galgame_set_character_mode",
    "galgame_get_character_list",
    "galgame_import_character_data",
    "galgame_get_scene_context",
    "galgame_get_story_so_far",
    "galgame_get_recent_lines",
    "galgame_get_push_history",
]
RUNTIME_KEYS = [
    "install.textractor.ok",
    "install.textractor.fail",
    "install.dxcam.ok",
    "install.dxcam.fail",
    "errors.not_configured",
    "errors.install_in_progress",
]
LOCALES = ["zh-CN", "zh-TW", "en", "ja", "ru", "ko", "es", "pt"]
UI_LOCALES = ["zh-CN", "zh-TW", "en", "ja", "ru", "ko"]


def _bundle(directory: Path, locale: str) -> dict[str, str]:
    return json.loads((directory / f"{locale}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("locale", LOCALES)
def test_i18n_all_locales_have_all_plugin_keys(locale: str) -> None:
    bundle = _bundle(I18N_DIR, locale)
    base = _bundle(I18N_DIR, "en")
    assert set(bundle) == set(base)
    for entry_id in ENTRY_IDS:
        assert bundle[f"entries.{entry_id}.name"]
        assert bundle[f"entries.{entry_id}.description"]
    for key in RUNTIME_KEYS:
        assert bundle[key]


def test_i18n_expected_entry_translations_are_present() -> None:
    assert _bundle(I18N_DIR, "zh-CN")["entries.galgame_get_status.name"] == (
        "获取 galgame 插件状态"
    )
    assert _bundle(I18N_DIR, "en")["entries.galgame_get_status.name"] == (
        "Get galgame plugin status"
    )


def test_i18n_entry_refs_supply_nonempty_default_fallbacks() -> None:
    refs: list[ast.Call] = []
    for source_path in (PLUGIN_ROOT / "plugin_entries").glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        refs.extend(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "tr"
        )
    assert refs
    for ref in refs:
        default = next(
            (keyword.value for keyword in ref.keywords if keyword.arg == "default"),
            None,
        )
        assert isinstance(default, ast.Constant) and isinstance(default.value, str)
        assert default.value.strip()


def test_zh_tw_plugin_locale_is_traditional_chinese() -> None:
    zh_cn = _bundle(I18N_DIR, "zh-CN")
    zh_tw = _bundle(I18N_DIR, "zh-TW")
    assert zh_tw != zh_cn
    assert zh_tw["plugin.name"] == "Galgame 遊玩助手"
    assert zh_tw["plugin.description"] == "讓貓娘陪伴你一起玩 Galgame"
    simplified = ["游玩", "让猫娘", "获取", "设置", "窗口", "进程", "识别", "截图"]
    assert not [
        (key, value)
        for key, value in zh_tw.items()
        if any(fragment in value for fragment in simplified)
    ]


def test_ui_locale_bundles_have_same_nonempty_keys() -> None:
    bundles = {locale: _bundle(UI_I18N_DIR, locale) for locale in UI_LOCALES}
    expected = set(bundles["zh-CN"])
    assert len(expected) >= 100
    for bundle in bundles.values():
        assert set(bundle) == expected
        assert all(isinstance(value, str) and value for value in bundle.values())


def test_ui_index_contains_galgame_dashboard_content() -> None:
    html = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    assert '<title data-i18n="ui.app.title">Galgame 游玩助手</title>' in html
    assert "让猫娘陪你一起玩 Galgame" in html
    for text in ["RapidOCR", "依赖安装", "DXcam", "Textractor", "OCR 截图校准"]:
        assert text in html
    for element_id in [
        "rapidocrCard",
        "dxcamCard",
        "textractorCard",
        "primaryDiagnosisPanel",
        "firstRunGuide",
        "currentLineOverview",
        "ocrPipelinePanel",
        "installCompactSummary",
    ]:
        assert f'id="{element_id}"' in html
    assert "./i18n.js?v=" in html


def test_ui_script_uses_runs_install_and_galgame_entries() -> None:
    script = (STATIC_ROOT / "main.js").read_text(encoding="utf-8")
    expected = [
        "const RUNS_URL = '/runs';",
        "const TEXTRACTOR_INSTALL_URL = `${UI_API_BASE}/textractor/install`;",
        "new EventSource(",
        "restoreTextractorInstallState",
        "galgame_get_status",
        "galgame_get_snapshot",
        "galgame_get_history",
        "galgame_agent_command",
        "galgame_set_ocr_capture_profile",
        "galgame_list_ocr_windows",
        "galgame_set_ocr_window_target",
        "renderPrimaryDiagnosis",
        "renderFirstRunGuide",
        "renderCurrentLineOverview",
        "renderOcrPipelinePanel",
        "renderInstallCompactSummary",
        "getInstallUIConfig",
    ]
    for fragment in expected:
        assert fragment in script
    assert "session.json" not in script
    assert "events.jsonl" not in script


def test_ui_script_rejects_stale_rapidocr_model_task_state() -> None:
    script = (STATIC_ROOT / "main.js").read_text(encoding="utf-8")
    for fragment in [
        "function canApplyRestoredInstallTaskState",
        "function shouldOfferRapidOcrModelsDownload",
        "generation: 0",
        "state.generation = Number(state.generation || 0) + 1;",
        "clearPersistedInstallTaskId(kind);",
        "function shouldRestoreRapidOcrModelsFailure",
        "ui.install.rapidocr.missing_models_manual_body",
        "clearPersistedInstallTaskId('rapidocr_models');",
    ]:
        assert fragment in script
    assert script.index("function canApplyRestoredInstallTaskState") < script.index(
        "applyInstallTaskState(kind, restoredState"
    )
    assert script.index("applyRapidOcrModelsGate(rapidocr);") < script.index(
        "const lastTask = installRuntime.rapidocr_models.state;"
    )


def test_rapidocr_language_buttons_use_full_i18n_keys() -> None:
    html = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    for suffix in ["ch", "japan", "korean", "en"]:
        assert f'data-i18n="ui.install.rapidocr.lang.{suffix}"' in html
        assert f"ui.install.rapidocr.lang.{suffix}_short" not in html


def test_tutorial_migration_copies_runtime_store_progress(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_store = runtime_root / "plugins" / "galgame_plugin" / "data" / "galgame_store.json"
    new_store = runtime_root / "server" / "plugin_install" / "tutorial_progress.json"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    runtime_store.parent.mkdir(parents=True)
    runtime_store.write_text(
        '{"tutorial_progress": {"completed": true, "last_step_index": 4}}',
        encoding="utf-8",
    )

    class _Store:
        def __init__(self, store_path: Path, _logger: object) -> None:
            self.store_path = store_path

        def load_tutorial_progress(self) -> dict[str, object] | None:
            return json.loads(self.store_path.read_text(encoding="utf-8")).get(
                "tutorial_progress"
            )

    monkeypatch.setattr(_tutorial_migration, "GalgameStore", _Store)
    _tutorial_migration.copy_legacy_tutorial_progress_if_missing(new_store)
    assert json.loads(new_store.read_text(encoding="utf-8")) == {
        "completed": True,
        "last_step_index": 4,
    }


def test_tutorial_migration_skips_unreadable_legacy_store(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    corrupt = tmp_path / "corrupt.json"
    valid = tmp_path / "valid.json"
    target = tmp_path / "tutorial.json"
    corrupt.write_text("not json", encoding="utf-8")
    valid.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(_tutorial_migration, "_legacy_store_paths", lambda: (corrupt, valid))

    class _Store:
        def __init__(self, store_path: Path, _logger: object) -> None:
            self.store_path = store_path

        def load_tutorial_progress(self) -> dict[str, object] | None:
            if self.store_path == corrupt:
                raise ValueError("corrupt")
            return {"completed": True, "last_step_index": 2}

    monkeypatch.setattr(_tutorial_migration, "GalgameStore", _Store)
    _tutorial_migration.copy_legacy_tutorial_progress_if_missing(target)
    assert json.loads(target.read_text(encoding="utf-8"))["last_step_index"] == 2


def test_ui_zh_tw_is_traditional_chinese() -> None:
    zh_cn = _bundle(UI_I18N_DIR, "zh-CN")
    zh_tw = _bundle(UI_I18N_DIR, "zh-TW")
    assert zh_tw != zh_cn
    assert zh_tw["ui.app.title"] == "Galgame 遊玩助手"
    assert zh_tw["ui.app.subtitle"] == "讓貓娘陪你一起玩 Galgame"


def test_ui_i18n_has_install_shell_and_dashboard_keys() -> None:
    bundle = _bundle(UI_I18N_DIR, "en")
    assert bundle["ui.app.title"] == "Galgame Play Assistant"
    assert bundle["ui.install.rapidocr.version_v5"] == "PP-OCRv5"
    assert "PP-OCRv4" in bundle["ui.install.rapidocr.v5_japan_note"]
    for key in [
        "ui.field.connection_state",
        "ui.field.ocr_reader_status",
        "ui.field.memory_reader_process",
        "ui.agent_status.paused_window_not_foreground",
        "ui.capture_profile.match_source.bucket_exact",
        "ui.action.select_ocr_window",
    ]:
        assert key in bundle


def test_ui_i18n_rapidocr_copy_is_not_half_deleted() -> None:
    forbidden = ["stable capture,.", "fell back to. Reason", "回退到了。原因", "优先 兜底"]
    for locale in UI_LOCALES:
        bundle = _bundle(UI_I18N_DIR, locale)
        for key in [
            "ui.install.ocr_desc",
            "ui.install.ocr_auto.title",
            "ui.install.rapidocr.fallback_body",
            "ui.install.rapidocr.ready_body",
        ]:
            assert not any(fragment in bundle[key] for fragment in forbidden)


def test_ui_script_prefers_query_locale_with_api_fallback() -> None:
    script = (STATIC_ROOT / "i18n.js").read_text(encoding="utf-8")
    for expected in [
        "new URLSearchParams(location.search).get('locale')",
        "const queryLocale = this._queryLocale();",
        "const storageLocale = this._storageLocale();",
        "localStorage.getItem('locale')",
        "/ui-api/locale",
        "/ui-api/i18n/ui/",
        "i18n-ready",
        "add('zh-CN');",
        "add('en');",
        "add('ja');",
        "add('ko');",
        "add('ru');",
    ]:
        assert expected in script


def test_ui_first_run_has_manual_rapidocr_cta_and_dxcam_gate() -> None:
    script = (STATIC_ROOT / "main.js").read_text(encoding="utf-8")
    assert "show_rapidocr_models_guide" in script
    assert "ui.first_run.action.show_rapidocr_models_guide" in script
    assert re.search(r"function\s+requiresDxcamBackend\s*\(", script)
    assert "dxcamRequired" in script
    assert "dxcam.installed" in script
    assert re.search(r"hasInstallFlow\s*\(\s*['\"]dxcam['\"]\s*\)", script)


def test_ui_has_rapidocr_version_toggle() -> None:
    html = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "main.js").read_text(encoding="utf-8")
    assert "rapidocrVersionBar" in html
    assert "rapidocrVersionV4Btn" in html
    assert "rapidocrVersionV5Btn" in html
    assert "renderRapidOcrVersionBar" in script
    assert re.search(
        r"setRapidOcrLang\s*\(\s*\{\s*ocr_version\s*:\s*version\s*\}\s*\)",
        script,
    )
