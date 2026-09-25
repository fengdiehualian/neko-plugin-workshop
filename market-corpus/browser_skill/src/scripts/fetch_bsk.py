"""Materialize pinned BrowserSkill release assets for tests and packaging."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = REPOSITORY_ROOT / "bin" / "manifest.json"
DEFAULT_OUTPUT_ROOT = REPOSITORY_ROOT / "bin"
USER_AGENT = "N.E.K.O-browser-skill-build/1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or manifest.get("component") != "bsk":
        raise RuntimeError(f"unsupported BrowserSkill asset manifest: {path}")
    assets = manifest.get("assets")
    if not isinstance(assets, dict) or not assets:
        raise RuntimeError(f"BrowserSkill asset manifest has no assets: {path}")
    return manifest


def target_path(output_root: Path, asset: dict[str, Any]) -> Path:
    relative = Path(str(asset.get("path") or ""))
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise RuntimeError(f"unsafe BrowserSkill output path: {relative}")
    return output_root / relative


def matching_archive_member(archive: Path, basename: str) -> bytes:
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as handle:
            members = [
                item
                for item in handle.infolist()
                if not item.is_dir() and PurePosixPath(item.filename).name == basename
            ]
            if len(members) != 1:
                raise RuntimeError(
                    f"expected one {basename!r} in {archive.name}, found {len(members)}"
                )
            return handle.read(members[0])

    if archive.name.endswith(".tar.gz"):
        with tarfile.open(archive, mode="r:gz") as handle:
            members = [
                item
                for item in handle.getmembers()
                if item.isfile() and PurePosixPath(item.name).name == basename
            ]
            if len(members) != 1:
                raise RuntimeError(
                    f"expected one {basename!r} in {archive.name}, found {len(members)}"
                )
            extracted = handle.extractfile(members[0])
            if extracted is None:
                raise RuntimeError(f"could not read {members[0].name} from {archive.name}")
            return extracted.read()

    raise RuntimeError(f"unsupported BrowserSkill archive format: {archive.name}")


def download_asset(asset_key: str, asset: dict[str, Any], temporary_root: Path) -> bytes:
    archive_name = str(asset.get("archive") or "")
    url = str(asset.get("url") or "")
    archive_hash = str(asset.get("archive_sha256") or "").lower()
    executable_hash = str(asset.get("sha256") or "").lower()
    member_basename = str(asset.get("member_basename") or "")
    if not all((archive_name, url, archive_hash, executable_hash, member_basename)):
        raise RuntimeError(f"incomplete BrowserSkill asset manifest entry: {asset_key}")

    archive_path = temporary_root / archive_name
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    print(f"Downloading {asset_key}: {archive_name}")
    with urllib.request.urlopen(request, timeout=120) as response, archive_path.open("wb") as output:
        shutil.copyfileobj(response, output)

    actual_archive_hash = sha256_file(archive_path)
    if actual_archive_hash != archive_hash:
        raise RuntimeError(
            f"archive checksum mismatch for {asset_key}: "
            f"expected {archive_hash}, got {actual_archive_hash}"
        )

    executable = matching_archive_member(archive_path, member_basename)
    actual_executable_hash = sha256_bytes(executable)
    if actual_executable_hash != executable_hash:
        raise RuntimeError(
            f"executable checksum mismatch for {asset_key}: "
            f"expected {executable_hash}, got {actual_executable_hash}"
        )
    return executable


def install_atomically(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(data)
        if os.name != "nt":
            temporary.chmod(
                temporary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
            )
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def verify_materialized(manifest: dict[str, Any], output_root: Path) -> list[str]:
    errors: list[str] = []
    for asset_key, raw_asset in manifest["assets"].items():
        if not isinstance(raw_asset, dict):
            errors.append(f"{asset_key}: manifest entry is not an object")
            continue
        target = target_path(output_root, raw_asset)
        expected = str(raw_asset.get("sha256") or "").lower()
        if not target.is_file():
            errors.append(f"{asset_key}: missing {target}")
            continue
        actual = sha256_file(target)
        if actual != expected:
            errors.append(f"{asset_key}: expected {expected}, got {actual}")
    return errors


def materialize(manifest: dict[str, Any], output_root: Path) -> None:
    pending: dict[str, tuple[Path, dict[str, Any]]] = {}
    for asset_key, raw_asset in manifest["assets"].items():
        if not isinstance(raw_asset, dict):
            raise RuntimeError(f"manifest entry is not an object: {asset_key}")
        target = target_path(output_root, raw_asset)
        expected = str(raw_asset.get("sha256") or "").lower()
        if target.is_file() and sha256_file(target) == expected:
            print(f"Verified existing {asset_key}: {target}")
            continue
        pending[asset_key] = (target, raw_asset)

    if pending:
        downloaded: dict[str, bytes] = {}
        with tempfile.TemporaryDirectory(prefix="browser-skill-assets-") as temporary:
            temporary_root = Path(temporary)
            for asset_key, (_target, asset) in pending.items():
                downloaded[asset_key] = download_asset(asset_key, asset, temporary_root)
        for asset_key, (target, _asset) in pending.items():
            install_atomically(target, downloaded[asset_key])
            print(f"Installed {asset_key}: {target}")

    errors = verify_materialized(manifest, output_root)
    if errors:
        raise RuntimeError("BrowserSkill asset verification failed:\n" + "\n".join(errors))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download and verify pinned BrowserSkill CLI release assets."
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify existing files without downloading missing assets",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = load_manifest(args.manifest.resolve())
    output_root = args.output_root.resolve()
    if args.check:
        errors = verify_materialized(manifest, output_root)
        if errors:
            print("BrowserSkill asset verification failed:")
            for error in errors:
                print(f"- {error}")
            return 1
    else:
        materialize(manifest, output_root)
    print(
        f"BrowserSkill CLI {manifest['version']} assets verified "
        f"for {len(manifest['assets'])} platforms."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
