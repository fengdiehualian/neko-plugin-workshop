"""Packaging configuration must exclude private runtime media, not source."""

from __future__ import annotations

import ast
import tomllib
from fnmatch import fnmatchcase
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIRS = {"data", "wallpapers", "temp", "staging", "backup", "published", "wallpaper_playback"}
PRIVATE_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".mp4", ".webm", ".part", ".bin"}


def _rules() -> dict[str, list[str]]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return project["tool"]["neko"]["build"]


def _matches(path: str, patterns: list[str]) -> bool:
    return any(
        fnmatchcase(path, pattern)
        or ("/" not in pattern and fnmatchcase(Path(path).name, pattern))
        for pattern in patterns
    )


def _excluded(path: str) -> bool:
    rules = _rules()
    parts = Path(path).parts
    for index in range(1, len(parts)):
        if _matches("/".join(parts[:index]), rules["exclude_dirs"]):
            return True
    return _matches(path, rules["exclude_files"])


@pytest.mark.parametrize("directory", sorted(RUNTIME_DIRS))
@pytest.mark.parametrize("filename", ["personal.txt", "manifest.json", "cover.png"])
def test_runtime_directories_are_excluded_regardless_of_file_suffix(
    directory: str, filename: str
) -> None:
    assert _excluded(f"{directory}/{filename}")
    assert _excluded(f"nested/{directory}/{filename}")


@pytest.mark.parametrize(
    "filename",
    [
        "store.db",
        "store.db-wal",
        "store.db-shm",
        "private.sqlite",
        "private.sqlite3",
        "original.mp4",
        "original.webm",
        "upload.part",
        "published.bin",
    ],
)
def test_private_media_and_database_files_are_excluded(filename: str) -> None:
    assert _excluded(filename)
    assert _excluded(f"nested/{filename}")


@pytest.mark.parametrize(
    "source",
    [
        "__init__.py",
        "core/media_files.py",
        "core/media.py",
        "mixins/media.py",
        "plugin.toml",
        "ui/panel.tsx",
        "ui/media.tsx",
        "ui/appearance.tsx",
        "i18n/en.json",
        "i18n/zh-CN.json",
    ],
)
def test_runtime_sources_are_not_excluded(source: str) -> None:
    assert (ROOT / source).is_file()
    assert not _excluded(source)


def test_archive_verifier_checks_private_directories_and_suffixes() -> None:
    source = (ROOT / "tools" / "build_import_package.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    worker = next(
        node for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "build_and_verify"
    )
    forbidden = next(
        node.value for node in ast.walk(worker)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "forbidden" for target in node.targets)
    )
    assert RUNTIME_DIRS <= set(ast.literal_eval(forbidden))
    suffix_sets = [
        set(ast.literal_eval(node))
        for node in ast.walk(worker)
        if isinstance(node, ast.Set)
        and all(isinstance(element, ast.Constant) for element in node.elts)
    ]
    assert any(PRIVATE_SUFFIXES <= values for values in suffix_sets)
    assert "part.casefold() for part in relative.parts" in source
    assert '".db-" not in relative.name.casefold()' in source
