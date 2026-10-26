"""KARDS 包内卡图资源：离线索引、清单校验与待构建清单（Python 3.11）。

边界与保证：

* **只读本地**：本模块不导入任何网络客户端，也不会解析/下载公网 URL。卡图只从
  包内 ``assets/cards/`` 读取；``knowledge/cards_builtin.json`` 仅用于生成元数据
  待构建清单与覆盖率期望，卡库文本的既有离线路径保持不变。
* **默认不分发**：仓库/安装包当前不含卡图；``assets/cards/manifest.json`` 或
  资源根缺失时索引返回 ``blocked``，而不是抛异常或悄悄联网补图。
* **授权门禁**：公开再分发默认 ``blocked_unlicensed``。只有 manifest 中显式
  声明且通过字段校验的 license（``verified``、``redistribution_allowed``、
  非空 ``name``/``evidence``）才允许声明 ``licensed_public_release``；
  “自用候选”是独立字段，不能替代公开授权。
* **安全根**：资源根、manifest 及每个相对路径组件都拒绝 symlink/junction；
  清单路径拒绝绝对路径、``..``、反斜杠、盘符、ADS、Windows 设备名等穿越形态。
* **无载荷清单**：manifest 只允许路径/摘要/字节数/格式/来源 URL 等元数据，
  拒绝图片字节、base64/data 载荷与密钥字段。

本模块只依赖标准库。Pillow 是可选依赖：``build_matcher_records`` 可在没有
Pillow 时返回明确的 ``pil_unavailable`` 状态。
"""

from __future__ import annotations

import hashlib
import importlib.util
import ipaddress
import json
import os
import re
import stat
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

__all__ = [
    "ASSET_FORMAT_AVIF",
    "DEFAULT_ASSETS_ROOT",
    "DISTRIBUTION_BLOCKED",
    "DISTRIBUTION_LICENSED",
    "MANIFEST_FILENAME",
    "MANIFEST_SCHEMA",
    "MANIFEST_VERSION",
    "MATCHER_PIL_UNAVAILABLE",
    "WORKLIST_SCHEMA",
    "AssetBlockedError",
    "AssetCoverage",
    "AssetIntegrityError",
    "AssetManifestError",
    "AssetNotFoundError",
    "AssetRootError",
    "AssetSlotState",
    "AssetVariantSpec",
    "BuiltinCardRef",
    "CardAssetEntry",
    "CardAssetError",
    "CardAssetManifest",
    "CardAssetStatus",
    "MatcherBuildState",
    "MatcherRecord",
    "OfflineCardAssetIndex",
    "build_pending_worklist",
    "builtin_card_ids",
    "load_builtin_card_refs",
]

# ---------------------------------------------------------------- 常量

MANIFEST_SCHEMA = "kards.card-assets"
MANIFEST_VERSION = 1
MANIFEST_FILENAME = "manifest.json"
WORKLIST_SCHEMA = "kards.card-assets.worklist"
WORKLIST_VERSION = 1
ASSET_FORMAT_AVIF = "avif"

DISTRIBUTION_BLOCKED = "blocked_unlicensed"
DISTRIBUTION_LICENSED = "licensed_public_release"
_DISTRIBUTION_VALUES = frozenset({DISTRIBUTION_BLOCKED, DISTRIBUTION_LICENSED})

STATE_READY = "ready"
STATE_PARTIAL = "partial"
STATE_UNVERIFIED = "unverified"
STATE_BLOCKED = "blocked"

SLOT_READY = STATE_READY
SLOT_PARTIAL = STATE_PARTIAL
SLOT_UNVERIFIED = STATE_UNVERIFIED
SLOT_MISSING = "missing"
SLOT_CORRUPT = "corrupt"
SLOT_UNSAFE = "unsafe"

MATCHER_PIL_UNAVAILABLE = "pil_unavailable"

DEFAULT_ASSETS_ROOT = Path(__file__).resolve().parents[1] / "assets" / "cards"
BUILTIN_CARDS_FILE = Path(__file__).with_name("cards_builtin.json")

MANIFEST_MAX_BYTES = 8 * 1024 * 1024
ASSET_MAX_BYTES = 32 * 1024 * 1024
CATALOG_MAX_BYTES = 32 * 1024 * 1024
_METADATA_MAX_STRING = 4096

_CARD_ID_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")
_PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
     *(f"LPT{i}" for i in range(1, 10)), "CONIN$", "CONOUT$"}
)

# manifest 是元数据文件；这些键一旦出现即视为图片载荷/密钥泄漏。
_FORBIDDEN_PAYLOAD_KEYS = frozenset({
    "api_key", "apikey", "base64", "blob", "byte_data", "content", "credentials",
    "data", "file_bytes", "image_bytes", "image_data", "password", "payload",
    "private_key", "secret", "secrets", "token", "access_token", "refresh_token",
})
_FORBIDDEN_KEY_SUFFIXES = ("_bytes", "_secret", "_token", "_password")

_TOP_LEVEL_KEYS = frozenset({
    "schema", "version", "generated_at", "catalog_sha256", "distribution_status",
    "self_use_candidate", "license", "cards",
})
_VARIANT_KEYS = frozenset({"path", "sha256", "bytes", "format"})
_SOURCE_KEYS = frozenset({"image_url", "thumb_url"})
_ENTRY_KEYS = frozenset({"card_id", "full", "thumb", "source"})
_LICENSE_KEYS = frozenset({
    "name", "verified", "redistribution_allowed", "evidence", "url",
})


# ---------------------------------------------------------------- 异常

class CardAssetError(RuntimeError):
    """卡图资源机制的基类异常。"""

    reason = "card_asset_error"


class AssetManifestError(CardAssetError):
    """manifest/catalog 结构或元数据不合法（不涉及读取图片内容）。"""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


class AssetRootError(CardAssetError):
    """资源根或路径组件不安全（symlink/junction/穿越）。"""

    def __init__(self, reason: str = "unsafe_asset_root", detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


class AssetBlockedError(CardAssetError):
    """整个资源索引不可用（缺目录/manifest 等）。"""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        super().__init__(f"{reason}: {detail}" if detail else reason)


class AssetNotFoundError(CardAssetError):
    """指定 card_id/变体不存在或路径不安全。"""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        super().__init__(f"{reason}: {detail}" if detail else reason)


class AssetIntegrityError(CardAssetError):
    """文件存在但 bytes/sha256/AVIF magic 校验失败。"""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        super().__init__(f"{reason}: {detail}" if detail else reason)


# ---------------------------------------------------------------- 基础数据

@dataclass(frozen=True)
class BuiltinCardRef:
    """从内置文本快照读取的最小只读元数据（不含图片字节）。"""

    card_id: str
    name: str
    image_url: str
    thumb_url: str


@dataclass(frozen=True)
class AssetVariantSpec:
    """一张卡图（full/thumb）的清单条目。"""

    path: str
    sha256: str
    bytes: int
    format: str = ASSET_FORMAT_AVIF

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "bytes": self.bytes,
            "format": self.format,
        }


@dataclass(frozen=True)
class CardAssetEntry:
    card_id: str
    full: AssetVariantSpec
    thumb: AssetVariantSpec
    source: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "card_id": self.card_id,
            "full": self.full.to_dict(),
            "thumb": self.thumb.to_dict(),
            "source": dict(self.source),
        }


@dataclass(frozen=True)
class CardAssetManifest:
    """已严格校验的包内卡图清单。

    ``distribution_status`` 是公开发行门禁字段：默认 ``blocked_unlicensed``；
    声明 ``licensed_public_release`` 必须同时存在显式验证过的 license。
    ``self_use_candidate`` 是独立的自用候选标记，不构成公开授权。
    """

    cards: tuple[CardAssetEntry, ...]
    generated_at: str = ""
    license: dict[str, Any] | None = None
    distribution_status: str = DISTRIBUTION_BLOCKED
    self_use_candidate: bool = False
    catalog_sha256: str = ""
    schema: str = MANIFEST_SCHEMA
    version: int = MANIFEST_VERSION

    # ---------------------------------------------------------- load / dump

    @classmethod
    def from_path(cls, path: str | os.PathLike[str]) -> "CardAssetManifest":
        manifest_path = Path(path)
        if _is_link_like(manifest_path):
            raise AssetManifestError("manifest_unsafe", "manifest is a symlink/junction")
        try:
            info = os.stat(manifest_path, follow_symlinks=False)
        except FileNotFoundError as exc:
            raise AssetManifestError("manifest_missing", str(manifest_path)) from exc
        except OSError as exc:
            raise AssetManifestError("manifest_unreadable", str(exc)) from exc
        if not stat.S_ISREG(info.st_mode):
            raise AssetManifestError("manifest_not_regular", str(manifest_path))
        if info.st_size > MANIFEST_MAX_BYTES:
            raise AssetManifestError("manifest_too_large", str(info.st_size))
        try:
            raw = _read_local_bytes(manifest_path, MANIFEST_MAX_BYTES)
        except ValueError as exc:
            raise AssetManifestError("manifest_too_large", str(exc)) from exc
        except OSError as exc:
            raise AssetManifestError("manifest_unreadable", str(exc)) from exc
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AssetManifestError("manifest_invalid_json", str(exc)) from exc
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CardAssetManifest":
        if not isinstance(data, Mapping):
            raise AssetManifestError("invalid_type", "manifest must be an object")
        payload = dict(data)
        _reject_embedded_payload(payload)
        _check_keys(payload, _TOP_LEVEL_KEYS, "manifest")

        schema = _require_str(_require_field(payload, "schema", "manifest"),
                              "manifest.schema", max_len=64, allow_empty=False)
        if schema != MANIFEST_SCHEMA:
            raise AssetManifestError("unsupported_schema", schema)
        version = _require_int(_require_field(payload, "version", "manifest"),
                               "manifest.version")
        if version != MANIFEST_VERSION:
            raise AssetManifestError("unsupported_version", str(version))

        generated_at = ""
        if "generated_at" in payload:
            generated_at = _validate_generated_at(payload["generated_at"])

        catalog_sha256 = ""
        if "catalog_sha256" in payload:
            catalog_sha256 = _require_sha256(payload["catalog_sha256"],
                                            "manifest.catalog_sha256")

        license_data: dict[str, Any] | None = None
        if "license" in payload and payload["license"] is not None:
            license_data = _validate_license(payload["license"])

        self_use_candidate = False
        if "self_use_candidate" in payload:
            self_use_candidate = _require_bool(payload["self_use_candidate"],
                                               "manifest.self_use_candidate")

        declared_status = _require_str(
            _require_field(payload, "distribution_status", "manifest"),
            "manifest.distribution_status", max_len=64, allow_empty=False,
        )
        if declared_status not in _DISTRIBUTION_VALUES:
            raise AssetManifestError("invalid_distribution_status", declared_status)
        if declared_status == DISTRIBUTION_LICENSED and not _license_is_verified(license_data):
            raise AssetManifestError(
                "distribution_status_not_verified",
                "licensed_public_release requires a verified redistribution license",
            )

        cards_raw = _require_field(payload, "cards", "manifest")
        if not isinstance(cards_raw, list):
            raise AssetManifestError("invalid_type", "manifest.cards must be an array")
        if not cards_raw:
            raise AssetManifestError("invalid_type", "manifest.cards must not be empty")
        if len(cards_raw) > 5000:
            raise AssetManifestError("too_many_cards", str(len(cards_raw)))

        entries: list[CardAssetEntry] = []
        seen_ids: dict[str, str] = {}
        seen_paths: dict[str, str] = {}
        for index, item in enumerate(cards_raw):
            entry = _parse_card_entry(item, f"manifest.cards[{index}]")
            id_key = entry.card_id.casefold()
            if id_key in seen_ids:
                raise AssetManifestError(
                    "duplicate_card_id",
                    f"{entry.card_id!r} collides with {seen_ids[id_key]!r}",
                )
            seen_ids[id_key] = entry.card_id
            for spec in (entry.full, entry.thumb):
                path_key = spec.path.casefold()
                if path_key in seen_paths:
                    raise AssetManifestError(
                        "duplicate_asset_path",
                        f"{spec.path!r} collides with {seen_paths[path_key]!r}",
                    )
                seen_paths[path_key] = spec.path
            entries.append(entry)

        return cls(
            cards=tuple(entries),
            generated_at=generated_at,
            license=license_data,
            distribution_status=declared_status,
            self_use_candidate=self_use_candidate,
            catalog_sha256=catalog_sha256,
            schema=schema,
            version=version,
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "schema": self.schema,
            "version": self.version,
            "distribution_status": self.distribution_status,
            "self_use_candidate": self.self_use_candidate,
            "cards": [entry.to_dict() for entry in self.cards],
        }
        if self.generated_at:
            data["generated_at"] = self.generated_at
        if self.catalog_sha256:
            data["catalog_sha256"] = self.catalog_sha256
        if self.license is not None:
            data["license"] = dict(self.license)
        return data

    # ---------------------------------------------------------- queries

    @property
    def license_verified(self) -> bool:
        return _license_is_verified(self.license)

    @property
    def public_release_licensed(self) -> bool:
        return self.distribution_status == DISTRIBUTION_LICENSED and self.license_verified

    @property
    def card_ids(self) -> tuple[str, ...]:
        return tuple(entry.card_id for entry in self.cards)

    @property
    def by_card_id(self) -> dict[str, CardAssetEntry]:
        """card_id 索引；故意不提供 import_id 索引。"""
        return {entry.card_id: entry for entry in self.cards}

    def entry(self, card_id: str) -> CardAssetEntry | None:
        return self.by_card_id.get(card_id)


# ---------------------------------------------------------------- manifest 校验

def _require_field(mapping: Mapping[str, Any], name: str, where: str) -> Any:
    if name not in mapping:
        raise AssetManifestError("missing_field", f"{where}.{name}")
    return mapping[name]


def _check_keys(mapping: Mapping[str, Any], allowed: frozenset[str], where: str) -> None:
    for key in mapping:
        if not isinstance(key, str):
            raise AssetManifestError("invalid_type", f"{where} has a non-string key")
        if key not in allowed:
            raise AssetManifestError("unknown_field", f"{where}.{key}")


def _require_str(value: Any, where: str, *, max_len: int,
                 allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise AssetManifestError("invalid_type", f"{where} must be a string")
    if not allow_empty and not value:
        raise AssetManifestError("empty_value", where)
    if len(value) > max_len:
        raise AssetManifestError("too_long", f"{where} exceeds {max_len}")
    if any(ord(ch) < 32 for ch in value):
        raise AssetManifestError("invalid_metadata_string", where)
    return value


def _require_int(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AssetManifestError("invalid_type", f"{where} must be an integer")
    return value


def _require_bool(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise AssetManifestError("invalid_type", f"{where} must be a boolean")
    return value


def _require_sha256(value: Any, where: str) -> str:
    text = _require_str(value, where, max_len=64, allow_empty=False)
    if not _SHA256_RE.fullmatch(text):
        raise AssetManifestError("invalid_sha256", where)
    return text


def _validate_generated_at(value: Any) -> str:
    text = _require_str(value, "manifest.generated_at", max_len=64)
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AssetManifestError("invalid_generated_at", text) from exc
    return text


def _validate_license(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AssetManifestError("invalid_type", "manifest.license must be an object")
    payload = dict(value)
    _check_keys(payload, _LICENSE_KEYS, "manifest.license")
    name = _require_str(_require_field(payload, "name", "manifest.license"),
                        "manifest.license.name", max_len=256)
    verified = _require_bool(
        _require_field(payload, "verified", "manifest.license"),
        "manifest.license.verified",
    )
    redistribution_allowed = _require_bool(
        _require_field(payload, "redistribution_allowed", "manifest.license"),
        "manifest.license.redistribution_allowed",
    )
    evidence = _require_str(
        _require_field(payload, "evidence", "manifest.license"),
        "manifest.license.evidence", max_len=2048,
    )
    result: dict[str, Any] = {
        "name": name,
        "verified": verified,
        "redistribution_allowed": redistribution_allowed,
        "evidence": evidence,
    }
    if "url" in payload:
        result["url"] = _require_str(payload["url"], "manifest.license.url",
                                     max_len=2048, allow_empty=True)
    return result


def _license_is_verified(license_data: Mapping[str, Any] | None) -> bool:
    if not isinstance(license_data, Mapping):
        return False
    return bool(
        license_data.get("verified") is True
        and license_data.get("redistribution_allowed") is True
        and isinstance(license_data.get("name"), str)
        and license_data["name"].strip()
        and isinstance(license_data.get("evidence"), str)
        and license_data["evidence"].strip()
    )


def _parse_card_entry(value: Any, where: str) -> CardAssetEntry:
    if not isinstance(value, Mapping):
        raise AssetManifestError("invalid_type", f"{where} must be an object")
    payload = dict(value)
    _check_keys(payload, _ENTRY_KEYS, where)
    card_id = _validate_card_id(_require_field(payload, "card_id", where), f"{where}.card_id")
    full = _parse_variant(_require_field(payload, "full", where), f"{where}.full")
    thumb = _parse_variant(_require_field(payload, "thumb", where), f"{where}.thumb")
    source = _parse_source(_require_field(payload, "source", where), f"{where}.source")
    if full.path.casefold() == thumb.path.casefold():
        raise AssetManifestError(
            "duplicate_asset_path",
            f"{where}.full and thumb point to the same path",
        )
    return CardAssetEntry(card_id=card_id, full=full, thumb=thumb, source=source)


def _parse_variant(value: Any, where: str) -> AssetVariantSpec:
    if not isinstance(value, Mapping):
        raise AssetManifestError("invalid_type", f"{where} must be an object")
    payload = dict(value)
    _check_keys(payload, _VARIANT_KEYS, where)
    path = _validate_asset_relpath(_require_field(payload, "path", where), f"{where}.path")
    sha256 = _require_sha256(_require_field(payload, "sha256", where), f"{where}.sha256")
    byte_count = _require_int(_require_field(payload, "bytes", where), f"{where}.bytes")
    if byte_count < 16 or byte_count > ASSET_MAX_BYTES:
        raise AssetManifestError("invalid_bytes", f"{where}.bytes={byte_count}")
    file_format = _require_str(_require_field(payload, "format", where),
                               f"{where}.format", max_len=16)
    if file_format != ASSET_FORMAT_AVIF:
        raise AssetManifestError("invalid_format", f"{where}.format={file_format}")
    return AssetVariantSpec(path=path, sha256=sha256, bytes=byte_count,
                            format=file_format)


def _parse_source(value: Any, where: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise AssetManifestError("invalid_type", f"{where} must be an object")
    payload = dict(value)
    _check_keys(payload, _SOURCE_KEYS, where)
    return {
        "image_url": _validate_https_source(
            _require_field(payload, "image_url", where), f"{where}.image_url"),
        "thumb_url": _validate_https_source(
            _require_field(payload, "thumb_url", where), f"{where}.thumb_url"),
    }


def _validate_card_id(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise AssetManifestError("invalid_type", f"{where} must be a non-empty string")
    text = value
    if text != text.strip():
        raise AssetManifestError("unsafe_card_id", where)
    if len(text) > 128:
        raise AssetManifestError("unsafe_card_id", f"{where} is too long")
    if not _CARD_ID_RE.fullmatch(text):
        raise AssetManifestError("unsafe_card_id", where)
    if text in {".", ".."} or text.rsplit(".", 1)[0].upper() in _WINDOWS_RESERVED:
        raise AssetManifestError("unsafe_card_id", where)
    return text


def _validate_asset_relpath(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise AssetManifestError("invalid_type", f"{where} must be a non-empty string")
    text = value
    if text != text.strip():
        raise AssetManifestError("unsafe_asset_path", where)
    if len(text) > 512:
        raise AssetManifestError("unsafe_asset_path", f"{where} is too long")
    if "\\" in text or ":" in text or text.startswith("/"):
        raise AssetManifestError("unsafe_asset_path", where)
    if any(ord(ch) < 32 for ch in text):
        raise AssetManifestError("unsafe_asset_path", where)
    parts = text.split("/")
    if any(not _PATH_SEGMENT_RE.fullmatch(part) for part in parts):
        raise AssetManifestError("unsafe_asset_path", where)
    for part in parts:
        if part.rsplit(".", 1)[0].upper() in _WINDOWS_RESERVED:
            raise AssetManifestError("unsafe_asset_path", where)
    if not text.endswith("." + ASSET_FORMAT_AVIF):
        raise AssetManifestError("unsafe_asset_path", f"{where} must end with .avif")
    return text


def _validate_https_source(value: Any, where: str) -> str:
    text = _require_str(value, where, max_len=2048)
    if text != text.strip():
        raise AssetManifestError("invalid_source_url", where)
    if any(ch.isspace() for ch in text):
        raise AssetManifestError("invalid_source_url", where)
    if not text.startswith("https://"):
        raise AssetManifestError("invalid_source_url", "only https URLs are metadata")
    remainder = text[len("https://"):]
    authority = re.split(r"[/?#]", remainder, maxsplit=1)[0]
    if not authority or "@" in authority:
        raise AssetManifestError("invalid_source_url", where)
    if authority.startswith("["):
        end = authority.find("]")
        if end < 0:
            raise AssetManifestError("invalid_source_url", where)
        host = authority[1:end]
        port = authority[end + 1:]
        if port and not port.startswith(":"):
            raise AssetManifestError("invalid_source_url", where)
        port = port[1:] if port else ""
    else:
        if authority.count(":") > 1:
            raise AssetManifestError("invalid_source_url", where)
        host, _, port = authority.partition(":")
    if not host:
        raise AssetManifestError("invalid_source_url", where)
    if port and (not port.isdigit() or not 0 < int(port) < 65536):
        raise AssetManifestError("invalid_source_url", where)
    lowered_host = host.lower()
    if lowered_host in {"localhost", "localhost.localdomain"}:
        raise AssetManifestError("invalid_source_url", "localhost is not public metadata")
    try:
        address = ipaddress.ip_address(lowered_host)
    except ValueError:
        pass
    else:
        if (address.is_private or address.is_loopback or address.is_link_local
                or address.is_reserved or address.is_multicast
                or address.is_unspecified):
            raise AssetManifestError("invalid_source_url", "private address metadata")
    return text


def _reject_embedded_payload(value: Any, where: str = "$") -> None:
    """拒绝图片字节/base64 载荷与密钥字段，避免清单夹带可执行内容。"""
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise AssetManifestError("invalid_type", f"{where} has a non-string key")
            lowered = key.lower()
            if (lowered in _FORBIDDEN_PAYLOAD_KEYS
                    or lowered.endswith(_FORBIDDEN_KEY_SUFFIXES)):
                raise AssetManifestError("payload_field_forbidden", f"{where}.{key}")
            _reject_embedded_payload(item, f"{where}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_embedded_payload(item, f"{where}[{index}]")
    elif isinstance(value, str):
        if len(value) > _METADATA_MAX_STRING:
            raise AssetManifestError("metadata_string_too_long", where)
    elif isinstance(value, (bytes, bytearray, memoryview)):
        raise AssetManifestError("payload_field_forbidden", where)


# ---------------------------------------------------------------- 内置文本快照

@dataclass(frozen=True)
class _BuiltinCatalog:
    refs: tuple[BuiltinCardRef, ...]
    version: int
    sha256: str


def _load_builtin_catalog(catalog_file: str | os.PathLike[str] | None = None) -> _BuiltinCatalog:
    path = Path(catalog_file) if catalog_file is not None else BUILTIN_CARDS_FILE
    try:
        info = os.stat(path, follow_symlinks=False)
    except FileNotFoundError as exc:
        raise AssetManifestError("catalog_missing", str(path)) from exc
    except OSError as exc:
        raise AssetManifestError("catalog_unreadable", str(exc)) from exc
    if not stat.S_ISREG(info.st_mode):
        raise AssetManifestError("catalog_not_regular", str(path))
    if info.st_size > CATALOG_MAX_BYTES:
        raise AssetManifestError("catalog_too_large", str(info.st_size))
    try:
        raw = _read_local_bytes(path, CATALOG_MAX_BYTES)
    except ValueError as exc:
        raise AssetManifestError("catalog_too_large", str(exc)) from exc
    except OSError as exc:
        raise AssetManifestError("catalog_unreadable", str(exc)) from exc
    digest = hashlib.sha256(raw).hexdigest()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssetManifestError("catalog_invalid_json", str(exc)) from exc
    if not isinstance(data, Mapping):
        raise AssetManifestError("catalog_invalid", "catalog must be an object")
    cards = data.get("cards")
    if not isinstance(cards, list) or not cards:
        raise AssetManifestError("catalog_invalid", "catalog.cards must be a non-empty array")
    version = data.get("version", 1)
    if isinstance(version, bool) or not isinstance(version, int):
        raise AssetManifestError("catalog_invalid", "catalog.version must be an integer")

    refs: list[BuiltinCardRef] = []
    seen: set[str] = set()
    for index, item in enumerate(cards):
        if not isinstance(item, Mapping):
            raise AssetManifestError("catalog_invalid", f"cards[{index}] must be an object")
        card_id = _validate_card_id(item.get("card_id"), f"catalog.cards[{index}].card_id")
        key = card_id.casefold()
        if key in seen:
            raise AssetManifestError("catalog_invalid", f"duplicate card_id {card_id!r}")
        seen.add(key)
        name = item.get("name")
        image_url = item.get("image_url")
        thumb_url = item.get("thumb_url")
        if isinstance(name, str):
            name = name.strip()
            if len(name) > 256 or any(ord(ch) < 32 for ch in name):
                raise AssetManifestError("catalog_invalid",
                                         f"cards[{index}].name is not safe metadata")
        else:
            name = ""
        refs.append(BuiltinCardRef(
            card_id=card_id,
            name=name,
            image_url=image_url if isinstance(image_url, str) else "",
            thumb_url=thumb_url if isinstance(thumb_url, str) else "",
        ))
    return _BuiltinCatalog(refs=tuple(refs), version=version, sha256=digest)


def load_builtin_card_refs(
    catalog_file: str | os.PathLike[str] | None = None,
) -> tuple[BuiltinCardRef, ...]:
    """只读内置快照的 card_id/名称/两张来源 URL；不访问网络。"""
    return _load_builtin_catalog(catalog_file).refs


def builtin_card_ids(
    catalog_file: str | os.PathLike[str] | None = None,
) -> tuple[str, ...]:
    """当前内置快照的 card_id 序列（现有快照为 1083 条）。"""
    return tuple(ref.card_id for ref in load_builtin_card_refs(catalog_file))


def build_pending_worklist(
    catalog_file: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """从内置快照生成待构建清单：1083 个 card_id + 全图/缩略来源 URL。

    返回的是**构建工作单**（schema ``kards.card-assets.worklist``），不是可直接
    部署的最终 manifest：它没有 sha256/bytes，也没有图片字节；来源 URL 只是
    元数据，调用方不得据此自动下载。公开授权默认阻断，自用候选单独标记。
    """
    path = Path(catalog_file) if catalog_file is not None else BUILTIN_CARDS_FILE
    catalog = _load_builtin_catalog(path)
    cards: list[dict[str, Any]] = []
    for ref in catalog.refs:
        if not ref.image_url or not ref.thumb_url:
            raise AssetManifestError("catalog_source_url_missing", ref.card_id)
        image_url = _validate_https_source(ref.image_url, f"catalog.{ref.card_id}.image_url")
        thumb_url = _validate_https_source(ref.thumb_url, f"catalog.{ref.card_id}.thumb_url")
        cards.append({
            "card_id": ref.card_id,
            "name": ref.name,
            "full_url": image_url,
            "thumb_url": thumb_url,
            "suggested_full_path": f"full/{ref.card_id}.{ASSET_FORMAT_AVIF}",
            "suggested_thumb_path": f"thumb/{ref.card_id}.{ASSET_FORMAT_AVIF}",
        })
    worklist = {
        "schema": WORKLIST_SCHEMA,
        "version": WORKLIST_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_catalog": ("knowledge/cards_builtin.json"
                           if path.name == BUILTIN_CARDS_FILE.name
                           else path.name),
        "catalog_version": catalog.version,
        "catalog_sha256": catalog.sha256,
        "asset_format": ASSET_FORMAT_AVIF,
        "expected_card_count": len(cards),
        "distribution_status": DISTRIBUTION_BLOCKED,
        "self_use_candidate": True,
        "cards": cards,
    }
    _reject_embedded_payload(worklist)
    return worklist


# ---------------------------------------------------------------- 文件系统安全

def _is_link_like(path: Path) -> bool:
    """symlink 或 Windows junction/reparse point 都视为链接。"""
    try:
        info = os.lstat(path)
    except (OSError, ValueError):
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    if os.name == "nt":
        attributes = getattr(info, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        if attributes & reparse_flag:
            return True
    return False


def _absolute_no_resolve(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _safe_local_path(root: Path, relative: str) -> Path:
    """把清单相对路径绑定到安全根；拒绝穿越与任何 symlink/junction 组件。"""
    if _is_link_like(root):
        raise AssetRootError("root_symlink_or_junction", str(root))
    text = relative.replace("\\", "/") if os.name == "nt" else relative
    if not text or text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        raise AssetRootError("unsafe_relative_path", relative)
    parts = text.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise AssetRootError("unsafe_relative_path", relative)
    root_abs = _absolute_no_resolve(root)
    candidate = Path(os.path.abspath(os.path.join(os.fspath(root_abs), *parts)))
    try:
        common = os.path.commonpath([os.fspath(root_abs), os.fspath(candidate)])
    except ValueError as exc:
        raise AssetRootError("path_escapes_root", relative) from exc
    if os.path.normcase(common) != os.path.normcase(os.fspath(root_abs)):
        raise AssetRootError("path_escapes_root", relative)
    current = root_abs
    for part in parts:
        current = current / part
        if _is_link_like(current):
            raise AssetRootError("path_symlink_or_junction", relative)
    return candidate


def _read_local_bytes(path: Path, limit: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if nofollow:
        flags |= nofollow
    descriptor = os.open(os.fspath(path), flags)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise OSError(f"not a regular file: {path}")
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
    finally:
        os.close(descriptor)
    if len(data) > limit:
        raise ValueError("file too large")
    return data


def _is_avif(data: bytes) -> bool:
    """最小 AVIF 容器魔数：ISO BMFF ftyp，主品牌或兼容品牌含 avif/avis。"""
    if len(data) < 16 or data[4:8] != b"ftyp":
        return False
    box_size = int.from_bytes(data[0:4], "big")
    if box_size < 16 or box_size > len(data):
        return False
    major_brand = data[8:12]
    if major_brand in (b"avif", b"avis"):
        return True
    brands = data[16:min(box_size, len(data))]
    return any(brands[offset:offset + 4] in (b"avif", b"avis")
               for offset in range(0, max(0, len(brands) - 3), 4))


def _variant_name(value: str) -> str:
    if value not in {"full", "thumb"}:
        raise ValueError("variant must be 'full' or 'thumb'")
    return value


def _pil_available() -> bool:
    try:
        return importlib.util.find_spec("PIL") is not None
    except (ImportError, AttributeError, ValueError):
        return False


# ---------------------------------------------------------------- 索引状态

@dataclass(frozen=True)
class AssetSlotState:
    variant: str
    state: str
    reason: str = ""
    path: Path | None = None
    sha256: str = ""
    expected_bytes: int = 0


@dataclass(frozen=True)
class CardAssetStatus:
    card_id: str
    state: str
    full: AssetSlotState
    thumb: AssetSlotState
    source: dict[str, str] = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        return self.state in {STATE_READY, STATE_UNVERIFIED, STATE_PARTIAL}

    def slot(self, variant: str) -> AssetSlotState:
        return self.full if _variant_name(variant) == "full" else self.thumb


@dataclass(frozen=True)
class AssetCoverage:
    expected: int
    manifest: int
    missing: tuple[str, ...]
    unknown: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return (self.expected > 0 and self.manifest == self.expected
                and not self.missing and not self.unknown)

    def to_dict(self) -> dict[str, Any]:
        return {
            "expected": self.expected,
            "manifest": self.manifest,
            "missing": list(self.missing),
            "unknown": list(self.unknown),
            "complete": self.complete,
        }


@dataclass(frozen=True)
class MatcherRecord:
    """供 CardMatcher 构建使用的元数据记录；不包含图片字节。"""

    card_id: str
    name: str
    variant: str
    path: Path
    sha256: str
    bytes: int
    source_url: str = ""


@dataclass(frozen=True)
class MatcherBuildState:
    state: str
    records: tuple[MatcherRecord, ...]
    reason: str = ""
    pil_available: bool = True
    requested: int = 0
    missing: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.state in {STATE_READY, STATE_PARTIAL, STATE_UNVERIFIED}

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "reason": self.reason,
            "pil_available": self.pil_available,
            "requested": self.requested,
            "record_count": len(self.records),
            "missing": list(self.missing),
        }


# ---------------------------------------------------------------- 离线索引

class OfflineCardAssetIndex:
    """包内卡图离线索引：严格校验 manifest，按 card_id（而非 import_id）索引。

    构造不会因缺资源抛异常；缺 ``assets/cards``、manifest 或结构不合法时
    ``state == "blocked"`` 并给出 ``blocked_reason``。manifest 合法但文件缺失/
    损坏/覆盖不全时 ``state == "partial"``，每一张卡/每个变体都有明确状态。
    图片字节只在 ``load_image_bytes`` 时从本地读取并复核。
    """

    def __init__(
        self,
        assets_root: str | os.PathLike[str] | None = None,
        *,
        manifest_path: str | os.PathLike[str] | None = None,
        expected_card_ids: Iterable[str] | None = None,
        catalog_file: str | os.PathLike[str] | None = None,
        verify_files: bool = True,
    ) -> None:
        self._root = _absolute_no_resolve(
            Path(assets_root) if assets_root is not None else DEFAULT_ASSETS_ROOT
        )
        self._manifest_path_input = (
            Path(manifest_path) if manifest_path is not None else None
        )
        self._catalog_file = (
            Path(catalog_file) if catalog_file is not None else BUILTIN_CARDS_FILE
        )
        if isinstance(expected_card_ids, str):
            expected_card_ids = (expected_card_ids,)
        self._expected_input = (
            tuple(expected_card_ids) if expected_card_ids is not None else None
        )
        self._verify_files = bool(verify_files)

        self._manifest_path: Path | None = None
        self._manifest: CardAssetManifest | None = None
        self._entries: dict[str, CardAssetEntry] = {}
        self._statuses: dict[str, CardAssetStatus] = {}
        self._expected: tuple[str, ...] = ()
        self._names: dict[str, str] = {}
        self._coverage = AssetCoverage(expected=0, manifest=0, missing=(), unknown=())
        self._state = STATE_BLOCKED
        self._blocked_reason = ""
        self._problems: list[str] = []
        self._load()

    # ---------------------------------------------------------- load

    def _block(self, reason: str, detail: str = "") -> None:
        self._state = STATE_BLOCKED
        self._blocked_reason = reason
        if detail:
            self._problems.append(f"{reason}: {detail}")

    def _load(self) -> None:
        root = self._root
        if _is_link_like(root):
            self._block("assets_dir_unsafe", str(root))
            return
        try:
            info = os.stat(root, follow_symlinks=False)
        except FileNotFoundError:
            self._block("assets_dir_missing", str(root))
            return
        except OSError as exc:
            self._block("assets_dir_unreadable", str(exc))
            return
        if not stat.S_ISDIR(info.st_mode):
            self._block("assets_dir_not_directory", str(root))
            return

        try:
            manifest_path = self._resolve_manifest_path()
        except AssetRootError as exc:
            self._block("manifest_unsafe", str(exc))
            return
        self._manifest_path = manifest_path

        try:
            manifest = CardAssetManifest.from_path(manifest_path)
        except AssetManifestError as exc:
            self._block(f"manifest_invalid:{exc.reason}", exc.detail)
            return
        self._manifest = manifest
        self._entries = manifest.by_card_id

        try:
            if self._expected_input is None:
                catalog = _load_builtin_catalog(self._catalog_file)
                self._expected = tuple(ref.card_id for ref in catalog.refs)
                self._names = {ref.card_id: ref.name for ref in catalog.refs}
            else:
                self._expected = self._normalize_expected(self._expected_input)
        except AssetManifestError as exc:
            self._block(f"catalog_invalid:{exc.reason}", exc.detail)
            return

        expected_set = set(self._expected)
        manifest_ids = manifest.card_ids
        manifest_set = set(manifest_ids)
        self._coverage = AssetCoverage(
            expected=len(self._expected),
            manifest=len(manifest_ids),
            missing=tuple(cid for cid in self._expected if cid not in manifest_set),
            unknown=tuple(cid for cid in manifest_ids if cid not in expected_set),
        )

        for entry in manifest.cards:
            self._statuses[entry.card_id] = self._inspect_card(entry)

        self._state, self._blocked_reason = self._compute_state()

    def _resolve_manifest_path(self) -> Path:
        if self._manifest_path_input is None:
            return _safe_local_path(self._root, MANIFEST_FILENAME)
        candidate = _absolute_no_resolve(self._manifest_path_input)
        try:
            relative = os.path.relpath(os.fspath(candidate), os.fspath(self._root))
        except ValueError as exc:
            raise AssetRootError("manifest_outside_root", str(candidate)) from exc
        relative = relative.replace(os.sep, "/")
        if relative in {".", ".."} or relative.startswith("../"):
            raise AssetRootError("manifest_outside_root", str(candidate))
        return _safe_local_path(self._root, relative)

    @staticmethod
    def _normalize_expected(values: Iterable[str]) -> tuple[str, ...]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            card_id = _validate_card_id(value, "expected_card_ids")
            key = card_id.casefold()
            if key in seen:
                raise AssetManifestError("duplicate_card_id", f"expected:{card_id}")
            seen.add(key)
            result.append(card_id)
        return tuple(result)

    def _inspect_card(self, entry: CardAssetEntry) -> CardAssetStatus:
        full = self._inspect_slot(entry.card_id, "full", entry.full)
        thumb = self._inspect_slot(entry.card_id, "thumb", entry.thumb)
        return CardAssetStatus(
            card_id=entry.card_id,
            state=_combine_slot_states(full.state, thumb.state),
            full=full,
            thumb=thumb,
            source=dict(entry.source),
        )

    def _inspect_slot(self, card_id: str, variant: str,
                      spec: AssetVariantSpec) -> AssetSlotState:
        try:
            path = _safe_local_path(self._root, spec.path)
        except AssetRootError as exc:
            return AssetSlotState(variant, SLOT_UNSAFE, str(exc), None,
                                  spec.sha256, spec.bytes)
        try:
            info = os.stat(path, follow_symlinks=False)
        except FileNotFoundError:
            return AssetSlotState(variant, SLOT_MISSING, "file_not_found", path,
                                  spec.sha256, spec.bytes)
        except OSError as exc:
            return AssetSlotState(variant, SLOT_UNSAFE, f"stat_failed:{exc}", path,
                                  spec.sha256, spec.bytes)
        if stat.S_ISLNK(info.st_mode) or _is_link_like(path):
            return AssetSlotState(variant, SLOT_UNSAFE, "symlink_or_junction", path,
                                  spec.sha256, spec.bytes)
        if not stat.S_ISREG(info.st_mode):
            return AssetSlotState(variant, SLOT_UNSAFE, "not_a_regular_file", path,
                                  spec.sha256, spec.bytes)
        if info.st_size != spec.bytes:
            return AssetSlotState(variant, SLOT_CORRUPT, "bytes_mismatch", path,
                                  spec.sha256, spec.bytes)
        if not self._verify_files:
            return AssetSlotState(variant, SLOT_UNVERIFIED, "verification_disabled", path,
                                  spec.sha256, spec.bytes)
        try:
            data = _read_local_bytes(path, ASSET_MAX_BYTES)
        except ValueError:
            return AssetSlotState(variant, SLOT_CORRUPT, "bytes_mismatch", path,
                                  spec.sha256, spec.bytes)
        except OSError as exc:
            return AssetSlotState(variant, SLOT_UNSAFE, f"read_failed:{exc}", path,
                                  spec.sha256, spec.bytes)
        if len(data) != spec.bytes:
            return AssetSlotState(variant, SLOT_CORRUPT, "bytes_mismatch", path,
                                  spec.sha256, spec.bytes)
        if hashlib.sha256(data).hexdigest() != spec.sha256:
            return AssetSlotState(variant, SLOT_CORRUPT, "sha256_mismatch", path,
                                  spec.sha256, spec.bytes)
        if not _is_avif(data):
            return AssetSlotState(variant, SLOT_CORRUPT, "avif_magic_mismatch", path,
                                  spec.sha256, spec.bytes)
        return AssetSlotState(variant, SLOT_READY, "", path, spec.sha256, spec.bytes)

    def _compute_state(self) -> tuple[str, str]:
        if not self._manifest or not self._statuses:
            return STATE_BLOCKED, "no_assets"
        states = [status.state for status in self._statuses.values()]
        usable = any(state in {STATE_READY, STATE_UNVERIFIED} for state in states)
        if not usable:
            return STATE_BLOCKED, "no_usable_assets"
        if self._coverage.complete and all(state == STATE_READY for state in states):
            return STATE_READY, ""
        if self._coverage.complete and all(state == STATE_UNVERIFIED for state in states):
            return STATE_UNVERIFIED, ""
        return STATE_PARTIAL, ""

    # ---------------------------------------------------------- state

    @property
    def assets_root(self) -> Path:
        return self._root

    @property
    def manifest_path(self) -> Path | None:
        return self._manifest_path

    @property
    def manifest(self) -> CardAssetManifest | None:
        return self._manifest

    @property
    def manifest_loaded(self) -> bool:
        return self._manifest is not None

    @property
    def expected_card_ids(self) -> tuple[str, ...]:
        return self._expected

    @property
    def card_ids(self) -> tuple[str, ...]:
        return self._manifest.card_ids if self._manifest else ()

    @property
    def coverage(self) -> AssetCoverage:
        return self._coverage

    @property
    def state(self) -> str:
        return self._state

    @property
    def blocked_reason(self) -> str:
        return self._blocked_reason

    @property
    def problems(self) -> tuple[str, ...]:
        return tuple(self._problems)

    @property
    def distribution_status(self) -> str:
        return (self._manifest.distribution_status
                if self._manifest else DISTRIBUTION_BLOCKED)

    @property
    def self_use_candidate(self) -> bool:
        return bool(self._manifest and self._manifest.self_use_candidate)

    @property
    def license_verified(self) -> bool:
        return bool(self._manifest and self._manifest.license_verified)

    def __contains__(self, card_id: object) -> bool:
        return isinstance(card_id, str) and card_id in self._statuses

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self):
        return iter(self.card_ids)

    def status(self, card_id: str) -> CardAssetStatus | None:
        return self._statuses.get(card_id)

    def entry(self, card_id: str) -> CardAssetEntry | None:
        return self._entries.get(card_id)

    def card_statuses(self) -> tuple[CardAssetStatus, ...]:
        return tuple(self._statuses.get(card_id) for card_id in self.card_ids
                     if card_id in self._statuses)

    @property
    def verified_card_ids(self) -> tuple[str, ...]:
        return tuple(card_id for card_id in self.card_ids
                     if self._statuses[card_id].state == STATE_READY)

    @property
    def missing_card_ids(self) -> tuple[str, ...]:
        expected_set = set(self._expected)
        result: list[str] = []
        for card_id in self._expected:
            status = self._statuses.get(card_id)
            if status is None or status.state not in {STATE_READY, STATE_UNVERIFIED}:
                result.append(card_id)
        for card_id in self.card_ids:
            if card_id in expected_set:
                continue
            status = self._statuses[card_id]
            if status.state not in {STATE_READY, STATE_UNVERIFIED}:
                result.append(card_id)
        return tuple(dict.fromkeys(result))

    @property
    def corrupt_card_ids(self) -> tuple[str, ...]:
        return tuple(card_id for card_id in self.card_ids
                     if self._statuses[card_id].state == SLOT_CORRUPT)

    @property
    def unsafe_card_ids(self) -> tuple[str, ...]:
        return tuple(card_id for card_id in self.card_ids
                     if self._statuses[card_id].state == SLOT_UNSAFE)

    # ---------------------------------------------------------- image access

    def load_image_bytes(self, card_id: str, *, variant: str = "thumb") -> bytes:
        """只从本地读取并复核 bytes/sha256/AVIF magic；绝不访问网络。"""
        variant = _variant_name(variant)
        if self._manifest is None or not self._statuses:
            raise AssetBlockedError(self._blocked_reason or "manifest_unavailable")
        entry = self._entries.get(card_id)
        if entry is None:
            raise AssetNotFoundError("card_asset_not_indexed", str(card_id))
        status = self._statuses.get(card_id)
        if status is None:
            raise AssetNotFoundError("card_asset_not_indexed", str(card_id))
        slot = status.full if variant == "full" else status.thumb
        if slot.state == SLOT_MISSING:
            raise AssetNotFoundError("asset_missing", f"{card_id}:{variant}")
        if slot.state == SLOT_UNSAFE:
            raise AssetNotFoundError("asset_unsafe", f"{card_id}:{variant}:{slot.reason}")
        if slot.state == SLOT_CORRUPT:
            raise AssetIntegrityError("asset_corrupt", f"{card_id}:{variant}:{slot.reason}")
        spec = entry.full if variant == "full" else entry.thumb
        try:
            path = _safe_local_path(self._root, spec.path)
        except AssetRootError as exc:
            raise AssetNotFoundError("asset_unsafe", f"{card_id}:{variant}:{exc}") from exc
        try:
            data = _read_local_bytes(path, ASSET_MAX_BYTES)
        except FileNotFoundError as exc:
            raise AssetNotFoundError("asset_missing", f"{card_id}:{variant}") from exc
        except ValueError as exc:
            raise AssetIntegrityError("bytes_mismatch", f"{card_id}:{variant}") from exc
        except OSError as exc:
            raise AssetIntegrityError("asset_unreadable", f"{card_id}:{variant}:{exc}") from exc
        if len(data) != spec.bytes:
            raise AssetIntegrityError("bytes_mismatch", f"{card_id}:{variant}")
        if hashlib.sha256(data).hexdigest() != spec.sha256:
            raise AssetIntegrityError("sha256_mismatch", f"{card_id}:{variant}")
        if not _is_avif(data):
            raise AssetIntegrityError("avif_magic_mismatch", f"{card_id}:{variant}")
        return data

    # ---------------------------------------------------------- matcher

    def matcher_records(
        self,
        *,
        variant: str = "thumb",
        include_unverified: bool = False,
        names: Mapping[str, str] | None = None,
    ) -> tuple[MatcherRecord, ...]:
        """返回按 card_id（不是 import_id）排序的 matcher 元数据记录。"""
        variant = _variant_name(variant)
        if self._manifest is None:
            return ()
        name_map = dict(self._names)
        if names:
            name_map.update({str(key): str(value) for key, value in names.items()})
        expected_set = set(self._expected)
        ordered_ids = list(self._expected)
        ordered_ids.extend(card_id for card_id in self.card_ids
                           if card_id not in expected_set)
        records: list[MatcherRecord] = []
        for card_id in ordered_ids:
            status = self._statuses.get(card_id)
            entry = self._entries.get(card_id)
            if status is None or entry is None:
                continue
            slot = status.full if variant == "full" else status.thumb
            available = slot.state == SLOT_READY or (
                include_unverified and slot.state == SLOT_UNVERIFIED)
            if not available or slot.path is None:
                continue
            source_url = (entry.source.get("image_url" if variant == "full"
                                           else "thumb_url", ""))
            records.append(MatcherRecord(
                card_id=card_id,
                name=name_map.get(card_id, card_id),
                variant=variant,
                path=slot.path,
                sha256=slot.sha256,
                bytes=slot.expected_bytes,
                source_url=source_url,
            ))
        return tuple(records)

    def build_matcher_records(
        self,
        *,
        variant: str = "thumb",
        require_pil: bool = False,
        include_unverified: bool = False,
        names: Mapping[str, str] | None = None,
    ) -> MatcherBuildState:
        """构建 matcher 记录状态；不导入 Pillow，缺 Pillow 时返回明确 state。

        ``require_pil=True`` 表示调用方准备把记录解码成图像；此时若 Pillow
        不可用，返回 ``pil_unavailable``（仍携带只读元数据记录），而不是抛异常。
        """
        variant = _variant_name(variant)
        records = self.matcher_records(
            variant=variant, include_unverified=include_unverified, names=names)
        pil_available = _pil_available()
        expected = self._expected or self.card_ids
        record_ids = {record.card_id for record in records}
        missing = tuple(card_id for card_id in expected if card_id not in record_ids)

        if not records:
            return MatcherBuildState(
                state=STATE_BLOCKED,
                records=(),
                reason=self._blocked_reason or "no_ready_assets",
                pil_available=pil_available,
                requested=len(expected),
                missing=missing,
            )
        if require_pil and not pil_available:
            return MatcherBuildState(
                state=MATCHER_PIL_UNAVAILABLE,
                records=records,
                reason="Pillow is not available; returning metadata-only records",
                pil_available=False,
                requested=len(expected),
                missing=missing,
            )
        if self._state == STATE_READY:
            state = STATE_READY
        elif self._state == STATE_UNVERIFIED and include_unverified:
            state = STATE_UNVERIFIED
        else:
            state = STATE_PARTIAL
        return MatcherBuildState(
            state=state,
            records=records,
            reason=self._blocked_reason,
            pil_available=pil_available,
            requested=len(expected),
            missing=missing,
        )

    # ---------------------------------------------------------- diagnostics

    def describe(self) -> dict[str, Any]:
        """无密钥、无图片字节的紧凑状态（用于宿主状态面板）。"""
        statuses = [self._statuses[card_id] for card_id in self.card_ids
                    if card_id in self._statuses]
        return {
            "state": self._state,
            "blocked_reason": self._blocked_reason,
            "assets_root": str(self._root),
            "manifest_path": str(self._manifest_path) if self._manifest_path else "",
            "distribution_status": self.distribution_status,
            "self_use_candidate": self.self_use_candidate,
            "license_verified": self.license_verified,
            "public_release_licensed": bool(
                self._manifest and self._manifest.public_release_licensed),
            "expected": self._coverage.expected,
            "manifest_entries": self._coverage.manifest,
            "coverage_complete": self._coverage.complete,
            "coverage_missing": len(self._coverage.missing),
            "coverage_unknown": len(self._coverage.unknown),
            "ready": sum(1 for status in statuses if status.state == STATE_READY),
            "unverified": sum(1 for status in statuses
                              if status.state == STATE_UNVERIFIED),
            "missing": len(self.missing_card_ids),
            "corrupt": len(self.corrupt_card_ids),
            "unsafe": len(self.unsafe_card_ids),
        }


def _combine_slot_states(full_state: str, thumb_state: str) -> str:
    states = (full_state, thumb_state)
    if all(state == SLOT_READY for state in states):
        return SLOT_READY
    if all(state == SLOT_UNVERIFIED for state in states):
        return SLOT_UNVERIFIED
    if all(state == SLOT_MISSING for state in states):
        return SLOT_MISSING
    if any(state == SLOT_UNSAFE for state in states):
        return SLOT_UNSAFE
    if any(state == SLOT_CORRUPT for state in states):
        return SLOT_CORRUPT
    if any(state in {SLOT_READY, SLOT_UNVERIFIED} for state in states):
        return SLOT_PARTIAL
    return SLOT_MISSING
