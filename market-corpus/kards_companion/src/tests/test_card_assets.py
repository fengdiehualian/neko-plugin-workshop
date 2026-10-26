"""包内卡图资源机制测试（Python 3.11，纯本地，不联网）。

使用 32 字节的假 AVIF 容器头验证结构校验；不依赖真实卡图、Pillow 或
公网资源。覆盖：待构建清单 1083 条、manifest schema/version/card_id 唯一/
路径安全/sha256/bytes/AVIF magic/coverage、card_id 索引、缺资源 blocked、
license 门禁、symlink/junction 拒绝、Pillow 缺失状态。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import subprocess
from pathlib import Path

import pytest
from kards_companion.knowledge import assets
from kards_companion.knowledge.assets import (
    ASSET_FORMAT_AVIF,
    DEFAULT_ASSETS_ROOT,
    DISTRIBUTION_BLOCKED,
    DISTRIBUTION_LICENSED,
    MANIFEST_SCHEMA,
    MANIFEST_VERSION,
    AssetBlockedError,
    AssetIntegrityError,
    AssetManifestError,
    AssetNotFoundError,
    CardAssetManifest,
    OfflineCardAssetIndex,
    build_pending_worklist,
    builtin_card_ids,
)

# ftyp box: size=32, major brand=avif, compatible brands include avif/mif1/miaf/MA1B
GOOD_AVIF = b"\x00\x00\x00\x20ftypavif\x00\x00\x00\x00avifmif1miafMA1B"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
assert len(GOOD_AVIF) == len(PNG_BYTES) == 32


# ---------------------------------------------------------------- fixtures

def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _variant(root: Path, relpath: str, data: bytes = GOOD_AVIF) -> dict:
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {
        "path": relpath,
        "sha256": _sha(data),
        "bytes": len(data),
        "format": ASSET_FORMAT_AVIF,
    }


def _source(card_id: str) -> dict:
    return {
        "image_url": f"https://www.kards.com/images/card/v53/zh-Hans/{card_id}.avif",
        "thumb_url": (
            f"https://www.kards.com/images/card/v53/zh-Hans/thumb/{card_id}.avif"
        ),
    }


def _entry(root: Path, card_id: str, *,
           full_data: bytes = GOOD_AVIF,
           thumb_data: bytes = GOOD_AVIF) -> dict:
    return {
        "card_id": card_id,
        "full": _variant(root, f"full/{card_id}.avif", full_data),
        "thumb": _variant(root, f"thumb/{card_id}.avif", thumb_data),
        "source": _source(card_id),
    }


def _manifest(cards: list[dict], **overrides) -> dict:
    data = {
        "schema": MANIFEST_SCHEMA,
        "version": MANIFEST_VERSION,
        "distribution_status": DISTRIBUTION_BLOCKED,
        "self_use_candidate": False,
        "cards": cards,
    }
    data.update(overrides)
    return data


def _write_manifest(root: Path, data: dict) -> Path:
    path = root / "manifest.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _tree(base: Path, card_ids: tuple[str, ...] = ("alpha", "beta"),
          **overrides) -> tuple[Path, list[dict]]:
    root = base / "assets" / "cards"
    root.mkdir(parents=True, exist_ok=True)
    entries = [_entry(root, card_id) for card_id in card_ids]
    _write_manifest(root, _manifest(entries, **overrides))
    return root, entries


def _index(base: Path, card_ids: tuple[str, ...] = ("alpha", "beta"),
           expected: tuple[str, ...] | None = None, **overrides):
    root, entries = _tree(base, card_ids, **overrides)
    return OfflineCardAssetIndex(
        root,
        expected_card_ids=card_ids if expected is None else expected,
    ), root, entries


def _sorted_keys(value) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            keys.add(key)
            keys.update(_sorted_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.update(_sorted_keys(item))
    return keys


def _symlink_or_skip(target: Path, link: Path, *, directory: bool) -> None:
    try:
        os.symlink(target, link, target_is_directory=directory)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink unavailable: {exc}")


def _junction_or_skip(target: Path, link: Path) -> None:
    if os.name != "nt":
        pytest.skip("Windows junction test")
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True, check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"junction unavailable: {result.stderr[:200]!r}")


# ---------------------------------------------------------------- 待构建清单

def test_pending_worklist_has_1083_builtin_card_ids_and_urls(monkeypatch) -> None:
    def no_network(*args, **kwargs):
        pytest.fail("待构建清单必须只读本地元数据，不得解析/访问网络")

    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    catalog_before = assets.BUILTIN_CARDS_FILE.read_bytes()
    worklist = build_pending_worklist()
    assert assets.BUILTIN_CARDS_FILE.read_bytes() == catalog_before  # 只读

    assert worklist["schema"] == "kards.card-assets.worklist"
    assert worklist["version"] == 1
    assert worklist["distribution_status"] == DISTRIBUTION_BLOCKED
    assert worklist["self_use_candidate"] is True
    assert worklist["expected_card_count"] == 1083
    assert len(worklist["cards"]) == 1083

    ids = [item["card_id"] for item in worklist["cards"]]
    assert len(set(ids)) == 1083
    assert "13e_dragons" in ids
    assert tuple(ids) == builtin_card_ids()
    for item in worklist["cards"]:
        assert item["full_url"].startswith("https://www.kards.com/")
        assert item["thumb_url"].startswith("https://www.kards.com/")
        assert item["suggested_full_path"] == f"full/{item['card_id']}.avif"
        assert item["suggested_thumb_path"] == f"thumb/{item['card_id']}.avif"

    keys = _sorted_keys(worklist)
    assert "import_id" not in keys
    assert not (keys & {"image_bytes", "image_data", "data", "secret", "token",
                        "api_key", "private_key", "base64"})
    # 清单只含 JSON 元数据，绝不包含 bytes 对象
    assert all(not isinstance(value, (bytes, bytearray))
               for value in _walk_values(worklist))
    assert re.fullmatch(r"[0-9a-f]{64}", worklist["catalog_sha256"])


def _walk_values(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_values(item)
    else:
        yield value


# ---------------------------------------------------------------- 正常索引

def test_ready_index_is_by_card_id_and_loads_only_local_bytes(tmp_path) -> None:
    index, root, _ = _index(tmp_path)
    assert index.state == "ready"
    assert index.coverage.complete
    assert len(index) == 2 and "alpha" in index
    assert index.status("alpha").full.state == "ready"
    assert index.status("alpha").thumb.state == "ready"
    assert index.entry("alpha").source == _source("alpha")
    assert index.load_image_bytes("alpha", variant="full") == GOOD_AVIF
    assert index.load_image_bytes("alpha", variant="thumb") == GOOD_AVIF

    # 索引键是 card_id，不是 import_id；也不暴露 import_id API
    assert index.status("rV") is None
    assert "rV" not in index
    assert not hasattr(index, "by_import_id")
    with pytest.raises(AssetNotFoundError):
        index.load_image_bytes("rV")

    records = index.matcher_records(names={"alpha": "Alpha"})
    assert [record.card_id for record in records] == ["alpha", "beta"]
    assert records[0].name == "Alpha"
    assert records[0].path == root / "thumb" / "alpha.avif"
    assert records[0].path.is_file()
    assert records[0].source_url.startswith("https://www.kards.com/")

    state = index.build_matcher_records(names={"alpha": "Alpha"})
    assert state.state == "ready" and state.ok
    assert state.pil_available is True
    assert len(state.records) == 2 and not state.missing
    assert index.describe()["ready"] == 2
    assert index.describe()["distribution_status"] == DISTRIBUTION_BLOCKED


def test_matcher_records_report_missing_pil_as_state(tmp_path, monkeypatch) -> None:
    index, _, _ = _index(tmp_path)
    monkeypatch.setattr(assets, "_pil_available", lambda: False)
    state = index.build_matcher_records(require_pil=True)
    assert state.state == "pil_unavailable"
    assert state.pil_available is False and state.ok is False
    assert len(state.records) == 2  # 仍返回只读元数据记录

    metadata_state = index.build_matcher_records(require_pil=False)
    assert metadata_state.state == "ready" and metadata_state.pil_available is False


def test_manifest_dump_roundtrip_and_schema_constants(tmp_path) -> None:
    _, entries = _tree(tmp_path)
    manifest = CardAssetManifest.from_dict(_manifest(entries))
    dumped = manifest.to_dict()
    assert dumped["distribution_status"] == DISTRIBUTION_BLOCKED
    assert dumped["self_use_candidate"] is False
    assert "image_bytes" not in json.dumps(dumped)
    assert CardAssetManifest.from_dict(dumped) == manifest
    assert manifest.card_ids == ("alpha", "beta")
    assert manifest.by_card_id["alpha"].card_id == "alpha"

    schema = json.loads(
        Path(assets.__file__).with_name("assets_manifest.schema.json")
        .read_text(encoding="utf-8")
    )
    assert schema["$schema"].endswith("2020-12/schema")
    assert schema["type"] == "object" and schema["additionalProperties"] is False
    assert schema["properties"]["schema"]["const"] == MANIFEST_SCHEMA
    assert schema["properties"]["version"]["const"] == MANIFEST_VERSION
    assert schema["properties"]["distribution_status"]["enum"] == [
        DISTRIBUTION_BLOCKED, DISTRIBUTION_LICENSED,
    ]
    assert set(schema["required"]) == {
        "schema", "version", "distribution_status", "cards",
    }
    assert set(dumped) <= set(schema["properties"])
    card_schema = schema["$defs"]["card"]
    assert set(card_schema["required"]) == {"card_id", "full", "thumb", "source"}
    assert card_schema["additionalProperties"] is False
    assert schema["$defs"]["variant"]["properties"]["format"]["const"] == "avif"


# ---------------------------------------------------------------- 结构拒绝

@pytest.mark.parametrize(
    "mutation, reason",
    [
        (lambda data: data.update(schema="kards.card-assets.v2"), "unsupported_schema"),
        (lambda data: data.update(version=2), "unsupported_version"),
        (lambda data: data["cards"][1].update(card_id="alpha"), "duplicate_card_id"),
        (lambda data: data["cards"][1].update(card_id="ALPHA"), "duplicate_card_id"),
        (lambda data: data["cards"][1].update(import_id="rV"), "unknown_field"),
        (lambda data: data["cards"][0].pop("full"), "missing_field"),
        (lambda data: data["cards"][0].pop("source"), "missing_field"),
        (lambda data: data["cards"][0]["full"].update(sha256="A" * 64), "invalid_sha256"),
        (lambda data: data["cards"][0]["full"].update(sha256="0" * 63), "invalid_sha256"),
        (lambda data: data["cards"][0]["full"].update(bytes=0), "invalid_bytes"),
        (lambda data: data["cards"][0]["full"].update(bytes=33554433), "invalid_bytes"),
        (lambda data: data["cards"][0]["full"].update(format="png"), "invalid_format"),
        (lambda data: data["cards"][0]["source"].update(
            image_url="http://www.kards.com/x.avif"), "invalid_source_url"),
        (lambda data: data["cards"][0]["source"].update(
            image_url="HTTPS://www.kards.com/x.avif"), "invalid_source_url"),
        (lambda data: data["cards"][0]["source"].update(
            image_url="https://user:pass@www.kards.com/x.avif"), "invalid_source_url"),
        (lambda data: data.update(distribution_status=DISTRIBUTION_LICENSED),
         "distribution_status_not_verified"),
        (lambda data: data.update(distribution_status="public"), "invalid_distribution_status"),
        (lambda data: data["cards"][1]["thumb"].update(
            path=data["cards"][0]["full"]["path"]), "duplicate_asset_path"),
        (lambda data: data["cards"][0].update(
            thumb=dict(data["cards"][0]["full"])), "duplicate_asset_path"),
    ],
    ids=[
        "schema", "version", "duplicate-id", "casefold-duplicate-id", "import-id-field",
        "missing-full", "missing-source", "uppercase-sha", "short-sha", "zero-bytes",
        "oversize-bytes", "format", "http-source", "uppercase-https",
        "credentials-source", "licensed-without-license", "bad-distribution",
        "path-collision", "same-full-thumb",
    ],
)
def test_manifest_rejects_structure_and_identity_violations(
    tmp_path, mutation, reason
) -> None:
    _, entries = _tree(tmp_path)
    data = _manifest(entries)
    mutation(data)
    with pytest.raises(AssetManifestError) as excinfo:
        CardAssetManifest.from_dict(data)
    assert excinfo.value.reason == reason


@pytest.mark.parametrize("bad_path", [
    "../escape.avif",
    "/absolute.avif",
    "full\\windows.avif",
    "C:/drive.avif",
    "full/../../escape.avif",
    "./leading.avif",
    "full//double.avif",
    "full/x.png",
    "full/CON.avif",
    "full/x.avif ",
    "full/.hidden.avif",
    "full/x.",
])
def test_manifest_rejects_unsafe_asset_paths(tmp_path, bad_path: str) -> None:
    _, entries = _tree(tmp_path)
    data = _manifest(entries)
    data["cards"][0]["full"]["path"] = bad_path
    with pytest.raises(AssetManifestError) as excinfo:
        CardAssetManifest.from_dict(data)
    assert excinfo.value.reason == "unsafe_asset_path"


@pytest.mark.parametrize("bad_id", ["../escape", "a/b", "..", "CON", "alpha."])
def test_manifest_rejects_unsafe_card_ids(tmp_path, bad_id: str) -> None:
    _, entries = _tree(tmp_path)
    data = _manifest(entries)
    data["cards"][0]["card_id"] = bad_id
    with pytest.raises(AssetManifestError) as excinfo:
        CardAssetManifest.from_dict(data)
    assert excinfo.value.reason == "unsafe_card_id"


@pytest.mark.parametrize("payload", [
    {"image_bytes": "AAAA"},
    {"cards": [{"data": "AAAA"}]},
    {"source": {"api_key": "secret"}},
])
def test_manifest_rejects_embedded_payloads_and_secrets(tmp_path, payload) -> None:
    _, entries = _tree(tmp_path)
    data = _manifest(entries)
    if "image_bytes" in payload:
        data.update(payload)
    elif "cards" in payload:
        data["cards"][0].update(payload["cards"][0])
    else:
        data["cards"][0]["source"].update(payload["source"])
    with pytest.raises(AssetManifestError) as excinfo:
        CardAssetManifest.from_dict(data)
    assert excinfo.value.reason == "payload_field_forbidden"


# ---------------------------------------------------------------- 文件完整性

def test_index_marks_sha_mismatch_corrupt_and_refuses_load(tmp_path) -> None:
    root, _ = _tree(tmp_path)
    tampered = bytearray(GOOD_AVIF)
    tampered[-1] = ord("C")  # 仍满足 AVIF 容器头，但 sha256 不同
    (root / "full" / "alpha.avif").write_bytes(bytes(tampered))

    index = OfflineCardAssetIndex(root, expected_card_ids=("alpha", "beta"))
    assert index.state == "partial"
    status = index.status("alpha")
    assert status.full.state == "corrupt"
    assert status.full.reason == "sha256_mismatch"
    assert status.thumb.state == "ready"
    with pytest.raises(AssetIntegrityError) as excinfo:
        index.load_image_bytes("alpha", variant="full")
    assert excinfo.value.reason == "asset_corrupt"
    assert index.load_image_bytes("alpha", variant="thumb") == GOOD_AVIF
    assert "alpha" in index.corrupt_card_ids


def test_index_marks_bytes_mismatch_corrupt(tmp_path) -> None:
    root, _ = _tree(tmp_path)
    (root / "full" / "alpha.avif").write_bytes(GOOD_AVIF + b"\x00")
    index = OfflineCardAssetIndex(root, expected_card_ids=("alpha", "beta"))
    assert index.status("alpha").full.state == "corrupt"
    assert index.status("alpha").full.reason == "bytes_mismatch"


def test_index_rejects_non_avif_magic_even_when_hash_matches(tmp_path) -> None:
    root, entries = _tree(tmp_path)
    # 重新按 PNG 字节生成 alpha 的清单，使 sha256/bytes 完全匹配但 magic 不是 AVIF
    entries[0]["full"] = _variant(root, "full/alpha.avif", PNG_BYTES)
    _write_manifest(root, _manifest(entries))
    index = OfflineCardAssetIndex(root, expected_card_ids=("alpha", "beta"))
    status = index.status("alpha")
    assert status.full.state == "corrupt"
    assert status.full.reason == "avif_magic_mismatch"
    assert status.thumb.state == "ready"
    with pytest.raises(AssetIntegrityError) as excinfo:
        index.load_image_bytes("alpha", variant="full")
    assert excinfo.value.reason == "asset_corrupt"


def test_load_rechecks_integrity_after_index_construction(tmp_path) -> None:
    index, root, _ = _index(tmp_path, card_ids=("alpha",))
    assert index.state == "ready"
    tampered = bytearray(GOOD_AVIF)
    tampered[-2] = ord("D")
    (root / "thumb" / "alpha.avif").write_bytes(bytes(tampered))
    with pytest.raises(AssetIntegrityError) as excinfo:
        index.load_image_bytes("alpha", variant="thumb")
    assert excinfo.value.reason == "sha256_mismatch"


def test_metadata_only_index_reports_unverified(tmp_path) -> None:
    root, _ = _tree(tmp_path)
    index = OfflineCardAssetIndex(root, expected_card_ids=("alpha", "beta"),
                                  verify_files=False)
    assert index.state == "unverified"
    assert index.status("alpha").full.state == "unverified"
    assert index.matcher_records() == ()
    state = index.build_matcher_records(include_unverified=True)
    assert state.state == "unverified" and len(state.records) == 2
    assert index.load_image_bytes("alpha", variant="thumb") == GOOD_AVIF


# ---------------------------------------------------------------- coverage/缺失

def test_missing_assets_root_and_manifest_are_blocked(tmp_path) -> None:
    absent = OfflineCardAssetIndex(tmp_path / "absent", expected_card_ids=("alpha",))
    assert absent.state == "blocked"
    assert absent.blocked_reason == "assets_dir_missing"
    assert absent.manifest is None
    with pytest.raises(AssetBlockedError):
        absent.load_image_bytes("alpha")

    empty = tmp_path / "empty"
    empty.mkdir()
    missing_manifest = OfflineCardAssetIndex(empty, expected_card_ids=("alpha",))
    assert missing_manifest.state == "blocked"
    assert missing_manifest.blocked_reason == "manifest_invalid:manifest_missing"

    bad_json = tmp_path / "badjson"
    bad_json.mkdir()
    (bad_json / "manifest.json").write_text("{", encoding="utf-8")
    invalid = OfflineCardAssetIndex(bad_json, expected_card_ids=("alpha",))
    assert invalid.state == "blocked"
    assert invalid.blocked_reason == "manifest_invalid:manifest_invalid_json"

    bad_schema = tmp_path / "badschema"
    bad_schema.mkdir()
    (bad_schema / "manifest.json").write_text(
        json.dumps({"schema": "nope", "version": 1, "cards": []}), encoding="utf-8")
    invalid_schema = OfflineCardAssetIndex(bad_schema, expected_card_ids=("alpha",))
    assert invalid_schema.state == "blocked"
    assert invalid_schema.blocked_reason == "manifest_invalid:unsupported_schema"


def test_coverage_missing_and_unknown_card_ids(tmp_path) -> None:
    index, _, _ = _index(tmp_path / "missing", card_ids=("alpha",),
                         expected=("alpha", "beta"))
    assert index.state == "partial"
    assert not index.coverage.complete
    assert index.coverage.missing == ("beta",)
    assert index.coverage.unknown == ()
    assert index.status("beta") is None
    assert "beta" in index.missing_card_ids
    assert [record.card_id for record in index.matcher_records()] == ["alpha"]
    assert index.build_matcher_records().state == "partial"

    unknown, _, _ = _index(tmp_path / "unknown", card_ids=("alpha", "gamma"),
                           expected=("alpha",))
    assert unknown.state == "partial"
    assert unknown.coverage.unknown == ("gamma",)
    assert not unknown.coverage.complete


def test_missing_thumb_has_explicit_card_and_slot_state(tmp_path) -> None:
    root, _ = _tree(tmp_path)
    (root / "thumb" / "beta.avif").unlink()
    index = OfflineCardAssetIndex(root, expected_card_ids=("alpha", "beta"))
    assert index.state == "partial"
    status = index.status("beta")
    assert status.state == "partial"
    assert status.full.state == "ready"
    assert status.thumb.state == "missing"
    assert "beta" in index.missing_card_ids
    with pytest.raises(AssetNotFoundError) as excinfo:
        index.load_image_bytes("beta", variant="thumb")
    assert excinfo.value.reason == "asset_missing"
    assert index.load_image_bytes("beta", variant="full") == GOOD_AVIF


def test_missing_build_returns_blocked_matcher_state(tmp_path) -> None:
    index = OfflineCardAssetIndex(tmp_path / "absent", expected_card_ids=("alpha",))
    state = index.build_matcher_records()
    assert state.state == "blocked"
    assert state.records == ()
    assert state.reason == "assets_dir_missing"
    # 即便调用方要求 Pillow，缺资源也必须优先报告 blocked，而不是 pil_unavailable
    with_pil = index.build_matcher_records(require_pil=True)
    assert with_pil.state == "blocked" and with_pil.records == ()


# ---------------------------------------------------------------- license 门禁

def test_license_gate_is_blocked_by_default_and_self_use_is_separate(tmp_path) -> None:
    index, _, _ = _index(tmp_path / "selfuse", card_ids=("alpha",),
                         self_use_candidate=True)
    assert index.distribution_status == DISTRIBUTION_BLOCKED
    assert index.self_use_candidate is True
    assert index.license_verified is False
    assert index.manifest.public_release_licensed is False
    assert index.describe()["public_release_licensed"] is False

    verified_license = {
        "name": "Operator-verified example license",
        "verified": True,
        "redistribution_allowed": True,
        "evidence": "docs/card-assets.md review note (example only)",
    }
    licensed, _, _ = _index(
        tmp_path / "licensed", card_ids=("alpha",),
        distribution_status=DISTRIBUTION_LICENSED,
        license=verified_license,
        self_use_candidate=True,
    )
    assert licensed.distribution_status == DISTRIBUTION_LICENSED
    assert licensed.license_verified is True
    assert licensed.self_use_candidate is True
    assert licensed.manifest.public_release_licensed is True


def test_licensed_status_requires_verified_license(tmp_path) -> None:
    _, entries = _tree(tmp_path / "unverified")
    unverified = _manifest(
        entries,
        distribution_status=DISTRIBUTION_LICENSED,
        license={
            "name": "Unverified",
            "verified": False,
            "redistribution_allowed": True,
            "evidence": "not reviewed",
        },
    )
    with pytest.raises(AssetManifestError) as excinfo:
        CardAssetManifest.from_dict(unverified)
    assert excinfo.value.reason == "distribution_status_not_verified"

    # 即便 license 已验证，显式声明 blocked 也保持更严格的公开门禁
    _, entries = _tree(tmp_path / "blocked")
    verified_but_blocked = _manifest(
        entries,
        distribution_status=DISTRIBUTION_BLOCKED,
        license={
            "name": "Verified but withheld",
            "verified": True,
            "redistribution_allowed": True,
            "evidence": "reviewed",
        },
    )
    manifest = CardAssetManifest.from_dict(verified_but_blocked)
    assert manifest.license_verified is True
    assert manifest.public_release_licensed is False


# ---------------------------------------------------------------- 路径安全

def test_symlinked_assets_root_is_blocked(tmp_path) -> None:
    real_root, _ = _tree(tmp_path / "real", card_ids=("alpha",))
    link = tmp_path / "linked-cards"
    _symlink_or_skip(real_root, link, directory=True)
    index = OfflineCardAssetIndex(link, expected_card_ids=("alpha",))
    assert index.state == "blocked"
    assert index.blocked_reason == "assets_dir_unsafe"
    with pytest.raises(AssetBlockedError):
        index.load_image_bytes("alpha")


def test_junction_assets_root_is_blocked(tmp_path) -> None:
    real_root, _ = _tree(tmp_path / "real", card_ids=("alpha",))
    link = tmp_path / "junction-cards"
    _junction_or_skip(real_root, link)
    index = OfflineCardAssetIndex(link, expected_card_ids=("alpha",))
    assert index.state == "blocked"
    assert index.blocked_reason == "assets_dir_unsafe"


def test_symlinked_asset_file_is_marked_unsafe(tmp_path) -> None:
    root, _ = _tree(tmp_path, card_ids=("alpha", "beta"))
    outside = tmp_path / "outside.avif"
    outside.write_bytes(GOOD_AVIF)
    (root / "full" / "alpha.avif").unlink()
    try:
        os.symlink(outside, root / "full" / "alpha.avif")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"file symlink unavailable: {exc}")
    index = OfflineCardAssetIndex(root, expected_card_ids=("alpha", "beta"))
    status = index.status("alpha")
    assert status.state == "unsafe"
    assert status.full.state == "unsafe"
    assert status.thumb.state == "ready"
    assert "alpha" in index.unsafe_card_ids
    with pytest.raises(AssetNotFoundError) as excinfo:
        index.load_image_bytes("alpha", variant="full")
    assert excinfo.value.reason == "asset_unsafe"


def test_junction_directory_inside_root_is_unsafe(tmp_path) -> None:
    root, _ = _tree(tmp_path, card_ids=("alpha", "beta"))
    outside = tmp_path / "outside-full"
    outside.mkdir()
    (outside / "alpha.avif").write_bytes(GOOD_AVIF)
    full_dir = root / "full"
    # 用 junction 替换 full 目录（实际文件在 root 外）
    for child in full_dir.iterdir():
        child.unlink()
    full_dir.rmdir()
    _junction_or_skip(outside, full_dir)
    index = OfflineCardAssetIndex(root, expected_card_ids=("alpha", "beta"))
    assert index.status("alpha").full.state == "unsafe"
    assert index.status("alpha").thumb.state == "ready"


def test_asset_symlink_manifest_entries_are_still_lexically_rejected(tmp_path) -> None:
    # 即使文件系统攻击不发生，清单自身也不得携带穿越路径
    _, entries = _tree(tmp_path)
    data = _manifest(entries)
    data["cards"][0]["thumb"]["path"] = "thumb/../../outside.avif"
    with pytest.raises(AssetManifestError):
        CardAssetManifest.from_dict(data)


# ---------------------------------------------------------------- 静态边界

def test_assets_module_has_no_network_client_imports() -> None:
    source = Path(assets.__file__).read_text(encoding="utf-8")
    assert not re.search(
        r"^\s*(?:import|from)\s+(?:socket|urllib|http|requests|aiohttp|httpx)\b",
        source, flags=re.MULTILINE,
    )
    assert ("url" + "open(") not in source


def test_package_assets_root_constant_is_the_documented_local_directory() -> None:
    assert DEFAULT_ASSETS_ROOT.name == "cards"
    assert DEFAULT_ASSETS_ROOT.parent.name == "assets"
    # 当前仓库不携带卡图：默认索引必须明确 blocked，而不是偷偷联网或伪造 ready
    index = OfflineCardAssetIndex()
    if not DEFAULT_ASSETS_ROOT.exists():
        assert index.state == "blocked"
        assert index.blocked_reason == "assets_dir_missing"
