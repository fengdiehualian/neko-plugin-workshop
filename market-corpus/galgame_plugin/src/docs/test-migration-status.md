# Galgame test migration status

This is the release ledger for moving Galgame tests out of the N.E.K.O host
repository. The source inventory was taken from `plugin/tests` in the
`N.E.K.O-remove-builtin-audit` worktree on 2026-08-28.

The exact `test_galgame_*`, `_galgame_*`, integration, and fixture scope contains
49 files, 53,629 physical lines, and 1,219 static test functions. Related files
outside that naming scope include `test_character_profile.py` (28 functions),
the optional training suite (16), and host install/query contracts. Parametrized
pytest case counts can be higher. A green standalone run does **not** close this
migration by itself.

## Executable in this repository

The following host suites now have standalone equivalents under
`tests/migrated`. They run through `market_plugins.galgame_plugin` and do not
import the built-in namespace.

- `test_character_profile.py` and `_galgame_character_data.py`
- plugin-owned tests from `test_galgame_config_helpers.py`
- `test_galgame_cat_consult.py`
- `test_galgame_context_compression.py`
- `test_galgame_character_profile_autoload.py`
- `test_galgame_context_builder.py` and `test_galgame_context_metrics.py`
- `test_galgame_context_tokens.py`
- plugin-private assertions from `test_galgame_llm_backend.py`,
  `test_galgame_llm_gateway.py`, `test_galgame_llm_gateway_cache.py`,
  `test_galgame_llm_json_correction.py`, `test_galgame_llm_prompts.py`, and
  `test_galgame_repeat_detection.py`
- `test_galgame_local_input_actuator.py`
- `test_galgame_session_lifecycle.py`
- `test_galgame_state_snapshot.py`
- `test_galgame_vision_classifier.py`
- `test_galgame_pyautogui_availability.py` (standalone regression coverage for
  dependency/headless probing)
- `test_galgame_capture_platform.py`, `test_galgame_ocr_capture_safety.py`,
  `test_galgame_ocr_game_presets.py`, `test_galgame_ocr_reader.py`, and
  `test_galgame_vision_integration.py`
- `test_galgame_host_agent_adapter.py`, `test_galgame_memory_reader.py`,
  `test_galgame_service.py`, and `test_galgame_store_config.py`
- manual-load and rollback bridge fixtures plus standalone reader assertions
- `test_galgame_bridge.py`, `test_galgame_bridge_config.py`, and
  `test_galgame_bridge_entries.py` (split into scoped executable files without
  copying the host import-star aggregator)
- `test_galgame_bridge_memory_flow.py` and
  `integration/test_galgame_bridge_memory_reader.py`
- `test_galgame_bridge_ocr_flow.py`
- `integration/test_galgame_bridge_agent_entries.py`
- `test_galgame_scene_capsule.py` and plugin-owned assertions from
  `test_galgame_bridge_agent.py`; the real `utils.result_parser` assertion was
  split out as a host contract
- `test_galgame_i18n.py`, the 13 plugin-owned `test_galgame_ui_i18n.py`
  assertions, and the Galgame static-content assertions from the first five UI
  route tests; these read the independent package assets directly

The complete standalone suite provides **1,349 passing cases and 16 explicit
platform/training skips** under Python 3.12. The completed targeted batches
include 70 bridge-config cases, 21 bridge/entry remainder cases, 21 memory-flow
cases, 112 OCR-flow cases, three agent-entry integration cases, 117
scene-capsule cases, 176 bridge-agent cases, and 24 plugin UI/i18n cases.
`tests/test_standalone_layout.py` also guards market-only loading, the
absence of the built-in namespace, Unicode/space install paths, plugin-relative
model paths, install registration, and tutorial migration.

## Host contracts (do not copy wholesale)

- `integration/test_galgame_bridge_ui_routes.py`: FastAPI UI/install/SSE,
  authentication/path safety, and task-state behavior remain host-owned. Plugin
  static UI and locale content now have standalone equivalents.
- `test_galgame_rapidocr_support.py`: tests N.E.K.O `_shared/rapidocr`; keep and
  generalize it in the host.
- `test_install_task_state_path_rejects_path_traversal` from
  `test_galgame_config_helpers.py`: tests a host server route helper.
- `unit/server/test_plugin_query_status.py`: generic host query/i18n-card contract;
  replace Galgame with a neutral fixture.
- `unit/server/test_install_registry.py`: generic registration remains; built-in
  Galgame registration and reverse-import assertions must be removed or rewritten.
- `test_galgame_bridge_agent.py::test_scene_summary_ignores_legacy_memory_input`:
  only the cross-repository `utils.result_parser` parsing behavior is a host
  contract; the plugin-generated content assertion is executable here.
- real `NekoPluginBase`, SDK decorators, `Ok`/`Err`/`SdkError` identity and
  serialization, RapidOCR loading, and `portalocker` locking used by character
  autoload: these require the real host runtime rather than import-only stubs.
- SDK implementation details remain host contracts, but assertions about how
  Galgame calls `create_chat_llm_async`, SDK target entries, or handles Result
  payloads are plugin-owned and are executable here with minimal edge stubs.
- real OS window enumeration/capture, DXCam/MSS/PyAutoGUI/Electron connections,
  shared RapidOCR installation paths, and real host HTTP transport wiring.

## Plugin-owned migration blockers

The following suites remain release blockers for market v1.0.1. Stage one is not
complete until they are migrated, or individual assertions receive an approved
host-contract classification.

There are no remaining P behavior files in the audited inventory. The
`_galgame_agent_support.py`, `_galgame_bridge_support.py`,
`_galgame_install_support.py`, `_galgame_ocr_support.py`, and
`_galgame_test_support.py` files are source helper/aggregation inventories, not
additional test behavior; they are intentionally replaced by scoped helpers
rather than copied. Do not copy the host `plugin/tests/conftest.py`; it loads
unrelated server and runtime state.

### Optional training gate

- `unit/training/test_game_companion_training.py`

This is collected from the independent repository as 16 tests. In the normal
market environment it reports four dependency-free passes and 12 explicit
`torch` skips; a separate PyTorch/torchvision job must run all 16. Normal market
validation must not install PyTorch.

## Remaining non-P inventory

- **H (host):** FastAPI routes and transport, the seven host-owned tutorial/UI
  locale assertions, real SDK base/decorator/Result behavior, and the real
  `utils.result_parser` parse contract.
- **S (shared):** the seven shared RapidOCR path assertions plus the host
  `test_galgame_rapidocr_support.py` suite.
- **T (training):** 16 PyTorch/torchvision cases collected by the separate
  optional training job.
- **Removed/obsolete:** built-in Galgame bootstrap registration. It must not be
  recreated in either repository.

## Release gate

Before market v1.0.1, empty the blocker list and run from this repository root:

```bash
uv run pytest
uvx ruff==0.12.4 check --ignore-noqa --config ruff.toml .
```

Then run `neko-plugin check -r` from N.E.K.O, install under a Unicode path with
spaces, and prove every loaded Galgame module's `__file__` is below that user
plugin directory while `plugin.plugins.galgame_plugin` is absent from
`sys.modules`.
