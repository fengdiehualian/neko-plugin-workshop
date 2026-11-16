#!/usr/bin/env python3
"""构建和校验 N.E.K.O 适配版的下游 vendor 源码包。

只使用 Python 标准库。ZIP 的时间戳、权限、顺序与压缩级别固定，同一个源码树
重复构建应得到完全相同的 SHA-256。清单用于完整性校验，不代替签名。
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_NAME = "VENDOR-SHA256SUMS.txt"
ROOT_FILES = (
    "LICENSE",
    "README.md",
    "CHANGELOG.md",
    "__init__.py",
    "config.example.toml",
    "docs/vendor.md",
    "plugin.toml",
    "pyproject.toml",
    "rbb_core.py",
    "scripts/vendor_release.py",
)
INCLUDE_DIRS = ("docs", "skills")
SKIP_DIRS = {".git", ".pytest_cache", ".ruff_cache", "__pycache__", "tests"}
HEX_256 = re.compile(r"[0-9a-f]{64}")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def plugin_version(root: Path = ROOT) -> str:
    with (root / "plugin.toml").open("rb") as fh:
        plugin = tomllib.load(fh)
    with (root / "pyproject.toml").open("rb") as fh:
        project = tomllib.load(fh)
    version = str(plugin["plugin"]["version"])
    if version != str(project["project"]["version"]):
        raise ValueError("plugin.toml 与 pyproject.toml 的版本不一致")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError(f"插件版本不是 x.y.z：{version!r}")
    return version


def vendor_files(root: Path = ROOT) -> list[str]:
    """返回明确的运行文件集合，不把 .git、CI、测试或缓存带进下游。"""
    paths: list[Path] = []
    for name in ROOT_FILES:
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"vendor 必需文件缺失或不是普通文件：{name}")
        paths.append(path)
    for directory in INCLUDE_DIRS:
        base = root / directory
        if not base.is_dir():
            raise ValueError(f"vendor 必需目录缺失：{directory}")
        for path in base.rglob("*"):
            rel = path.relative_to(root)
            if any(part in SKIP_DIRS for part in rel.parts):
                continue
            if path.is_symlink():
                raise ValueError(f"vendor 不接受符号链接：{rel.as_posix()}")
            if path.is_file():
                if path.suffix in {".pyc", ".pyo"}:
                    continue
                paths.append(path)
    files = sorted({path.relative_to(root).as_posix() for path in paths})
    for name in files:
        _safe_relative(name)
    if not files:
        raise ValueError("vendor 文件集合为空")
    return files


def _safe_relative(name: str) -> PurePosixPath:
    """清单路径必须是无盘符、无反斜杠、无 .. 的 POSIX 相对路径。"""
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or path.is_absolute()
        or re.match(r"^[A-Za-z]:", name)
        or any(part in {"", ".", ".."} for part in name.split("/"))
    ):
        raise ValueError(f"非法清单路径：{name!r}")
    return path


def _parse_checksums(text: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in text.splitlines():
        if not line:
            continue
        digest, separator, name = line.partition("  ")
        if not separator or not HEX_256.fullmatch(digest):
            raise ValueError(f"非法 SHA-256 清单行：{line!r}")
        _safe_relative(name)
        if name in entries:
            raise ValueError(f"清单路径重复：{name}")
        entries[name] = digest
    if not entries:
        raise ValueError("SHA-256 清单为空")
    return entries


def _manifest(root: Path, files: list[str]) -> str:
    return "".join(f"{sha256_file(root / name)}  {name}\n" for name in files)


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def build_vendor_archive(output_dir: Path, tag: str = "", root: Path = ROOT) -> Path:
    version = plugin_version(root)
    if tag and tag != f"v{version}":
        raise ValueError(f"tag {tag!r} 必须与 plugin.toml 版本 v{version} 完全一致")
    files = vendor_files(root)
    prefix = f"neko-reuse-before-build-v{version}"
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / f"{prefix}-vendor.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.comment = f"1-dcjiang/neko-reuse-before-build {tag or f'v{version}'}".encode("ascii")
        for name in [*files, MANIFEST_NAME]:
            payload = _manifest(root, files).encode("utf-8") if name == MANIFEST_NAME else (root / name).read_bytes()
            bundle.writestr(_zip_info(f"{prefix}/{name}"), payload, compresslevel=9)
    return archive


def _check_release_sum(archive: Path, sums: Path) -> None:
    entries = _parse_checksums(sums.read_text(encoding="utf-8"))
    expected = entries.get(archive.name)
    if expected is None:
        raise ValueError(f"Release 清单没有收录 {archive.name}")
    actual = sha256_file(archive)
    if actual != expected:
        raise ValueError(f"Release ZIP 哈希不匹配：{archive.name}")


def verify_vendor_archive(archive: Path, sums: Path | None = None) -> dict[str, object]:
    archive = archive.resolve()
    if sums is not None:
        _check_release_sum(archive, sums.resolve())
    try:
        with zipfile.ZipFile(archive) as bundle:
            infos = [info for info in bundle.infolist() if not info.is_dir()]
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise ValueError("vendor ZIP 含重复路径")
            for info in infos:
                _safe_relative(info.filename)
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError(f"vendor ZIP 不接受符号链接：{info.filename}")
            manifests = [name for name in names if name.endswith("/" + MANIFEST_NAME)]
            if len(manifests) != 1:
                raise ValueError("vendor ZIP 必须且只能有一份文件级 SHA-256 清单")
            manifest_name = manifests[0]
            prefix = manifest_name[: -len(MANIFEST_NAME)]
            entries = _parse_checksums(bundle.read(manifest_name).decode("utf-8"))
            expected_names = {prefix + name for name in entries}
            actual_names = set(names) - {manifest_name}
            if actual_names != expected_names:
                missing = sorted(expected_names - actual_names)
                extra = sorted(actual_names - expected_names)
                raise ValueError(f"vendor ZIP 文件集合与清单不符：missing={missing}, extra={extra}")
            for name, expected in entries.items():
                actual = sha256_bytes(bundle.read(prefix + name))
                if actual != expected:
                    raise ValueError(f"vendor 文件哈希不匹配：{name}")
            with bundle.open(prefix + "plugin.toml") as fh:
                version = str(tomllib.load(fh)["plugin"]["version"])
            if prefix != f"neko-reuse-before-build-v{version}/":
                raise ValueError("vendor ZIP 根目录与包内版本不一致")
    except zipfile.BadZipFile as exc:
        raise ValueError(f"不是有效的 vendor ZIP：{archive.name}") from exc
    return {"archive": archive.name, "files": len(entries), "sha256": sha256_file(archive), "version": version}


def verify_vendor_tree(root: Path) -> int:
    """复制到下游后逐文件复检；允许下游额外存在配置和运行数据。"""
    root = root.resolve()
    entries = _parse_checksums((root / MANIFEST_NAME).read_text(encoding="utf-8"))
    for name, expected in entries.items():
        path = root.joinpath(*_safe_relative(name).parts)
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"vendor 文件缺失或不是普通文件：{name}")
        if sha256_file(path) != expected:
            raise ValueError(f"vendor 文件哈希不匹配：{name}")
    return len(entries)


def write_release_sums(asset_dir: Path, output: Path) -> list[str]:
    """为 Release 附件生成统一清单；清单自身不递归纳入。"""
    asset_dir = asset_dir.resolve()
    output = output.resolve()
    if output.parent != asset_dir:
        raise ValueError("SHA256SUMS.txt 必须与附件位于同一目录")
    files = sorted(
        (path for path in asset_dir.iterdir() if path.is_file() and path.name != output.name),
        key=lambda path: path.name,
    )
    if not files:
        raise ValueError("附件目录为空")
    output.write_text("".join(f"{sha256_file(path)}  {path.name}\n" for path in files), encoding="utf-8", newline="\n")
    return [path.name for path in files]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="按 plugin.toml 版本构建可复现 vendor ZIP")
    build.add_argument("--tag", default="", help="可选发布 tag；提供时必须为 v<版本>")
    build.add_argument("--output-dir", type=Path, default=Path("dist"))
    verify_archive = commands.add_parser("verify-archive", help="校验 ZIP 文件集合与文件级哈希")
    verify_archive.add_argument("archive", type=Path)
    verify_archive.add_argument("--sums", type=Path, help="同时核对 Release SHA256SUMS.txt")
    verify_tree = commands.add_parser("verify-tree", help="校验复制到下游后的文件")
    verify_tree.add_argument("root", type=Path)
    sums = commands.add_parser("sums", help="为一个目录内的 Release 附件生成 SHA256SUMS.txt")
    sums.add_argument("asset_dir", type=Path)
    sums.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            archive = build_vendor_archive(args.output_dir, args.tag)
            print(f"PASS build archive={archive} sha256={sha256_file(archive)}")
        elif args.command == "verify-archive":
            result = verify_vendor_archive(args.archive, args.sums)
            print("PASS verify-archive " + " ".join(f"{key}={value}" for key, value in result.items()))
        elif args.command == "verify-tree":
            count = verify_vendor_tree(args.root)
            print(f"PASS verify-tree root={args.root} files={count}")
        else:
            names = write_release_sums(args.asset_dir, args.output)
            print(f"PASS sums output={args.output} assets={len(names)}")
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
