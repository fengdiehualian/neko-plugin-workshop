"""Backup files live only below the SDK data directory, never the static UI."""

from __future__ import annotations

import os
import re
import time
import uuid
from pathlib import Path

from .backup import MAX_BYTES, BackupError
from .media_files import MediaFileError, _check_path, _open_file


def root_for(plugin) -> Path:
    try:
        base = Path(plugin.data_path())
        root = Path(plugin.data_path("backups"))
        _check_path(base)
        _check_path(root)
        if not base.is_absolute() or root.resolve() != base.resolve() / "backups":
            raise BackupError("backup_path_unavailable")
        for attr in ("plugin_dir", "config_dir"):
            code = getattr(plugin, attr, None)
            if code is not None and (base.resolve() == Path(code).resolve() or Path(code).resolve() in base.resolve().parents):
                raise BackupError("backup_path_unavailable")
        root.mkdir(parents=True, exist_ok=True)
        _check_path(root)
        return root
    except (AttributeError, TypeError, ValueError, OSError, MediaFileError):
        raise BackupError("backup_path_unavailable") from None


def checked(root: Path, name: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", name) or name in (".", ".."):
        raise BackupError("backup_path_unavailable")
    path = root / name
    try:
        _check_path(root)
        _check_path(path)
        if path.resolve().parent != root.resolve():
            raise BackupError("backup_path_unavailable")
    except (MediaFileError, OSError):
        raise BackupError("backup_path_unavailable") from None
    return path


def write_atomic(root: Path, name: str, content: bytes) -> Path:
    target = checked(root, name)
    temporary = checked(root, f"write-{uuid.uuid4().hex}.part")
    try:
        with _open_file(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        checked(root, name)
        os.replace(temporary, target)
        return target
    except (MediaFileError, OSError):
        raise BackupError("backup_save_failed") from None
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def cleanup_imports(root: Path) -> None:
    for path in root.iterdir():
        if re.fullmatch(r"(?:import|write)-[a-f0-9]{32}\.part", path.name):
            safe = checked(root, path.name)
            if time.time() - safe.stat().st_mtime >= 30 * 60:
                remove(root, path.name)


def read(root: Path, name: str, limit: int = MAX_BYTES) -> bytes:
    try:
        path = checked(root, name)
        if path.stat().st_size > limit:
            raise BackupError("backup_too_large")
        with _open_file(path, os.O_RDONLY, "rb") as handle:
            content = handle.read(limit + 1)
        if len(content) > limit:
            raise BackupError("backup_too_large")
        return content
    except (MediaFileError, OSError):
        raise BackupError("backup_invalid") from None


def remove(root: Path, name: str) -> None:
    try:
        checked(root, name).unlink(missing_ok=True)
    except (MediaFileError, OSError):
        raise BackupError("backup_save_failed") from None


def append(root: Path, name: str, offset: int, content: bytes, total: int) -> int:
    path = checked(root, name)
    try:
        size = path.stat().st_size
        with _open_file(path, os.O_RDWR, "r+b") as handle:
            if size == offset:
                if size + len(content) > total:
                    raise BackupError("backup_upload_invalid")
                handle.seek(offset)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
                return offset + len(content)
            if size == offset + len(content):
                handle.seek(offset)
                if handle.read(len(content)) == content:
                    return size
        raise BackupError("backup_upload_invalid")
    except (MediaFileError, OSError):
        raise BackupError("backup_save_failed") from None
