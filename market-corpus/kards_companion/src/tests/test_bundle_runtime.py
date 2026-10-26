"""Phase-1 offline worker/bundle runtime tests.

These tests never download, install, or touch an installation copy.  Every
bundle is a temporary fake directory.  They assert the safety properties that
matter before any real vendored runtime exists:

* bundle-relative path resolution and traversal rejection;
* missing distribution manifest/vendor/model are blocked/missing, never ready;
* ``worker-runtime.json`` is optional: when absent the worker reports bundle
  manifest component entries instead of inventing dependency state;
* the JSON-lines protocol honours version, size, id, and timeout rules;
* bootstrap ``--check`` works under a dedicated Python 3.11 without importing
  optional third-party packages at module import time;
* no SDK/host-push/network/frame-binary path is present in the worker.
"""

from __future__ import annotations

import hashlib
import importlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

package_check = importlib.import_module("worker.package_check")
protocol = importlib.import_module("worker.protocol")


@pytest.fixture
def fake_bundle(tmp_path: Path) -> Path:
    root = tmp_path / "bundle"
    (root / "worker").mkdir(parents=True)
    (root / "worker" / "bootstrap.py").write_text("# fake worker\n", encoding="utf-8")
    return root


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _entry(
    rel: str,
    data: bytes,
    *,
    component: str | None = None,
    version: str = "1.0.0",
    license_value: str = "LicenseRef-Test-Only",
) -> dict[str, object]:
    if component is None:
        component = "notices" if rel in package_check.NOTICE_CANDIDATES else rel.split("/")[0]
    return {
        "path": rel,
        "size": len(data),
        "sha256": _sha(data),
        "component": component,
        "version": version,
        "license": license_value,
    }


def _components(entries: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    for entry in entries:
        item = out.setdefault(
            str(entry["component"]),
            {
                "version": entry["version"],
                "license": entry["license"],
                "file_count": 0,
                "size_bytes": 0,
            },
        )
        item["file_count"] += 1
        item["size_bytes"] += entry["size"]
    return dict(sorted(out.items()))


def _fingerprint(entries: list[dict[str, object]]) -> str:
    digest = hashlib.sha256()
    for entry in sorted(entries, key=lambda item: str(item["path"])):
        row = "\0".join(
            (
                str(entry["path"]),
                str(entry["size"]),
                str(entry["sha256"]),
                str(entry["component"]),
                str(entry["version"]),
                str(entry["license"]),
            )
        )
        digest.update(row.encode("utf-8") + b"\n")
    return digest.hexdigest()


def _write_distribution(
    bundle: Path,
    *,
    schema: str = package_check.DISTRIBUTION_MANIFEST_SCHEMA,
    model_bytes: bytes | None = None,
    integrity_sha: str | None = None,
    extra_file_entry: dict[str, object] | None = None,
) -> tuple[dict[str, object], bytes]:
    """Create distribution markers plus a full read-only bundle-manifest.json."""
    base_files = {
        "worker/bootstrap.py": (bundle / "worker" / "bootstrap.py").read_bytes()
        if (bundle / "worker" / "bootstrap.py").is_file()
        else b"# fake worker\n",
        "assets/cards/manifest.json": b'{"assets":[]}\n',
        "THIRD_PARTY_NOTICES.md": b"final test notices\n",
    }
    for relative, data in base_files.items():
        path = bundle / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    entries = [_entry(relative, data) for relative, data in base_files.items()]
    if model_bytes is not None:
        models_dir = bundle / "models"
        models_dir.mkdir(parents=True, exist_ok=True)
        for relative in package_check.MODEL_IDS.values():
            (bundle / relative).write_bytes(model_bytes)
            entries.append(_entry(relative, model_bytes))
    entries.sort(key=lambda item: str(item["path"]))
    by_path = {str(entry["path"]): entry for entry in entries}
    required: dict[str, dict[str, object]] = {}
    for name, alternatives in package_check.REQUIRED_MARKERS:
        path = next(relative for relative in alternatives if relative in by_path)
        entry = by_path[path]
        required[name] = {
            "expected": list(alternatives),
            "path": path,
            "present": True,
            "size": entry["size"],
            "sha256": entry["sha256"],
        }
    optional: dict[str, dict[str, object]] = {}
    for model_id, relative in package_check.MODEL_IDS.items():
        state = {
            "path": relative,
            "state": "present" if model_bytes is not None else "missing",
            "present": model_bytes is not None,
        }
        if model_bytes is not None:
            model_entry = by_path[relative]
            state.update(
                {"size": model_entry["size"], "sha256": model_entry["sha256"]}
            )
        optional[model_id] = state
    integrity = {
        "file_count": len(entries),
        "total_bytes": sum(int(entry["size"]) for entry in entries),
        "sha256": _fingerprint(entries),
    }
    if integrity_sha is not None:
        integrity["sha256"] = integrity_sha
    files: list[dict[str, object]] = list(entries)
    if extra_file_entry is not None:
        files.append(extra_file_entry)
    manifest: dict[str, object] = {
        "schema": schema,
        "generated_at": "2026-10-18T00:00:00+00:00",
        "plugin": {"id": "test-kards", "version": "1.0.0"},
        "allowed_dirs": list(package_check.BUNDLE_DIRS),
        "forbidden_components": ["training", "dataset", "data", "config"],
        "files": files,
        "components": _components(entries),
        "required": required,
        "optional": optional,
        "capabilities": {
            "bundle_ready": True,
            **{
                model_id: "present" if model_bytes is not None else "missing"
                for model_id in package_check.MODEL_IDS
            },
        },
        "warnings": [],
        "integrity": integrity,
    }
    payload = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    (bundle / "bundle-manifest.json").write_bytes(payload)
    return manifest, payload


def _worker_runtime(
    *,
    model_path: str = "models/ui_det.onnx",
    model_id: str = "kards_ui_det",
    model_bytes: bytes = b"",
    sha256: str | None = None,
    required: bool = True,
    runtime: dict[str, str] | None = None,
    compatibility: dict[str, object] | None = None,
    dependencies: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    digest = hashlib.sha256(model_bytes).hexdigest() if sha256 is None else sha256
    return {
        "schema": package_check.WORKER_RUNTIME_SCHEMA,
        "bundle_id": "test-kards-cpu",
        "bundle_version": "1.0.0",
        "generated_at": "2026-10-18T00:00:00Z",
        "compatibility": compatibility
        or {"requires_python": "3.11", "platform": "win_amd64", "cpu_only": True},
        "runtime": runtime
        or {
            "python_root": "vendor/python",
            "python_exe": "vendor/python/python.exe",
            "site_packages": "vendor/python/Lib/site-packages",
        },
        "dependencies": dependencies
        if dependencies is not None
        else [
            {
                "name": "numpy",
                "import_names": ["numpy"],
                "required": False,
            }
        ],
        "models": [
            {
                "id": model_id,
                "path": model_path,
                "required": required,
                "sha256": digest,
                "size_bytes": len(model_bytes),
            }
        ],
    }


def _write_worker_runtime(bundle: Path, data: dict[str, object]) -> None:
    (bundle / "worker-runtime.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _json_line(raw: bytes) -> dict[str, object]:
    return json.loads(raw.decode("utf-8"))


# --------------------------------------------------------------------- paths


def test_layout_is_absolute_and_bundle_contained(fake_bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    layout = package_check.resolve_layout(fake_bundle)

    assert layout.bundle_root == fake_bundle.resolve()
    assert layout.python_root == (fake_bundle / "vendor" / "python").resolve()
    assert layout.python_exe == (fake_bundle / "vendor" / "python" / "python.exe").resolve()
    assert layout.site_packages == (
        fake_bundle / "vendor" / "python" / "Lib" / "site-packages"
    ).resolve()
    for path in (
        layout.worker_root,
        layout.worker_runtime_path,
        layout.distribution_manifest_path,
        layout.assets_manifest_path,
        layout.python_root,
        layout.python_exe,
        layout.site_packages,
        *layout.model_paths.values(),
    ):
        assert Path(path).is_absolute()
        assert package_check.is_within_root(layout.bundle_root, path)


def test_relative_root_and_parent_segments_are_rejected(fake_bundle: Path) -> None:
    with pytest.raises(package_check.BundlePathError) as relative:
        package_check.resolve_bundle_root("relative\\bundle")
    assert relative.value.code == "bundle_root_not_absolute"

    with pytest.raises(package_check.BundlePathError) as parent:
        package_check.resolve_bundle_root(str(fake_bundle / ".."))
    assert parent.value.code == "bundle_root_parent_segment"

    with pytest.raises(package_check.BundlePathError) as member:
        package_check.resolve_member_path(fake_bundle, "../outside.onnx")
    assert member.value.code == "unsafe_path"

    with pytest.raises(package_check.BundlePathError) as drive:
        package_check.resolve_member_path(fake_bundle, "C:/outside.onnx")
    assert drive.value.code == "unsafe_path"

    with pytest.raises(package_check.BundlePathError) as absolute:
        package_check.resolve_member_path(fake_bundle, "/outside.onnx")
    assert absolute.value.code == "unsafe_path"


# ------------------------------------------------------------- self-check


def test_missing_distribution_and_vendor_are_blocked_and_worker_runtime_is_optional(
    fake_bundle: Path,
) -> None:
    result = package_check.check_bundle(fake_bundle)

    assert result["schema"] == package_check.SELFCHECK_SCHEMA
    assert result["status"] == "blocked"
    assert result["ok"] is False
    assert result["compatibility"]["requires_python"] == "3.11"
    assert result["compatibility"]["platform"] == "win_amd64"
    assert result["compatibility"]["cpu_only"] is True

    codes = {item["code"] for item in result["blockers"]}
    assert "distribution_manifest_missing" in codes
    assert "assets_manifest_missing" in codes
    assert "ocr_runtime_missing" not in codes  # OCR 标记已删除
    assert "vendor_missing" in codes
    assert "third_party_notices_missing" in codes
    assert "vendor_python_missing" in codes
    assert "vendor_site_packages_missing" in codes
    assert "worker_runtime_missing" not in codes

    assert result["worker_runtime"]["state"] == "absent"
    assert result["worker_runtime"]["present"] is False
    assert result["distribution"]["manifest"]["state"] == "missing"
    assert result["distribution"]["assets_manifest"]["state"] == "missing"
    assert "ocr_runtime" not in result["distribution"]
    assert result["distribution"]["vendor_root"]["present"] is False
    assert result["components"] == {}

    assert result["python"]["python_exe_present"] is False
    assert result["python"]["site_packages_present"] is False
    assert result["python"]["runtime_root"].endswith("vendor" + os.sep + "python")
    assert set(result["model"]) == set(package_check.MODEL_IDS)
    for model_id in package_check.MODEL_IDS:
        assert result["model"][model_id]["state"] == "missing"
        assert result["model"][model_id]["ready"] is False
        assert result["model"][model_id]["sha256"] is None
    assert result["dependencies"] == {}

    warning_codes = {item["code"] for item in result["warnings"]}
    assert "worker_runtime_absent" in warning_codes
    assert "model_missing" in warning_codes

    for resource in (
        "distribution_manifest",
        "assets_manifest",
        "vendor",
        "third_party_notices",
        "vendored_python",
        "vendored_site_packages",
    ):
        assert resource in result["blocked_resources"]
    assert "worker_runtime" not in result["blocked_resources"]
    assert "worker_runtime" in result["degraded_resources"]

    # A check is read-only: no vendor/models directories are fabricated.
    assert not (fake_bundle / "vendor").exists()
    assert not (fake_bundle / "models").exists()
    assert result["policy"]["downloads_allowed"] is False
    assert result["policy"]["creates_directories"] is False


def test_distribution_manifest_components_are_reported_when_worker_runtime_is_absent(
    fake_bundle: Path,
) -> None:
    manifest, _ = _write_distribution(fake_bundle)

    result = package_check.check_bundle(fake_bundle)

    assert result["worker_runtime"]["state"] == "absent"
    assert result["distribution"]["manifest"]["state"] == "loaded"
    assert result["distribution"]["manifest"]["schema"] == (
        package_check.DISTRIBUTION_MANIFEST_SCHEMA
    )
    assert result["components"] == manifest["components"]
    assert set(result["components"]) == {"worker", "assets", "notices"}
    assert result["dependencies"] == {}
    assert any(item["code"] == "worker_runtime_absent" for item in result["warnings"])
    assert "vendor_missing" in {item["code"] for item in result["blockers"]}


def test_worker_runtime_present_but_distribution_missing_stays_blocked(fake_bundle: Path) -> None:
    _write_worker_runtime(fake_bundle, _worker_runtime())

    result = package_check.check_bundle(fake_bundle)
    codes = {item["code"] for item in result["blockers"]}

    assert result["worker_runtime"]["state"] == "loaded"
    assert "distribution_manifest_missing" in codes
    assert "assets_manifest_missing" in codes
    assert "ocr_runtime_missing" not in codes
    assert "vendor_missing" in codes
    assert result["status"] == "blocked"
    assert set(result["dependencies"]) == {"numpy", "PIL", "cv2", "onnxruntime"}


def test_present_but_undeclared_model_is_never_ready(fake_bundle: Path) -> None:
    (fake_bundle / "models").mkdir()
    (fake_bundle / "models" / "ui_det.onnx").write_bytes(b"not-a-real-model")

    result = package_check.check_bundle(fake_bundle)
    model = result["model"]["kards_ui_det"]

    assert model["exists"] is True
    assert model["declared"] is False
    assert model["state"] == "present_unverified"
    assert model["ready"] is False
    assert any(item["code"] == "model_unverified" for item in result["warnings"])


def test_worker_runtime_path_traversal_is_rejected(fake_bundle: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.onnx"
    outside.write_bytes(b"outside-model")
    data = _worker_runtime(
        model_path="../outside.onnx",
        model_bytes=b"outside-model",
        runtime={
            "python_root": "vendor/python",
            "python_exe": "../outside-python.exe",
            "site_packages": "vendor/python/Lib/site-packages",
        },
    )
    _write_worker_runtime(fake_bundle, data)

    result = package_check.check_bundle(fake_bundle)

    assert result["status"] == "blocked"
    unsafe = [item for item in result["blockers"] if item["code"] == "unsafe_path"]
    assert unsafe
    fields = {item.get("field") for item in unsafe}
    assert "models[0].path" in fields
    assert "runtime.python_exe" in fields
    assert result["worker_runtime"]["state"] == "invalid"
    assert result["model"]["kards_ui_det"]["ready"] is False


def test_worker_runtime_rejects_python_314_and_non_windows_target(fake_bundle: Path) -> None:
    data = _worker_runtime()
    data["compatibility"] = {
        "requires_python": "3.14",
        "platform": "linux_x86_64",
        "cpu_only": False,
    }
    _write_worker_runtime(fake_bundle, data)

    result = package_check.check_bundle(fake_bundle)
    incompatible = [item for item in result["blockers"] if item["code"] == "incompatible_target"]
    fields = {item.get("field") for item in incompatible}

    assert result["status"] == "blocked"
    assert "compatibility.requires_python" in fields
    assert "compatibility.platform" in fields
    assert "compatibility.cpu_only" in fields


def test_declared_model_checksum_mismatch_is_not_ready(fake_bundle: Path) -> None:
    model_bytes = b"pretend-onnx-bytes"
    (fake_bundle / "models").mkdir()
    (fake_bundle / "models" / "ui_det.onnx").write_bytes(model_bytes)
    _write_worker_runtime(
        fake_bundle,
        _worker_runtime(model_bytes=model_bytes, sha256="0" * 64, required=True),
    )

    result = package_check.check_bundle(fake_bundle)
    model = result["model"]["kards_ui_det"]

    assert model["state"] == "checksum_mismatch"
    assert model["ready"] is False
    assert any(item["code"] == "model_checksum_mismatch" for item in result["blockers"])


def test_verified_model_without_onnxruntime_is_not_ready(fake_bundle: Path) -> None:
    model_bytes = b"pretend-onnx-bytes"
    (fake_bundle / "models").mkdir()
    (fake_bundle / "models" / "ui_det.onnx").write_bytes(model_bytes)
    _write_worker_runtime(fake_bundle, _worker_runtime(model_bytes=model_bytes, required=True))

    result = package_check.check_bundle(fake_bundle)
    model = result["model"]["kards_ui_det"]

    assert result["worker_runtime"]["state"] == "loaded"
    assert model["declared"] is True
    assert model["sha256"] == hashlib.sha256(model_bytes).hexdigest()
    assert model["state"] != "ready"
    assert model["ready"] is False
    onnx_state = result["dependencies"]["onnxruntime"]["state"]
    assert onnx_state != "ready"


def test_distribution_optional_models_are_used_without_worker_runtime(
        fake_bundle: Path) -> None:
    model_bytes = b"pretend-onnx-bytes"
    _write_distribution(fake_bundle, model_bytes=model_bytes)

    result = package_check.check_bundle(fake_bundle)

    assert result["worker_runtime"]["state"] == "absent"
    for model_id in package_check.MODEL_IDS:
        model = result["model"][model_id]
        assert model["declared"] is True
        assert model["declaration_source"] == package_check.DISTRIBUTION_MANIFEST_FILENAME
        assert model["sha256"] == hashlib.sha256(model_bytes).hexdigest()
        assert model["state"] == "present_verified"
        assert model["ready"] is False


def test_distribution_manifest_is_read_but_never_rewritten(fake_bundle: Path) -> None:
    _, payload = _write_distribution(fake_bundle)
    before = (fake_bundle / "bundle-manifest.json").read_bytes()

    result = package_check.check_bundle(fake_bundle)
    after = (fake_bundle / "bundle-manifest.json").read_bytes()

    assert payload == before
    assert after == before
    assert result["distribution"]["manifest"]["state"] == "loaded"
    assert result["distribution"]["manifest"]["schema"] == (
        package_check.DISTRIBUTION_MANIFEST_SCHEMA
    )
    assert result["distribution"]["assets_manifest"]["state"] == "loaded"
    assert result["distribution"]["assets_manifest"]["relative_path"] == (
        "assets/cards/manifest.json"
    )
    assert "ocr_runtime" not in result["distribution"]
    assert "distribution_manifest_missing" not in {
        item["code"] for item in result["blockers"]
    }
    # vendor is still absent, so the bundle remains blocked.
    assert result["status"] == "blocked"
    assert not (fake_bundle / "vendor").exists()


def test_distribution_schema_mismatch_blocks(fake_bundle: Path) -> None:
    _write_distribution(fake_bundle, schema="kards-offline-bundle-manifest/1")

    result = package_check.check_bundle(fake_bundle)
    codes = {item["code"] for item in result["blockers"]}

    assert "distribution_manifest_schema_mismatch" in codes
    assert result["distribution"]["manifest"]["state"] == "invalid"
    assert result["status"] == "blocked"


def test_distribution_required_file_missing_blocks(fake_bundle: Path) -> None:
    _write_distribution(fake_bundle)
    (fake_bundle / "assets" / "cards" / "manifest.json").unlink()

    result = package_check.check_bundle(fake_bundle)
    codes = {item["code"] for item in result["blockers"]}

    assert "assets_manifest_missing" in codes
    assert "distribution_required_missing" in codes
    assert "distribution_file_missing" in codes
    assert result["status"] == "blocked"


def test_distribution_integrity_mismatch_blocks(fake_bundle: Path) -> None:
    _write_distribution(fake_bundle, integrity_sha="0" * 64)

    result = package_check.check_bundle(fake_bundle)
    codes = {item["code"] for item in result["blockers"]}

    assert "distribution_manifest_integrity_mismatch" in codes
    assert result["distribution"]["manifest"]["state"] == "invalid"
    assert result["status"] == "blocked"


def test_distribution_unsafe_file_path_blocks(fake_bundle: Path) -> None:
    _write_distribution(
        fake_bundle,
        extra_file_entry={
            "path": "../outside.onnx",
            "size": 1,
            "sha256": "0" * 64,
            "component": "worker",
            "version": "1.0.0",
            "license": "LicenseRef-Test-Only",
        },
    )

    result = package_check.check_bundle(fake_bundle)
    unsafe = [item for item in result["blockers"] if item["code"] == "distribution_manifest_unsafe_path"]

    assert unsafe
    assert any(str(item.get("field", "")).startswith("files[") for item in unsafe)
    assert result["status"] == "blocked"


# ------------------------------------------------------------------ protocol


def test_oversized_json_line_is_rejected_and_stream_recovers() -> None:
    oversized = b'{"v":1,"id":"x","op":"ping"}' + b" " * (protocol.MAX_LINE_BYTES + 1)
    with pytest.raises(protocol.ProtocolError) as exc:
        protocol.decode_line(oversized)
    assert exc.value.code == "line_too_large"

    good = b'{"v":1,"id":"next","op":"ping"}\n'
    stream = io.BytesIO(b'{"v":1,"id":"big","op":"ping"}' + b" " * (protocol.MAX_LINE_BYTES + 1) + b"\n" + good)
    with pytest.raises(protocol.ProtocolError) as stream_exc:
        protocol.read_limited_line(stream)
    assert stream_exc.value.code == "line_too_large"
    assert protocol.read_limited_line(stream) == good


def test_unknown_protocol_version_and_op_return_structured_errors() -> None:
    called: list[str] = []

    def handler(request: dict[str, object]) -> dict[str, object]:
        called.append(str(request.get("id")))
        return {"op": request.get("op")}

    version_resp = _json_line(
        protocol.process_line(
            b'{"v":2,"id":"req-1","op":"ping","payload":{}}',
            handler,
        )
    )
    assert version_resp["ok"] is False
    assert version_resp["id"] == "req-1"
    assert version_resp["error"]["code"] == "unsupported_protocol_version"
    assert "state" not in version_resp

    op_resp = _json_line(
        protocol.process_line(
            b'{"v":1,"id":"req-2","op":"bogus","payload":{}}',
            handler,
        )
    )
    assert op_resp["ok"] is False
    assert op_resp["id"] == "req-2"
    assert op_resp["error"]["code"] == "unsupported_op"
    assert "push" not in op_resp
    assert called == []


def test_timeout_semantics_are_structured_and_correlated() -> None:
    def slow_handler(_request: dict[str, object]) -> dict[str, object]:
        time.sleep(0.08)
        return {"op": "slow"}

    request = {
        "v": 1,
        "id": "req-timeout",
        "op": "ping",
        "session_id": "session-1",
        "frame_id": 7,
        "timeout_ms": 10,
        "payload": {},
    }
    response = _json_line(
        protocol.process_line(
            json.dumps(request).encode("utf-8"),
            slow_handler,
        )
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "timeout"
    assert response["error"]["retryable"] is True
    assert response["id"] == "req-timeout"
    assert response["session_id"] == "session-1"
    assert response["frame_id"] == 7


@pytest.mark.parametrize("timeout", [0, -1, protocol.MAX_TIMEOUT_MS + 1, True, "10", 1.5])
def test_invalid_timeout_values_are_rejected(timeout: object) -> None:
    raw = json.dumps(
        {"v": 1, "id": "req-t", "op": "ping", "timeout_ms": timeout, "payload": {}}
    ).encode("utf-8")
    with pytest.raises(protocol.ProtocolError) as exc:
        protocol.decode_line(raw)
    assert exc.value.code == "invalid_timeout"


def test_inline_frame_payload_is_rejected() -> None:
    binary = json.dumps(
        {
            "v": 1,
            "id": "req-b64",
            "op": "ping",
            "payload": {"frame_b64": "AAAA"},
        }
    ).encode("utf-8")
    with pytest.raises(protocol.ProtocolError) as exc:
        protocol.decode_line(binary)
    assert exc.value.code == "binary_payload_rejected"

    long_string = json.dumps(
        {
            "v": 1,
            "id": "req-long",
            "op": "ping",
            "payload": {"note": "x" * (protocol.MAX_INLINE_STRING_CHARS + 1)},
        }
    ).encode("utf-8")
    with pytest.raises(protocol.ProtocolError) as long_exc:
        protocol.decode_line(long_string)
    assert long_exc.value.code == "inline_payload_too_large"


def test_frame_op_is_recognized_but_not_implemented() -> None:
    raw = json.dumps(
        {
            "v": 1,
            "id": "req-frame",
            "op": "frame.process",
            "session_id": "session-1",
            "frame_id": 3,
            "payload": {},
        }
    ).encode("utf-8")

    def not_implemented(_request: dict[str, object]) -> dict[str, object]:
        raise protocol.ProtocolError("not_implemented", "frame processing is phase 2")

    response = _json_line(protocol.process_line(raw, not_implemented))
    assert response["ok"] is False
    assert response["error"]["code"] == "not_implemented"
    assert response["frame_id"] == 3

    # session/frame ids are validated before the op is rejected.
    missing_frame = json.dumps(
        {
            "v": 1,
            "id": "req-frame-2",
            "op": "frame.process",
            "session_id": "session-1",
            "payload": {},
        }
    ).encode("utf-8")
    with pytest.raises(protocol.ProtocolError) as missing_exc:
        protocol.decode_line(missing_frame)
    assert missing_exc.value.code == "missing_frame_id"


# ------------------------------------------------------- worker/CLI safety


def test_bootstrap_check_cli_is_structured_and_read_only(fake_bundle: Path) -> None:
    shutil.copytree(
        PLUGIN_ROOT / "worker",
        fake_bundle / "worker",
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    proc = subprocess.run(
        [sys.executable, str(fake_bundle / "worker" / "bootstrap.py"), "--check"],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )

    assert proc.returncode == 2, proc.stderr
    assert "Traceback" not in proc.stderr
    lines = proc.stdout.strip().splitlines()
    assert len(lines) == 1
    result = json.loads(lines[0])
    assert result["status"] == "blocked"
    assert all(item["ready"] is False for item in result["model"].values())
    assert result["worker_runtime"]["state"] == "absent"
    assert not (fake_bundle / "vendor").exists()
    assert not (fake_bundle / "models").exists()


def test_bootstrap_check_rejects_relative_bundle_root(fake_bundle: Path) -> None:
    shutil.copytree(
        PLUGIN_ROOT / "worker",
        fake_bundle / "worker",
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    proc = subprocess.run(
        [
            sys.executable,
            str(fake_bundle / "worker" / "bootstrap.py"),
            "--check",
            "--bundle-root",
            "relative\\bundle",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 2
    assert "Traceback" not in proc.stderr
    result = json.loads(proc.stdout.strip())
    assert result["status"] == "blocked"
    assert any(item["code"] == "bundle_root_not_absolute" for item in result["blockers"])


def test_bootstrap_serve_ping_and_shutdown(fake_bundle: Path) -> None:
    shutil.copytree(
        PLUGIN_ROOT / "worker",
        fake_bundle / "worker",
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    requests = [
        {"v": 1, "id": "ping-1", "op": "ping", "session_id": "s-1", "frame_id": 1, "payload": {}},
        {"v": 1, "id": "shutdown-1", "op": "shutdown", "payload": {}},
    ]
    payload = "\n".join(json.dumps(item) for item in requests) + "\n"
    proc = subprocess.run(
        [sys.executable, str(fake_bundle / "worker" / "bootstrap.py"), "--serve"],
        input=payload,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stderr
    assert "Traceback" not in proc.stderr
    lines = [json.loads(line) for line in proc.stdout.strip().splitlines()]
    assert [line["id"] for line in lines] == ["ping-1", "shutdown-1"]
    assert lines[0]["ok"] is True
    assert lines[0]["state"]["binary_frames"] is False
    assert lines[0]["state"]["host_push"] is False
    assert lines[1]["ok"] is True
    assert lines[1]["state"]["op"] == "shutdown"


def test_worker_sources_do_not_import_sdk_network_or_create_dirs() -> None:
    for name in ("__init__.py", "bootstrap.py", "protocol.py", "package_check.py"):
        text = (PLUGIN_ROOT / "worker" / name).read_text(encoding="utf-8")
        assert "plugin.sdk" not in text
        assert "push_message" not in text
        for forbidden in ("import requests", "urllib.request", "import socket", "http.client"):
            assert forbidden not in text
        assert ".mkdir(" not in text


def test_bundle_contract_files_are_templates_not_fabricated_locks() -> None:
    requirements = (PLUGIN_ROOT / "requirements-bundle.txt").read_text(encoding="utf-8")
    assert "BLOCKED_PENDING_BUILD" in requirements
    assert "win_amd64" in requirements
    assert "3.11" in requirements
    assert "==3.14" not in requirements
    assert not re.search(r"^[A-Za-z0-9_.-]+==\d", requirements, re.MULTILINE)

    notices = (PLUGIN_ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert "BLOCKED_PENDING_BUILD" in notices
    assert "发布拒绝门" in notices


def test_manifest_schemas_pin_the_unified_contracts() -> None:
    bundle_schema = json.loads(
        (PLUGIN_ROOT / "bundle-manifest.schema.json").read_text(encoding="utf-8")
    )
    assert bundle_schema["properties"]["schema"]["const"] == (
        package_check.DISTRIBUTION_MANIFEST_SCHEMA
    )
    assert {"files", "components", "required", "optional", "integrity"} <= set(
        bundle_schema["required"]
    )

    runtime_schema = json.loads(
        (PLUGIN_ROOT / "worker-runtime.schema.json").read_text(encoding="utf-8")
    )
    assert runtime_schema["properties"]["schema"]["const"] == package_check.WORKER_RUNTIME_SCHEMA
    compat = runtime_schema["properties"]["compatibility"]["properties"]
    assert compat["requires_python"]["const"] == "3.11"
    assert compat["platform"]["const"] == "win_amd64"
    assert compat["cpu_only"]["const"] is True
    assert runtime_schema["properties"]["models"]["items"]["required"].count("sha256") == 1
