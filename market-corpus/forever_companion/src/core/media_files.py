"""Private wallpaper file storage primitives.

This module deliberately knows nothing about the host's HTTP/static serving
layer.  Files are addressed only by generated storage ids and are kept below
the SDK-provided plugin data directory.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
from pathlib import Path


class MediaFileError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# The media RPC layer historically exposed this name for validation failures.
GalleryMediaError = MediaFileError


_SID = re.compile(r"^[a-f0-9]{32}$")
_FILE_RE = re.compile(r"^[a-f0-9]{32}\.(?:part|bin)$")


def _is_reparse(path: Path) -> bool:
    """Return true for symlinks and Windows junction/reparse points."""
    if path.is_symlink():
        return True
    try:
        attrs = getattr(os.stat(path, follow_symlinks=False), "st_file_attributes", 0)
        return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    except FileNotFoundError:
        return False


def _check_path(path: Path) -> None:
    for part in (path, *path.parents):
        if _is_reparse(part):
            raise MediaFileError("video_path_unsafe")
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
        raise MediaFileError("video_path_unsafe")


def safe_wallpaper_root(plugin: object) -> Path:
    """Resolve ``self.data_path('wallpapers')`` and reject unsafe locations."""
    resolver = getattr(plugin, "data_path", None)
    if not callable(resolver):
        raise MediaFileError("video_path_unsafe")
    try:
        raw = Path(resolver("wallpapers"))
        try:
            base = Path(resolver())
        except TypeError:
            base = raw.parent
    except (TypeError, ValueError, OSError):
        raise MediaFileError("video_path_unsafe") from None
    try:
        if not raw.is_absolute() or not base.is_absolute():
            raise MediaFileError("video_path_unsafe")
        _check_path(base)
        _check_path(raw)
        # Check the lexical path before resolving: a junction in an existing
        # ancestor must never be followed even when the resolved target looks
        # like a normal SDK data directory.
        _check_path(base)
        _check_path(raw)
        base_resolved = base.resolve()
        root = raw.resolve()
    except OSError:
        raise MediaFileError("video_path_unsafe") from None
    # The data directory must not be the installed code/config directory.
    for attr in ("plugin_dir", "config_dir"):
        candidate = getattr(plugin, attr, None)
        if candidate is None:
            continue
        try:
            candidate_resolved = Path(candidate).resolve()
        except OSError:
            continue
        if root == candidate_resolved or candidate_resolved in root.parents:
            raise MediaFileError("video_path_unsafe")
    if root != base_resolved / "wallpapers":
        raise MediaFileError("video_path_unsafe")
    try:
        base_resolved.mkdir(parents=True, exist_ok=True)
        if raw.exists() and _is_reparse(raw):
            raise MediaFileError("video_path_unsafe")
        raw.mkdir(parents=True, exist_ok=True)
        if _is_reparse(raw):
            raise MediaFileError("video_path_unsafe")
    except MediaFileError:
        raise
    except OSError:
        raise MediaFileError("video_path_unsafe") from None
    return raw


def _check_sid(storage_id: str) -> None:
    if not isinstance(storage_id, str) or _SID.fullmatch(storage_id) is None:
        raise MediaFileError("video_path_unsafe")


def file_path(root: Path, storage_id: str, suffix: str) -> Path:
    _check_sid(storage_id)
    if suffix not in (".part", ".bin"):
        raise MediaFileError("video_path_unsafe")
    path = root / f"{storage_id}{suffix}"
    try:
        _check_path(root)
        _check_path(path)
        if path.exists() and _is_reparse(path):
            raise MediaFileError("video_path_unsafe")
        if path.resolve().parent != root.resolve():
            raise MediaFileError("video_path_unsafe")
    except MediaFileError:
        raise
    except OSError:
        raise MediaFileError("video_path_unsafe") from None
    return path


def _open_file(path: Path, flags: int, mode: str):
    _check_path(path)
    # Windows can strip a trailing Ctrl-Z during os.open in text mode, before
    # fdopen gets the chance to wrap the descriptor as a binary file.
    descriptor = os.open(
        path, flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0), 0o600,
    )
    try:
        if os.fstat(descriptor).st_nlink > 1:
            raise MediaFileError("video_path_unsafe")
        _check_path(path)
        return os.fdopen(descriptor, mode)
    except BaseException:
        os.close(descriptor)
        raise


def write_chunk(
    root: Path, storage_id: str, offset: int, payload: bytes, expected_size: int | None = None,
) -> None:
    if expected_size is None:
        expected_size = offset + len(payload)
    if type(offset) is not int or offset < 0 or type(expected_size) is not int or expected_size <= 0:
        raise MediaFileError("video_chunk_invalid")
    if not isinstance(payload, (bytes, bytearray)) or offset + len(payload) > expected_size:
        raise MediaFileError("video_chunk_invalid")
    path = file_path(root, storage_id, ".part")
    try:
        with _open_file(path, os.O_RDWR | os.O_CREAT, "r+b") as handle:
            handle.seek(offset)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except MediaFileError:
        raise
    except OSError:
        raise MediaFileError("video_save_failed") from None


def commit_file(root: Path, storage_id: str, expected_size: int, *_ignored: object) -> Path:
    part = file_path(root, storage_id, ".part")
    target = file_path(root, storage_id, ".bin")
    try:
        if not part.is_file() or _is_reparse(part) or part.stat().st_size != expected_size:
            raise MediaFileError("video_upload_incomplete")
        with _open_file(part, os.O_RDWR, "r+b") as handle:
            os.fsync(handle.fileno())
        os.replace(part, target)
        # Persist the directory entry where the platform supports it.
        try:
            directory_fd = os.open(str(root), os.O_RDONLY)
        except OSError:
            directory_fd = -1
        if directory_fd >= 0:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except MediaFileError:
        raise
    except OSError:
        raise MediaFileError("video_save_failed") from None
    return target


def read_chunk(
    root: Path, storage_id: str, offset: int, length: int, expected_size: int | None = None,
    *, published: bool = True,
) -> bytes:
    _check_sid(storage_id)
    if expected_size is None:
        from .state import _GALLERY_VIDEO_CHUNK_BYTES
        expected_size, number = offset, length
        offset = number * _GALLERY_VIDEO_CHUNK_BYTES
        length = min(_GALLERY_VIDEO_CHUNK_BYTES, expected_size - offset)
    if type(offset) is not int or type(length) is not int or offset < 0 or length <= 0:
        raise MediaFileError("video_chunk_invalid")
    path = file_path(root, storage_id, ".bin" if published else ".part")
    try:
        if not path.is_file() or _is_reparse(path) or path.stat().st_size != expected_size:
            raise MediaFileError("video_read_failed")
        with _open_file(path, os.O_RDONLY, "rb") as handle:
            handle.seek(offset)
            value = handle.read(length)
        if len(value) != min(length, expected_size - offset):
            raise MediaFileError("video_read_failed")
        return value
    except MediaFileError:
        raise
    except OSError:
        raise MediaFileError("video_read_failed") from None


def delete_file(root: Path, storage_id: str) -> bool:
    """Delete both temporary and published files; false means a residue remains."""
    _check_sid(storage_id)
    success = True
    for suffix in (".part", ".bin"):
        try:
            path = file_path(root, storage_id, suffix)
            if path.exists():
                path.unlink()
        except (MediaFileError, OSError):
            success = False
    for suffix in (".part", ".bin"):
        try:
            if file_path(root, storage_id, suffix).exists():
                success = False
        except (MediaFileError, OSError):
            success = False
    return success


def inventory(root: Path) -> list[Path]:
    """List only regular files owned by this store; unsafe entries are reported."""
    try:
        _check_path(root)
        entries = list(root.iterdir())
    except OSError:
        raise MediaFileError("video_path_unsafe") from None
    result: list[Path] = []
    for entry in entries:
        _check_path(entry)
        if _is_reparse(entry):
            raise MediaFileError("video_path_unsafe")
        if entry.is_dir():
            raise MediaFileError("video_path_unsafe")
        if not _FILE_RE.fullmatch(entry.name):
            raise MediaFileError("video_path_unsafe")
        result.append(entry)
    return result


def clear_files(root: Path) -> bool:
    """Remove all owned files and return whether the directory is empty."""
    success = True
    try:
        entries = inventory(root)
    except MediaFileError:
        raise
    for entry in entries:
        try:
            entry.unlink()
        except OSError:
            success = False
    try:
        remaining = list(root.iterdir())
    except OSError:
        return False
    return success and not remaining


def disk_usage(root: Path) -> tuple[int, int]:
    try:
        usage = shutil.disk_usage(root)
    except OSError:
        raise MediaFileError("video_disk_full") from None
    return int(usage.free), int(usage.total)


safe_root = safe_wallpaper_root


def try_lock(root: Path):
    """A persistent zero-byte coordination marker, outside user media files."""
    path = root.parent / ".wallpapers.lock"
    handle = _open_file(path, os.O_RDWR | os.O_CREAT, "r+b")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            handle.write(b"\0")
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def unlock(handle) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def process_alive(pid: int) -> bool:
    if pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5
        try:
            code = ctypes.c_ulong()
            return bool(kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
