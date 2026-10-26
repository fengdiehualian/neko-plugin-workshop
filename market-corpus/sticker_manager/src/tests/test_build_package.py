import importlib.util
import json
import subprocess
import sys
from fnmatch import fnmatchcase
from pathlib import Path
from tomllib import loads as toml_loads
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("sticker_package_builder", ROOT / "tools" / "build_package.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)
ISOLATION_SPEC = importlib.util.spec_from_file_location("sticker_host_isolation_test", ROOT / "tools/host_isolation.py")
isolation = importlib.util.module_from_spec(ISOLATION_SPEC)
ISOLATION_SPEC.loader.exec_module(isolation)


def _stage_fixture(tmp_path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    (root / "__init__.py").write_text("value = 1\n", encoding="utf-8")
    (root / "plugin.meta.json").write_text('{"source_files": ["tests/absent.py"]}', encoding="utf-8")
    host = tmp_path / "host"
    python = host / ".venv" / ("Scripts/python.exe" if builder.os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(builder, "ROOT", root)
    return root, host


def test_metadata_is_derived_from_payload_and_same_bytes_are_archived(tmp_path, monkeypatch):
    root, host = _stage_fixture(tmp_path, monkeypatch)
    original = (root / "__init__.py").read_bytes()

    def probe(args, **kwargs):
        staged = Path(args[-1])
        assert not (staged / "plugin.meta.json").exists()
        assert kwargs["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
        assert kwargs["cwd"] != host
        assert Path(kwargs["env"]["NEKO_STORAGE_SELECTED_ROOT"]).is_relative_to(
            Path(kwargs["env"]["STICKER_ISOLATION_ROOT"])
        )
        assert (kwargs["cwd"] / "sitecustomize.py").is_file()
        assert "derive_plugin_metadata" in args[2]
        assert "read_packaged_metadata" in args[2]
        (staged / "plugin.meta.json").write_text(json.dumps({"source_files": ["__init__.py"]}), encoding="utf-8")
        (root / "__init__.py").write_text("value = 2\n", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(builder.subprocess, "run", probe)
    entries = dict(builder.stage_plugin_entries(list(root.iterdir()), host))
    assert entries["plugins/sticker_manager/__init__.py"] == original
    assert json.loads(entries["plugins/sticker_manager/plugin.meta.json"])["source_files"] == ["__init__.py"]


def test_probe_failure_refuses_package_metadata(tmp_path, monkeypatch):
    root, host = _stage_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(builder.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=1, stdout="", stderr="probe failed"
    ))
    with pytest.raises(SystemExit, match="probe failed"):
        builder.stage_plugin_entries(list(root.iterdir()), host)


def test_mismatched_file_list_is_rejected(tmp_path, monkeypatch):
    root, host = _stage_fixture(tmp_path, monkeypatch)

    def probe(args, **kwargs):
        (Path(args[-1]) / "plugin.meta.json").write_text('{"source_files": ["tests/absent.py"]}', encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(builder.subprocess, "run", probe)
    with pytest.raises(SystemExit, match="file list"):
        builder.stage_plugin_entries(list(root.iterdir()), host)


def test_missing_host_is_an_explicit_build_failure(tmp_path):
    with pytest.raises(SystemExit, match="Host Python not found"):
        builder.stage_plugin_entries([], tmp_path / "missing")


def test_snapshot_copies_support_source_not_installed_plugins_or_caches(tmp_path):
    host = tmp_path / "host"
    for name in (
        "plugin/sdk/base.py",
        "plugin/sdk/hosted-ui/index.d.ts",
        "plugin/plugins/installed/__init__.py",
        "plugin/tests/test_host.py",
        "utils/__pycache__/stale.py",
        "config/api_providers.json",
        "utils/large-asset.zip",
    ):
        source = host / name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(name, encoding="utf-8")
    snapshot = tmp_path / "isolated" / "snapshot"
    isolation.copy_host_sources(host, snapshot)
    assert (snapshot / "plugin/sdk/base.py").is_file()
    assert (snapshot / "plugin/sdk/hosted-ui/index.d.ts").is_file()
    assert (snapshot / "config/api_providers.json").is_file()
    assert not (snapshot / "plugin/plugins/installed").exists()
    assert not (snapshot / "plugin/tests").exists()
    assert not (snapshot / "utils/__pycache__").exists()
    assert not (snapshot / "utils/large-asset.zip").exists()
    assert (snapshot / "sitecustomize.py").is_file()


def test_snapshot_must_not_be_created_inside_source_host(tmp_path):
    host = tmp_path / "host"
    host.mkdir()
    with pytest.raises(ValueError, match="outside"):
        isolation.copy_host_sources(host, host / "nested")


def test_write_guard_is_inherited_by_python_descendants(tmp_path):
    host = tmp_path / "source-host"
    host.mkdir()
    root = tmp_path / "sandbox"
    snapshot = root / "host"
    isolation.copy_host_sources(host, snapshot)
    outside = tmp_path / "must-not-exist.txt"
    inside = root / "allowed.txt"
    worker = (
        "import os,sys; from pathlib import Path; "
        "assert getattr(sys,'_sticker_isolation_root',None); "
        "open(os.devnull,'w').close(); "
        "Path(sys.argv[1]).write_text('allowed'); "
        "Path(sys.argv[2]).write_text('forbidden')"
    )
    code = (
        "import subprocess,sys; "
        f"p=subprocess.run([sys.executable,'-c',{worker!r},sys.argv[1],sys.argv[2]],capture_output=True,text=True); "
        "assert p.returncode != 0; "
        "assert 'isolated host refused filesystem write' in p.stderr"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(inside), str(outside)],
        cwd=snapshot, env=isolation.isolated_environment(snapshot, root),
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert inside.read_text() == "allowed"
    assert not outside.exists()


def test_runtime_files_match_market_allowlist():
    rules = toml_loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["neko"]["build"]
    selected = {path.relative_to(ROOT).as_posix() for path in builder.collect_plugin_files()}
    market = {
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*")
        if path.is_file()
        and not set(path.relative_to(ROOT).parts) & set(rules["exclude_dirs"])
        and path.name not in rules["exclude_files"]
        and any(fnmatchcase(path.relative_to(ROOT).as_posix(), pattern) for pattern in rules["include"])
    }
    assert selected == market
    assert "plugin.meta.json" not in selected
    assert "official/official_pack.zip.parts.json" in selected
    assert "official/official_pack.zip.part001" in selected
    assert "official/official_pack.zip" not in selected


def test_collector_leaves_development_and_private_files_out(tmp_path, monkeypatch):
    for name in (
        "__init__.py", "plugin.toml", "core/catalog.py", "services/library.py", "ui/panel.tsx",
        "official/official_pack.zip", "core/__pycache__/leak.py", "data/catalog.json",
        ".env", ".env.local", "private.json", "debug.log", "plugin.meta.json", "tests/test_private.py",
        "core/private.json", "services/private.toml", "i18n/debug.py",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    monkeypatch.setattr(builder, "ROOT", tmp_path)
    (tmp_path / "official/official_pack.zip.part001").write_bytes(b"fixture")
    (tmp_path / "official/official_pack.zip.parts.json").write_text(json.dumps({
        "parts": [{"name": "official_pack.zip.part001"}],
    }), encoding="utf-8")
    selected = {path.relative_to(tmp_path).as_posix() for path in builder.collect_plugin_files()}
    assert selected == {
        "__init__.py", "plugin.toml", "core/catalog.py", "services/library.py", "ui/panel.tsx",
        "official/official_pack.zip.parts.json", "official/official_pack.zip.part001",
    }


def test_collector_rejects_linked_runtime_inputs(tmp_path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    private = tmp_path / "private.py"
    private.write_bytes(b"private fixture")
    try:
        (root / "__init__.py").symlink_to(private)
    except OSError:
        pytest.skip("symlink creation is not available")
    monkeypatch.setattr(builder, "ROOT", root)
    with pytest.raises(ValueError, match="linked build input"):
        builder.collect_plugin_files()


def test_guard_allows_asyncio_self_pipe_but_rejects_host_connections(tmp_path):
    host = tmp_path / "source-host"
    host.mkdir()
    root = tmp_path / "sandbox"
    snapshot = root / "host"
    isolation.copy_host_sources(host, snapshot)
    code = (
        "import asyncio,socket\n"
        "asyncio.run(asyncio.sleep(0))\n"
        "try:\n"
        "    socket.create_connection(('127.0.0.1',48916),timeout=1)\n"
        "except PermissionError as exc:\n"
        "    assert 'isolated host refused network connection' in str(exc)\n"
        "else:\n"
        "    raise AssertionError('real host connection was permitted')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=snapshot,
        env=isolation.isolated_environment(snapshot, root),
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stderr
