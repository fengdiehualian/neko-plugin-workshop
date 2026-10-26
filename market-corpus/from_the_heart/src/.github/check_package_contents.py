"""Fail closed when a built plugin package contains unsafe cache entries."""

from __future__ import annotations

import argparse
import posixpath
import sys
import zipfile
from pathlib import Path


def _unsafe_entries(package: Path) -> list[str]:
    unsafe: list[str] = []
    with zipfile.ZipFile(package) as archive:
        for info in archive.infolist():
            name = info.filename
            normalized = posixpath.normpath(name)
            raw_parts = [part for part in name.replace("\\", "/").split("/") if part]
            parts = [part for part in normalized.split("/") if part]
            drive_prefix = len(raw_parts) > 0 and len(raw_parts[0]) == 2 and raw_parts[0][1] == ":"
            if (
                name.startswith(("/", "\\"))
                or drive_prefix
                or ".." in raw_parts
                or normalized == ".."
                or normalized.startswith("../")
            ):
                unsafe.append(f"{name} (path traversal)")
                continue
            if "__pycache__" in parts or name.endswith((".pyc", ".pyo")):
                unsafe.append(f"{name} (Python bytecode cache)")
    return unsafe


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    args = parser.parse_args()
    package = args.package
    if not package.is_file():
        print(f"[FAIL] package does not exist: {package}", file=sys.stderr)
        return 2
    try:
        unsafe = _unsafe_entries(package)
    except (OSError, zipfile.BadZipFile) as exc:
        print(f"[FAIL] cannot inspect package {package}: {exc}", file=sys.stderr)
        return 2
    if unsafe:
        print("[FAIL] package contains forbidden entries:", file=sys.stderr)
        for entry in unsafe:
            print(f"  - {entry}", file=sys.stderr)
        return 1
    print(f"[OK] package contains no Python bytecode caches or traversal entries: {package}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
