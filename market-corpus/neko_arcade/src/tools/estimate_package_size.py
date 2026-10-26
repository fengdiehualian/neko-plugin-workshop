"""按官方打包规则估算发布包体积（不真的打包，秒出）。

规则来源：`neko_plugin_cli/core/build_rules.py`
  · 内置排除目录: __pycache__/.github/.vscode/.idea/.pytest_cache/.mypy_cache/
    .ruff_cache/.venv/.git, 根目录 dist/build
  · 内置排除文件: .DS_Store, 后缀 .pyc/.pyo
  · 叠加 pyproject `[tool.neko.build]` 的 exclude_dirs(名字或路径) / exclude_files(支持通配)

用法：
    python tools/estimate_package_size.py            # 只报总量
    python tools/estimate_package_size.py -v         # 附最大的 15 个文件
"""
from __future__ import annotations

import argparse
import fnmatch
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

DEFAULT_DIR_NAMES = {"__pycache__", ".github", ".vscode", ".idea", ".pytest_cache",
                     ".mypy_cache", ".ruff_cache", ".venv", ".git"}
ROOT_DIR_NAMES = {"dist", "build"}
DEFAULT_FILE_NAMES = {".DS_Store"}
DEFAULT_SUFFIXES = {".pyc", ".pyo"}


def load_rules() -> tuple[list[str], list[str]]:
    with (REPO / "pyproject.toml").open("rb") as f:
        cfg = tomllib.load(f)
    build = cfg.get("tool", {}).get("neko", {}).get("build", {}) or {}
    return list(build.get("exclude_dirs", [])), list(build.get("exclude_files", []))


def _match(path_str: str, pattern: str) -> bool:
    """官方 _match_pattern 的同义实现：整路径 fnmatch，或（无斜杠时）按文件名匹配。"""
    if fnmatch.fnmatchcase(path_str, pattern):
        return True
    if "/" not in pattern and fnmatch.fnmatchcase(Path(path_str).name, pattern):
        return True
    return False


def _dir_excluded(dir_parts: tuple[str, ...], patterns: list[str]) -> bool:
    """官方 _matches_excluded_dir 的同义实现：逐级前缀匹配（支持 'a/b/c' 与目录名）。"""
    for index in range(len(dir_parts)):
        candidate = "/".join(dir_parts[:index + 1])
        if any(_match(candidate, p) for p in patterns):
            return True
    return False


def included_files() -> list[Path]:
    excl_dirs, excl_files = load_rules()
    out: list[Path] = []
    for path in sorted(REPO.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(REPO)
        dir_parts = rel.parts[:-1]
        if dir_parts and dir_parts[0] in ROOT_DIR_NAMES:
            continue
        if any(p in DEFAULT_DIR_NAMES for p in dir_parts):
            continue
        if _dir_excluded(dir_parts, excl_dirs):
            continue
        if path.name in DEFAULT_FILE_NAMES or path.suffix in DEFAULT_SUFFIXES:
            continue
        posix = rel.as_posix()
        if path.name in excl_files or any(_match(posix, p) for p in excl_files):
            continue
        out.append(path)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    files = included_files()
    total = sum(p.stat().st_size for p in files)
    print(f"预计发布包: {total / 1024 / 1024:.2f} MB  ({len(files)} 个文件)")
    if args.verbose:
        print("\n最大的 15 个:")
        for p in sorted(files, key=lambda x: x.stat().st_size, reverse=True)[:15]:
            print(f"  {p.stat().st_size / 1024:9.1f} KB  {p.relative_to(REPO).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
