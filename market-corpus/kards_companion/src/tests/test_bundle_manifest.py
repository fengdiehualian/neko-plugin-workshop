"""Focused tests for the opt-in distribution bundle manifest.

Every test builds a fake bundle under tmp_path only.  These tests never touch
an installation, download assets, add real vendor payloads, or assert a real
license.  Python 3.11+.
"""

import importlib.util
import json
import sys
import tomllib
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "scripts"


def _load(name):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bundle = _load("bundle_manifest")
sync = _load("sync_installations")

PLUGIN_TOML = b"""\
[plugin]
id = "kards_companion"
name = "KARDS test"
version = "6.0.0"
entry = "plugin.plugins.kards_companion:KardsCompanionPlugin"
"""

COMPONENTS = {"vendor/numpy": {"version": "0.0-test-only",
                               "license": "LicenseRef-Test-Only"}}


def _write(root, rel, data):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    return path


def fake_bundle(root, *, models=False):
    _write(root, "plugin.toml", PLUGIN_TOML)
    _write(root, "worker/entry.py", "print('worker')\n")
    _write(root, "worker/lib/util.py", "VALUE = 1\n")
    _write(root, "vendor/numpy/__init__.py", "VERSION = 'fake'\n")
    _write(root, "assets/manifest.json", '{"schema": "test", "assets": []}\n')
    _write(root, "assets/cards/0001.png", b"\x89PNG\r\n\x1a\nfake")
    _write(root, "THIRD_PARTY_NOTICES.md", "fake notices for tests only\n")
    if models:
        _write(root, "models/ui_det.onnx", b"fake-ui-det")
        _write(root, "models/card_cls.onnx", b"fake-card-cls")
    return root


def make_manifest(root, **kwargs):
    return bundle.generate_manifest(root, COMPONENTS, generated_at="2026-01-01T00:00:00+00:00",
                                    **kwargs)


def test_generate_and_verify_complete_bundle(tmp_path):
    root = fake_bundle(tmp_path / "bundle")
    manifest = make_manifest(root)
    assert manifest["schema"] == bundle.SCHEMA
    paths = [item["path"] for item in manifest["files"]]
    assert paths == sorted(paths)
    by_path = {item["path"]: item for item in manifest["files"]}
    assert by_path["worker/entry.py"]["component"] == "worker"
    assert by_path["vendor/numpy/__init__.py"]["component"] == "vendor/numpy"
    assert by_path["vendor/numpy/__init__.py"]["version"] == "0.0-test-only"
    assert by_path["vendor/numpy/__init__.py"]["license"] == "LicenseRef-Test-Only"
    assert by_path["THIRD_PARTY_NOTICES.md"]["component"] == "notices"
    assert manifest["required"]["python_worker"]["present"] is True
    assert manifest["required"]["assets_manifest"]["present"] is True
    assert manifest["required"]["third_party_notices"]["present"] is True
    assert "ocr_runtime" not in manifest["required"]
    assert manifest["optional"]["kards_ui_det"]["state"] == "missing"
    assert manifest["optional"]["kards_card_cls"]["state"] == "missing"
    assert manifest["capabilities"]["bundle_ready"] is True
    assert manifest["capabilities"]["kards_ui_det"] == "missing"
    assert manifest["capabilities"]["kards_card_cls"] == "missing"
    assert manifest["integrity"]["file_count"] == len(manifest["files"])
    report = bundle.verify_manifest(root, manifest)
    assert report["ok"] is True, report["errors"]
    assert report["capabilities"]["bundle_ready"] is True
    assert "optional_missing:kards_ui_det" in report["warnings"]
    assert "optional_missing:kards_card_cls" in report["warnings"]
    assert "license_unverified:worker" in report["warnings"]


def test_verify_detects_hash_mismatch(tmp_path):
    root = fake_bundle(tmp_path / "bundle")
    manifest = make_manifest(root)
    _write(root, "worker/lib/util.py", "VALUE = 2\n")  # same size, different bytes
    report = bundle.verify_manifest(root, manifest)
    assert report["ok"] is False
    assert "sha256_mismatch:worker/lib/util.py" in report["errors"]


def test_verify_detects_missing_and_unlisted_files(tmp_path):
    root = fake_bundle(tmp_path / "bundle")
    manifest = make_manifest(root)
    (root / "worker/lib/util.py").unlink()
    report = bundle.verify_manifest(root, manifest)
    assert "file_missing:worker/lib/util.py" in report["errors"]
    _write(root, "worker/extra.py", "EXTRA = 1\n")
    report = bundle.verify_manifest(root, manifest)
    assert "unlisted_file:worker/extra.py" in report["errors"]


def test_optional_models_never_block_bundle(tmp_path):
    root = fake_bundle(tmp_path / "bundle")
    manifest = make_manifest(root)
    assert manifest["optional"]["kards_ui_det"]["state"] == "missing"
    assert manifest["optional"]["kards_card_cls"]["state"] == "missing"

    _write(root, "models/ui_det.onnx", b"fake-ui-det")
    _write(root, "models/card_cls.onnx", b"fake-card-cls")
    report = bundle.verify_manifest(root, manifest)
    assert report["ok"] is True, report["errors"]
    assert report["optional"]["kards_ui_det"]["state"] == "present"
    assert "optional_unlisted:models/ui_det.onnx" in report["warnings"]

    manifest_with_models = make_manifest(root)
    assert manifest_with_models["optional"]["kards_ui_det"]["state"] == "present"
    assert manifest_with_models["optional"]["kards_card_cls"]["state"] == "present"
    (root / "models/ui_det.onnx").unlink()
    (root / "models/card_cls.onnx").unlink()
    report = bundle.verify_manifest(root, manifest_with_models)
    assert report["ok"] is True, report["errors"]
    assert report["optional"]["kards_ui_det"]["state"] == "missing"
    assert "optional_missing:kards_ui_det" in report["warnings"]
    assert report["capabilities"]["bundle_ready"] is True
    assert "ocr_bundle_ready" not in report["capabilities"]


@pytest.mark.parametrize("rel,name", [
    ("worker/entry.py", "python_worker"),
    ("assets/manifest.json", "assets_manifest"),
    ("THIRD_PARTY_NOTICES.md", "third_party_notices"),
])
def test_required_marker_missing_is_fail_closed(tmp_path, rel, name):
    root = fake_bundle(tmp_path / "bundle")
    manifest = make_manifest(root)
    (root / rel).unlink()
    scan = bundle.scan_bundle(root)
    assert any(reason.startswith(f"required_missing:{name}:") for reason in scan.errors)
    assert scan.ok is False
    with pytest.raises(bundle.BundleError):
        make_manifest(root)
    report = bundle.verify_manifest(root, manifest)
    assert report["ok"] is False
    assert any(reason.startswith(f"required_missing:{name}:") or
               reason == f"file_missing:{rel}" for reason in report["errors"])


def test_worker_bootstrap_marker_satisfies_python_worker(tmp_path):
    root = fake_bundle(tmp_path / "bundle")
    (root / "worker/entry.py").unlink()
    _write(root, "worker/bootstrap.py", "print('bootstrap')\n")
    manifest = make_manifest(root)
    assert manifest["required"]["python_worker"]["path"] == "worker/bootstrap.py"
    assert manifest["required"]["python_worker"]["present"] is True
    report = bundle.verify_manifest(root, manifest)
    assert report["ok"] is True, report["errors"]


def test_assets_cards_manifest_satisfies_assets_marker(tmp_path):
    root = fake_bundle(tmp_path / "bundle")
    (root / "assets/manifest.json").unlink()
    _write(root, "assets/cards/manifest.json", '{"schema": "kards.card-assets", "cards": []}\n')
    manifest = make_manifest(root)
    assert manifest["required"]["assets_manifest"]["path"] == "assets/cards/manifest.json"
    assert manifest["required"]["assets_manifest"]["present"] is True
    report = bundle.verify_manifest(root, manifest)
    assert report["ok"] is True, report["errors"]


def test_ocr_runtime_marker_is_no_longer_required(tmp_path):
    # OCR 运行时标记已随 OCR 链路删除：不存在 ocr_models 时 bundle 仍 ok。
    root = fake_bundle(tmp_path / "bundle")
    manifest = make_manifest(root)
    assert "ocr_runtime" not in manifest["required"]
    assert not (root / "ocr_models").exists()
    report = bundle.verify_manifest(root, manifest)
    assert report["ok"] is True, report["errors"]


def test_required_marker_must_not_be_empty(tmp_path):
    root = fake_bundle(tmp_path / "bundle")
    (root / "worker/entry.py").write_bytes(b"")
    scan = bundle.scan_bundle(root)
    assert "required_empty:python_worker:worker/entry.py" in scan.errors
    with pytest.raises(bundle.BundleError):
        make_manifest(root)


@pytest.mark.parametrize("rel,name", [
    ("assets/manifest.json", "assets_manifest"),
])
@pytest.mark.parametrize("payload", [b"not json", b"[]"])
def test_required_json_marker_must_be_object(tmp_path, rel, name, payload):
    root = fake_bundle(tmp_path / "bundle")
    (root / rel).write_bytes(payload)
    scan = bundle.scan_bundle(root)
    assert f"required_invalid_json:{name}:{rel}" in scan.errors
    with pytest.raises(bundle.BundleError):
        make_manifest(root)


@pytest.mark.parametrize("rel", [
    "worker/training/sample.bin",
    "worker/dataset/sample.bin",
    "worker/Videos/sample.bin",
    "worker/recording/sample.bin",
    "worker/data/sample.bin",
    "worker/config/sample.json",
    "worker/.pytest_cache/sample.bin",
    "worker/site-packages/sample.py",
    "vendor/site-packages/sample.py",
    "vendor/whl/sample.whl",
    "vendor/keys/sample.pem",
    "vendor/python/Lib/site-packages/pkg/keys/sample.pem",
    "vendor/python/Lib/site-packages/pkg/training/sample.bin",
])
def test_forbidden_bundle_content_blocks_generation(tmp_path, rel):
    root = fake_bundle(tmp_path / "bundle")
    _write(root, rel, b"x")
    scan = bundle.scan_bundle(root)
    assert any("bundle_forbidden_path" in reason for reason in scan.errors), scan.errors
    with pytest.raises(bundle.BundleError):
        make_manifest(root)


def test_vendor_python_site_packages_layout_is_allowed(tmp_path):
    root = fake_bundle(tmp_path / "bundle")
    files = {
        "vendor/python/python.exe": b"fake-python",
        "vendor/python/python311.dll": b"fake-dll",
        "vendor/python/Lib/site-packages/numpy/__init__.py": b"VERSION = 'fake'\n",
        "vendor/python/Lib/site-packages/cv2/data/cascade.xml": b"<cascade/>",
        "vendor/python/Lib/site-packages/cv2/config/settings.json": b"{}\n",
    }
    for rel, data in files.items():
        _write(root, rel, data)
    manifest = make_manifest(root)
    paths = {item["path"] for item in manifest["files"]}
    assert set(files) <= paths
    report = bundle.verify_manifest(root, manifest)
    assert report["ok"] is True, report["errors"]


def test_generated_manifest_passes_worker_distribution_validator(tmp_path):
    root = fake_bundle(tmp_path / "bundle")
    for rel, data in {
        "vendor/python/python.exe": b"fake-python",
        "vendor/python/python311.dll": b"fake-dll",
        "vendor/python/Lib/site-packages/numpy/__init__.py": b"VERSION = 'fake'\n",
        "vendor/python/Lib/site-packages/cv2/data/cascade.xml": b"<cascade/>",
    }.items():
        _write(root, rel, data)
    manifest = make_manifest(root)
    bundle.write_manifest(root / "bundle-manifest.json", manifest)
    from worker import package_check  # unified reader, read-only integration check

    data = json.loads((root / "bundle-manifest.json").read_text(encoding="utf-8"))
    view, issues = package_check._validate_distribution_manifest(data, root)
    assert issues == [], issues
    blockers, _warnings = [], []
    package_check._check_distribution_files(view, blockers, _warnings)
    package_check._check_distribution_required_files(view, blockers)
    assert blockers == [], blockers


def test_generated_caches_are_skipped_but_manifest_entries_are_rejected(tmp_path):
    root = fake_bundle(tmp_path / "bundle")
    _write(root, "worker/__pycache__/sample.pyc", b"cache")
    _write(root, "worker/loose.pyc", b"cache")
    _write(root, "vendor/python/Lib/site-packages/pkg/__pycache__/x.pyc", b"cache")
    manifest = make_manifest(root)
    paths = {item["path"] for item in manifest["files"]}
    assert not any(path.endswith(".pyc") or "/__pycache__/" in path for path in paths)
    report = bundle.verify_manifest(root, manifest)
    assert report["ok"] is True, report["errors"]
    bad = json.loads(json.dumps(manifest))
    bad["files"][0]["path"] = "worker/__pycache__/sample.pyc"
    report = bundle.verify_manifest(root, bad)
    assert report["ok"] is False
    assert any("forbidden" in reason for reason in report["errors"]), report["errors"]


def test_missing_vendor_runtime_is_warning_only(tmp_path):
    root = fake_bundle(tmp_path / "bundle")
    manifest = make_manifest(root)
    assert "vendor_python_runtime_missing:vendor/python/python.exe" in manifest["warnings"]
    assert "vendor_site_packages_missing:vendor/python/Lib/site-packages" in manifest["warnings"]
    report = bundle.verify_manifest(root, manifest)
    assert report["ok"] is True, report["errors"]
    assert "vendor_python_runtime_missing:vendor/python/python.exe" in report["warnings"]


@pytest.mark.parametrize("rel", [
    "../worker/evil.py",
    "/worker/evil.py",
    "worker\\evil.py",
    "C:/worker/evil.py",
    "worker/evil:stream.py",
    "worker/../evil.py",
    "./worker/evil.py",
    "worker//evil.py",
])
def test_manifest_rejects_unsafe_paths(tmp_path, rel):
    root = fake_bundle(tmp_path / "bundle")
    manifest = make_manifest(root)
    bad = json.loads(json.dumps(manifest))
    bad["files"][0]["path"] = rel
    report = bundle.verify_manifest(root, bad)
    assert report["ok"] is False
    assert any("unsafe" in reason or "not_allowed" in reason or "forbidden" in reason
               or "too_long" in reason for reason in report["errors"]), report["errors"]


def test_manifest_rejects_overlong_path(tmp_path):
    root = fake_bundle(tmp_path / "bundle")
    manifest = make_manifest(root)
    bad = json.loads(json.dumps(manifest))
    bad["files"][0]["path"] = "worker/" + "a" * 1024
    report = bundle.verify_manifest(root, bad)
    assert report["ok"] is False
    assert any("too_long" in reason for reason in report["errors"]), report["errors"]


def test_manifest_entry_validation_and_fingerprint(tmp_path):
    root = fake_bundle(tmp_path / "bundle")
    manifest = make_manifest(root)

    bad = json.loads(json.dumps(manifest))
    bad["files"][0]["size"] = -1
    report = bundle.verify_manifest(root, bad)
    assert any(reason.startswith("manifest_entry_invalid_size:") for reason in report["errors"])

    bad = json.loads(json.dumps(manifest))
    bad["files"][0]["sha256"] = "not-a-hash"
    report = bundle.verify_manifest(root, bad)
    assert any(reason.startswith("manifest_entry_invalid_sha256:") for reason in report["errors"])

    bad = json.loads(json.dumps(manifest))
    bad["files"][0]["license"] = ""
    report = bundle.verify_manifest(root, bad)
    assert any(reason.startswith("manifest_entry_invalid_license:") for reason in report["errors"])

    bad = json.loads(json.dumps(manifest))
    bad["files"].append(dict(bad["files"][0]))
    report = bundle.verify_manifest(root, bad)
    assert any(reason.startswith("manifest_duplicate_path:") for reason in report["errors"])

    bad = json.loads(json.dumps(manifest))
    bad["integrity"]["sha256"] = "0" * 64
    report = bundle.verify_manifest(root, bad)
    assert "manifest_fingerprint_mismatch" in report["errors"]


@pytest.mark.parametrize("components", [
    {"../worker": {"version": "1", "license": "X"}},
    {"worker/../assets": {"version": "1", "license": "X"}},
    {"README.md": {"version": "1", "license": "X"}},
    {"worker": {"version": "", "license": "X"}},
    {"worker": {"version": "1", "license": ""}},
])
def test_component_metadata_rejects_unsafe_or_empty_values(tmp_path, components):
    root = fake_bundle(tmp_path / "bundle")
    with pytest.raises(bundle.BundleError):
        bundle.generate_manifest(root, components)


def test_symlink_in_bundle_is_rejected(tmp_path):
    root = fake_bundle(tmp_path / "bundle")
    link = root / "worker/link.py"
    try:
        link.symlink_to(root / "worker/entry.py")
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable on this host")
    scan = bundle.scan_bundle(root)
    assert "bundle_reparse:worker/link.py" in scan.errors
    with pytest.raises(bundle.BundleError):
        make_manifest(root)


def test_symlinked_root_is_rejected(tmp_path):
    real = fake_bundle(tmp_path / "real")
    link = tmp_path / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable on this host")
    scan = bundle.scan_bundle(link)
    assert "bundle_root_reparse" in scan.errors


def test_symlinked_manifest_is_rejected(tmp_path):
    root = fake_bundle(tmp_path / "bundle")
    manifest = make_manifest(root)
    real = tmp_path / "real-manifest.json"
    bundle.write_manifest(real, manifest)
    link = root / "bundle-manifest.json"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable on this host")
    report = bundle.verify_manifest(root, link)
    assert report["ok"] is False
    assert "manifest_reparse" in report["errors"]


def test_write_manifest_refuses_different_schema(tmp_path):
    root = fake_bundle(tmp_path / "bundle")
    path = root / "bundle-manifest.json"
    path.write_text(json.dumps({"schema": "kards-offline-bundle-manifest/1"}),
                    encoding="utf-8")
    with pytest.raises(bundle.BundleError, match="schema_conflict"):
        bundle.write_manifest(path, make_manifest(root))


def test_cli_generate_default_prints_without_writing(tmp_path, capsys):
    root = fake_bundle(tmp_path / "bundle")
    code = bundle.main(["--root", str(root), "--generate"])
    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert output["schema"] == bundle.SCHEMA
    assert not (root / "bundle-manifest.json").exists()


def test_cli_generate_output_then_check(tmp_path, capsys):
    root = fake_bundle(tmp_path / "bundle")
    manifest_path = root / "bundle-manifest.json"
    code = bundle.main(["--root", str(root), "--generate", "--output", str(manifest_path)])
    assert code == 0
    assert manifest_path.is_file()
    capsys.readouterr()
    code = bundle.main(["--root", str(root), "--check"])
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["ok"] is True
    _write(root, "worker/entry.py", "print('changed')\n")
    code = bundle.main(["--root", str(root), "--check"])
    assert code == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_cli_incomplete_generate_writes_nothing(tmp_path, capsys):
    root = fake_bundle(tmp_path / "bundle")
    (root / "worker/entry.py").unlink()
    manifest_path = root / "bundle-manifest.json"
    code = bundle.main(["--root", str(root), "--generate", "--output", str(manifest_path)])
    report = json.loads(capsys.readouterr().out)
    assert code == 2
    assert report["ok"] is False
    assert not manifest_path.exists()


def test_sync_bundle_input_verifies_and_fails_closed(tmp_path):
    root = fake_bundle(tmp_path / "bundle")
    manifest = make_manifest(root)
    path = root / "bundle-manifest.json"
    bundle.write_manifest(path, manifest)
    _, summary, files, errors = sync.bundle_input(root, True)
    assert summary["ok"] is True, errors
    assert not errors
    assert set(files) == {item["path"] for item in manifest["files"]}
    assert files["worker/entry.py"] == (root / "worker/entry.py").read_bytes()

    _write(root, "worker/entry.py", "print('tamper')\n")  # same size, new hash
    _, summary, files, errors = sync.bundle_input(root, True)
    assert summary["ok"] is False
    assert files == {}
    assert any("sha256_mismatch" in reason for reason in errors), errors


def test_sync_bundle_input_missing_manifest_is_fail_closed(tmp_path):
    root = fake_bundle(tmp_path / "bundle")
    _, summary, files, errors = sync.bundle_input(root, True)
    assert summary["ok"] is False
    assert files == {}
    assert any("manifest_unreadable" in reason for reason in errors), errors


def test_sync_bundle_input_skips_missing_optional_models(tmp_path):
    root = fake_bundle(tmp_path / "bundle", models=True)
    manifest = make_manifest(root)
    bundle.write_manifest(root / "bundle-manifest.json", manifest)
    (root / "models/ui_det.onnx").unlink()
    (root / "models/card_cls.onnx").unlink()
    _, summary, files, errors = sync.bundle_input(root, True)
    assert summary["ok"] is True, errors
    assert not errors
    assert "models/ui_det.onnx" not in files
    assert summary["optional"]["kards_ui_det"]["state"] == "missing"
    assert summary["optional"]["kards_card_cls"]["state"] == "missing"


def test_should_backup_includes_bundle_paths_even_under_models():
    det = {"models/ui_det.onnx"}
    assert sync.should_backup("models/ui_det.onnx", det) is True
    # models/ 已不再被全局 SKIP：是否进入同步集由 manifest 声明决定。
    assert sync.should_backup("worker/entry.py", set()) is True
    assert sync.should_backup("data/events.jsonl", set()) is False
    assert sync.should_backup("dataset/x.bin", set()) is False


def test_backup_preflight_is_stat_only_and_reports_space(tmp_path):
    target = tmp_path / "target"
    (target / "runtime").mkdir(parents=True)
    (target / "runtime/new.py").write_bytes(b"x" * 100)
    (target / "config").mkdir()
    (target / "config/secret.json").write_bytes(b"must-not-be-read")
    report = [{"changes": [{"path": "runtime/new.py", "source_sha256": "a",
                            "target_sha256": "b"}]}]
    backup_base = tmp_path / "backups"
    result = sync.backup_preflight(backup_base, backup_base / "work", [target], report, set())
    assert result["ok"] is True, result["reasons"]
    assert result["backup_bytes"] == 100
    assert result["required_bytes"] == 100 + sync.BACKUP_SPACE_MARGIN
    assert result["free_bytes"] >= result["required_bytes"]
    assert not backup_base.exists()  # preflight is read-only


def test_backup_preflight_rejects_reparse_root(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable on this host")
    result = sync.backup_preflight(link, link / "work", [], [], set())
    assert result["ok"] is False
    assert any(reason.startswith("backup_root_reparse:") for reason in result["reasons"])


def test_backup_preflight_rejects_relative_root(tmp_path):
    target = tmp_path / "target"
    (target / "runtime").mkdir(parents=True)
    (target / "runtime/new.py").write_bytes(b"x")
    report = [{"changes": [{"path": "runtime/new.py", "source_sha256": "a",
                            "target_sha256": "b"}]}]
    result = sync.backup_preflight(Path("relative"), Path("relative/work"),
                                   [target], report, set())
    assert result["ok"] is False
    assert "backup_root_not_absolute" in result["reasons"]


def test_dev_manifest_derivation_preserves_channel_identity_diff(tmp_path):
    root = tmp_path / "source"
    _write(root, "plugin.toml", """\
[plugin]
id = "kards_companion"
name = "KARDS 猫娘陪玩"
version = "6.0.0"
entry = "plugin.plugins.kards_companion:KardsCompanionPlugin"

[plugin.author]
name = "MIKU"

[other]
id = "keep"
name = "keep"
entry = "keep"
""")
    data, diff, want = sync.manifest(root, "dev")
    parsed = tomllib.loads(data.decode("utf-8"))
    assert want == sync.DEV
    assert parsed["plugin"]["id"] == sync.DEV
    assert parsed["plugin"]["name"] == "KARDS 猫娘陪玩（dev）"
    assert parsed["plugin"]["entry"] == "plugin.plugins.kards_companion_dev:KardsCompanionPlugin"
    assert parsed["plugin"]["version"] == "6.0.0"
    assert set(diff) == {"id", "name", "entry"}
    assert parsed["other"] == {"id": "keep", "name": "keep", "entry": "keep"}

    formal_bytes, formal_diff, formal_want = sync.manifest(root, "formal")
    assert formal_want == sync.FORMAL
    assert formal_diff == {}
    assert tomllib.loads(formal_bytes.decode("utf-8"))["plugin"]["id"] == "kards_companion"
