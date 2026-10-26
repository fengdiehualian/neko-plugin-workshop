from __future__ import annotations

import importlib.util
import io
import json
import re
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import PNG_BYTES
from sticker_manager.services import lanlan, local_http, pack_io, tool_watch
from sticker_manager.services.bundled_pack import open_pack
from sticker_manager.services.library import Library
from sticker_manager.services.official_assets import validate_official_assets
from sticker_manager.services.official_delivery import OfficialDelivery

ROOT = Path(__file__).resolve().parents[1]


def _oversized_pack(path):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", b" " * (pack_io.PACK_MANIFEST_MAX_BYTES + 1))
        archive.writestr("stickers/image.png", PNG_BYTES)
    return path


def test_oversized_manifest_is_rejected_before_decompression(tmp_path, monkeypatch):
    path = _oversized_pack(tmp_path / "large.zip")
    with zipfile.ZipFile(path) as archive:
        monkeypatch.setattr(archive, "open", lambda *a, **k: pytest.fail("oversized member was decompressed"))
        with pytest.raises(pack_io.ManifestLimitError):
            pack_io.read_pack_manifest(archive)


def test_manifest_actual_read_is_bounded_even_if_header_lies():
    class Archive:
        def getinfo(self, name):
            return SimpleNamespace(is_dir=lambda: False, file_size=1)

        def open(self, info):
            return io.BytesIO(b" " * (pack_io.PACK_MANIFEST_MAX_BYTES + 1))

    with pytest.raises(pack_io.ManifestLimitError):
        pack_io.read_pack_manifest(Archive())


def test_oversized_import_fails_without_bare_fallback_or_catalog_write(tmp_path):
    library = Library(tmp_path / "library")
    assert library.load().ok
    result = library.import_pack(_oversized_pack(tmp_path / "large.zip"))
    assert result == {"imported": 0, "duplicates": 0, "rejected": 0, "failed": 1}
    assert library.count() == 0
    assert not (tmp_path / "library/catalog.json").exists()


def test_official_paths_also_reject_oversized_manifest(tmp_path):
    path = _oversized_pack(tmp_path / "large.zip")
    with pytest.raises(pack_io.ManifestLimitError):
        validate_official_assets(path)
    with pytest.raises(pack_io.ManifestLimitError):
        OfficialDelivery(path).resolve(PNG_BYTES)
    library = Library(tmp_path / "library")
    assert library.seed_official(path)["status"] == "invalid_assets"
    assert not library.official_zone()


def test_bad_json_keeps_existing_bare_import_compatibility(tmp_path):
    path = tmp_path / "broken.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", b"not JSON")
        archive.writestr("stickers/image.png", PNG_BYTES)
    library = Library(tmp_path / "library")
    assert library.load().ok
    assert library.import_pack(path)["imported"] == 1


def test_local_http_disables_proxy_and_redirects_and_bounds_read(monkeypatch):
    calls = []

    class Response(io.BytesIO):
        def read(self, size=-1):
            calls.append(size)
            return super().read(size)

    def build(*handlers):
        assert isinstance(handlers[0], urllib.request.ProxyHandler)
        assert handlers[0].proxies == {}
        redirect = handlers[1]
        assert redirect.redirect_request(None, None, 302, "", {}, "https://external.invalid") is None
        return SimpleNamespace(open=lambda url, timeout: Response(b'{"current_catgirl":"test"}'))

    monkeypatch.setattr(local_http.urllib.request, "build_opener", build)
    assert local_http.read_local_json("http://127.0.0.1:48911/api/test", timeout=0.5)["current_catgirl"] == "test"
    assert calls == [local_http.MAX_RESPONSE_BYTES + 1]


@pytest.mark.parametrize("url", [
    "https://127.0.0.1/test", "http://external.invalid/test", "http://localhost/test",
    "http://user:password@127.0.0.1/test", "file:///private",
])
def test_non_loopback_http_is_rejected_before_open(monkeypatch, url):
    monkeypatch.setattr(local_http.urllib.request, "build_opener", lambda *a: pytest.fail("external request"))
    with pytest.raises(ValueError, match="local_http_url"):
        local_http.read_local_json(url, timeout=0.5)


def test_local_http_response_size_limit(monkeypatch):
    opener = SimpleNamespace(open=lambda *a, **k: io.BytesIO(b" " * (local_http.MAX_RESPONSE_BYTES + 1)))
    monkeypatch.setattr(local_http.urllib.request, "build_opener", lambda *a: opener)
    with pytest.raises(ValueError, match="response_size"):
        local_http.read_local_json("http://127.0.0.1/test", timeout=0.5)


def test_resolver_and_watch_use_local_transport(monkeypatch, run_async):
    calls = []

    def fetch(url, *, timeout):
        calls.append((url, timeout))
        return {"current_catgirl": "test", "tools_by_role": {}}

    monkeypatch.setattr(lanlan, "read_local_json", fetch)
    monkeypatch.setattr(tool_watch, "read_local_json", fetch)
    resolver = lanlan.LanlanResolver(SimpleNamespace())
    assert resolver._fetch_blocking() == "test"
    assert run_async(tool_watch._default_fetch("/api/tools?role=test"))["tools_by_role"] == {}
    assert len(calls) == 2
    assert all(url.startswith("http://127.0.0.1:") for url, _ in calls)


def test_local_http_redirect_is_a_failure_without_followup():
    handler = local_http._NoRedirect()
    request = urllib.request.Request("http://127.0.0.1/test")
    with pytest.raises(urllib.error.HTTPError):
        handler.http_error_302(request, io.BytesIO(), 302, "Found", {"location": "https://external.invalid"})


def test_release_host_gates_only_call_isolation_runner():
    spec = importlib.util.spec_from_file_location("sticker_release_gate_test", ROOT / "tools/release_gate.py")
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    for name in ("pytest", "check", "release", "hosted-tsx"):
        command = gate.gate_command(name, Path("host"))
        assert Path(command[1]).name == "host_isolation.py"
        assert command[-2:] == ["--only", name]
        assert "sync" not in command and "uv" not in command


# High-confidence signatures only; ordinary API option names are not secrets.
SIGNATURES = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{50,}\b"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(r"(?i)[A-Z]:[\\/]Users[\\/][^\\/\s]+[\\/]"),
)
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".tmpgate", ".pytest_cache", ".ruff_cache", "node_modules"}


def test_high_confidence_secrets_and_personal_paths_are_not_in_sources():
    matches = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or set(path.relative_to(ROOT).parts) & SKIP_DIRS:
            continue
        if path.suffix not in {".py", ".md", ".json", ".toml", ".tsx", ".ts", ".yml", ".cjs"}:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if any(pattern.search(line) for pattern in SIGNATURES):
                matches.append(f"{path.relative_to(ROOT).as_posix()}:{number}")
    assert not matches, "Suspected private material (values omitted): " + ", ".join(matches)


def test_secret_signatures_detect_known_bad_synthetic_inputs():
    bad = ["-----BEGIN " + "PRIVATE KEY-----", "ghp_" + "A" * 36, "AKIA" + "A" * 16]
    assert all(any(pattern.search(value) for pattern in SIGNATURES) for value in bad)


def test_official_archive_contains_only_declared_media():
    with open_pack(ROOT / "official/official_pack.zip") as archive:
        manifest = json.loads(pack_io.read_pack_manifest(archive))
        expected = {"manifest.json"}
        for row in manifest["stickers"]:
            expected.add("stickers/" + row["file"])
            expected.add("delivery/" + row["delivery_file"])
        names = archive.namelist()
        assert len(names) == len(set(names))
        assert set(names) == expected
        for info in archive.infolist():
            assert not info.flag_bits & 1
            assert not info.is_dir()
            assert "\\" not in info.filename and ":" not in info.filename
            assert all(part not in {"", ".", ".."} for part in info.filename.split("/"))
